from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import tempfile
import time
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4
from zoneinfo import ZoneInfo

from fastapi import APIRouter, BackgroundTasks, Body, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from llama_index.core.readers.file.base import default_file_metadata_func
from sqlalchemy import delete, or_
from sqlmodel import Session, select
from theflow.settings import settings as flowsettings

from ktem.db.engine import engine
from ktem.db.models import Conversation as DbConversation
from ktem.db.models import Settings as DbSettings
from ktem.db.models import User
from ktem.evaluation import ragas_metrics
from ktem.evaluation import EvalMetricInputs, calculate_metrics, store as eval_store
from ktem.index.file.graph.product import ProductGraphIndexingMixin, collection_graph_id
from ktem.permissions import (
    can_read_source,
    filter_source_ids,
    list_source_permissions,
    resolve_principal,
    set_source_acl,
)
from ktem.permissions.models import SourcePermission
from ktem.trace import (
    RagTraceRecorder,
    get_trace,
    get_trace_by_message,
    list_conversation_traces,
    save_trace,
    set_active_recorder,
)
from ktem.react_defaults import (
    DEFAULT_PROMPT_TEMPLATE_NAME,
    DEFAULT_PROMPT_TEMPLATES,
    DEFAULT_PROMPT_TEMPLATE_TEXT,
    DEFAULT_SETTING,
    STATE,
)

CHINA_TZ = ZoneInfo("Asia/Shanghai")
RAG_EVAL_COMPARISON_VARIANTS = (
    "normal_query",
    "rewrite",
    "hyde",
    "fusion",
)
RAG_EVAL_STRATEGY_LABELS = {
    "current": "Current settings",
    "normal_query": "Normal Query",
    "rewrite": "Query Rewrite",
    "hyde": "HyDE",
    "fusion": "RAG-Fusion",
    "vector": "Vector",
    "text": "Full Text",
    "hybrid_rrf": "Hybrid + RRF",
    "hybrid_rrf+rerank": "Hybrid + RRF + Rerank",
    "hybrid_rrf+rerank+mmr": "Hybrid + RRF + Rerank + MMR",
    "graph": "GraphRAG Fusion",
}


def _now() -> str:
    return datetime.now(CHINA_TZ).isoformat()


def _to_iso(value: Any) -> str:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=CHINA_TZ)
        return value.astimezone(CHINA_TZ).isoformat()
    return _now()


def _user_id_from_request(request: Request) -> str | None:
    if not getattr(flowsettings, "KH_FEATURE_USER_MANAGEMENT", False):
        return "default"

    try:
        session = request.session
    except AssertionError:
        session = None
    if session:
        session_user = session.get("user") or {}
        session_user_id = session_user.get("sub") or session_user.get("email")
        if session_user_id:
            return session_user_id

    return "default"


def _require_user_id(request: Request) -> str:
    user_id = _user_id_from_request(request)
    if not user_id:
        raise HTTPException(status_code=401, detail="请先登录后再使用聊天接口。")
    return user_id


def _load_user_settings(user_id: str, app_runtime: Any | None = None) -> dict[str, Any]:
    default_settings = (
        app_runtime.default_settings.flatten() if app_runtime is not None else {}
    )
    with Session(engine) as session:
        statement = select(DbSettings).where(DbSettings.user == user_id)
        result = session.exec(statement).one_or_none()
        if result:
            return result.setting
    return default_settings


def _can_see_public(user_id: str) -> bool:
    with Session(engine) as session:
        statement = select(User).where(User.id == user_id)
        result = session.exec(statement).one_or_none()
        if result is None:
            return True
        public_user = getattr(flowsettings, "KH_USER_CAN_SEE_PUBLIC", None)
        return result.username == public_user if public_user else True


def _conversation_statement(user_id: str):
    if _can_see_public(user_id):
        return (
            select(DbConversation)
            .where(or_(DbConversation.user == user_id, DbConversation.is_public))
            .order_by(DbConversation.is_public.desc(), DbConversation.date_created.desc())  # type: ignore[attr-defined]
        )
    return (
        select(DbConversation)
        .where(DbConversation.user == user_id)
        .order_by(DbConversation.date_created.desc())  # type: ignore[attr-defined]
    )


def _get_conversation_or_404(conversation_id: str, user_id: str) -> DbConversation:
    with Session(engine) as session:
        statement = select(DbConversation).where(DbConversation.id == conversation_id)
        conversation = session.exec(statement).one_or_none()
        if conversation is None:
            raise HTTPException(status_code=404, detail="会话不存在。")
        if conversation.user != user_id and not conversation.is_public:
            raise HTTPException(status_code=403, detail="无权访问该会话。")
        return conversation


def _message_count(data_source: dict[str, Any]) -> int:
    return len(data_source.get("messages", []) or []) * 2


def _html_to_text(value: str) -> str:
    text = re.sub(r"<br\s*/?>", "\n", value or "", flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _reference_html_only(value: str) -> str:
    """Keep only citation detail blocks; diagnostics/errors are not references."""
    if not value:
        return ""
    blocks = re.findall(r"<details\b.*?</details>", value, flags=re.IGNORECASE | re.S)
    return "".join(blocks)


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on"}
    return bool(value)


def _normalize_retrieval_enhancement(
    value: Any,
) -> Literal["none", "rewrite", "hyde", "fusion"]:
    normalized = str(value or "none").strip().lower()
    if normalized in {"rewrite", "hyde", "fusion"}:
        return normalized  # type: ignore[return-value]
    return "none"


class Citation(BaseModel):
    id: str
    documentId: str
    title: str
    excerpt: str
    page: int | None = None
    score: float = 0.0
    highlight: str = ""


class ReferenceDocument(BaseModel):
    id: str
    title: str
    source: str
    summary: str
    updatedAt: str
    permission: str = "read"
    citations: list[Citation] = Field(default_factory=list)


class FileItem(BaseModel):
    id: str
    name: str
    source: str
    summary: str
    updatedAt: str
    size: int = 0
    directoryId: str | None = None
    selected: bool = False
    permission: str = "read"


class FileChunk(BaseModel):
    id: str
    index: int
    type: str = "text"
    text: str
    pageLabel: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class SourcePermissionItem(BaseModel):
    principalType: str
    principalId: str
    permission: str


class FileDetail(BaseModel):
    file: FileItem
    chunkCount: int
    chunkTypeCounts: dict[str, int] = Field(default_factory=dict)
    chunks: list[FileChunk] = Field(default_factory=list)
    permissions: list[SourcePermissionItem] = Field(default_factory=list)


class FileDirectory(BaseModel):
    id: str
    name: str
    updatedAt: str
    fileIds: list[str] = Field(default_factory=list)


class FileWorkspaceState(BaseModel):
    directories: list[FileDirectory]
    files: list[FileItem]


class UpdateFilePermissionsPayload(BaseModel):
    permissions: list[SourcePermissionItem] = Field(default_factory=list)


class Conversation(BaseModel):
    id: str
    title: str
    updatedAt: str
    messageCount: int
    pinned: bool = False


class ChatMessage(BaseModel):
    id: str
    conversationId: str
    role: Literal["user", "assistant", "system"]
    content: str
    createdAt: str
    status: Literal["sent", "loading", "streaming", "error"] = "sent"
    citations: list[Citation] = Field(default_factory=list)


class RagTraceSummary(BaseModel):
    traceId: str
    conversationId: str
    messageId: str | None = None
    turnIndex: int | None = None
    userId: str
    question: str
    status: str
    createdAt: str
    durationMs: int = 0


class RagTraceDetail(BaseModel):
    traceId: str
    conversationId: str
    messageId: str | None = None
    turnIndex: int | None = None
    userId: str
    question: str
    status: str
    createdAt: str
    durationMs: int = 0
    data: dict[str, Any] = Field(default_factory=dict)


class RetrievalSettings(BaseModel):
    topK: int = 10
    firstRoundMultiplier: int = 10
    retrievalMode: str = "hybrid"
    enhancement: Literal["none", "rewrite", "hyde", "fusion"] = "none"
    rerank: bool = True
    llmRerank: bool = False
    mmr: bool = False
    prioritizeTable: bool = False
    graphEnabled: bool | None = None
    graphProvider: Literal["lightrag", "nano"] | None = None
    graphSearchType: Literal["local", "global", "hybrid"] | None = None


class SelectOption(BaseModel):
    label: str
    value: str


class ModelProviderConfig(BaseModel):
    name: str = ""
    baseUrl: str = ""
    model: str = ""
    apiKey: str = ""
    timeout: int | None = Field(default=None, ge=1, le=600)
    isDefault: bool = True
    hasApiKey: bool = False


class RerankServiceConfig(BaseModel):
    enabled: bool = True
    provider: str = "default"
    model: str = ""
    baseUrl: str = ""
    apiKey: str = ""
    timeout: int | None = Field(default=30, ge=1, le=600)
    hasApiKey: bool = False


class GraphServiceConfig(BaseModel):
    enabled: bool = False
    provider: Literal["lightrag", "nano"] | str = "lightrag"
    searchType: Literal["local", "global", "hybrid"] | str = "local"
    batchSize: int = Field(default=5, ge=1, le=100)


class FileProcessingServiceConfig(BaseModel):
    readerMode: str = "default"
    ocrProvider: str = "default"
    chunkSize: int = Field(default=1024, ge=1, le=50000)
    chunkOverlap: int = Field(default=256, ge=0, le=50000)
    tableExtraction: bool = True


class SecurityAuditServiceConfig(BaseModel):
    enabled: bool = True
    logRetentionDays: int = Field(default=180, ge=1, le=3650)
    auditFrequency: str = "monthly"
    maskSecrets: bool = True


class ServiceConfigs(BaseModel):
    rerank: RerankServiceConfig = Field(default_factory=RerankServiceConfig)
    graph: GraphServiceConfig = Field(default_factory=GraphServiceConfig)
    fileProcessing: FileProcessingServiceConfig = Field(
        default_factory=FileProcessingServiceConfig
    )
    securityAudit: SecurityAuditServiceConfig = Field(
        default_factory=SecurityAuditServiceConfig
    )


def _sync_graph_service_config(settings: "ChatSettings") -> GraphServiceConfig:
    graph = settings.serviceConfigs.graph
    if settings.retrieval.graphEnabled is not None:
        graph.enabled = settings.retrieval.graphEnabled
    if settings.retrieval.graphProvider:
        graph.provider = settings.retrieval.graphProvider
    if settings.retrieval.graphSearchType:
        graph.searchType = settings.retrieval.graphSearchType
    settings.retrieval.graphEnabled = None
    settings.retrieval.graphProvider = None
    settings.retrieval.graphSearchType = None
    return graph


class ChatSettings(BaseModel):
    suggestedChat: bool = True
    reasoningMethod: str = "simple"
    model: str = ""
    embeddingModel: str = ""
    modelConfig: ModelProviderConfig = Field(default_factory=ModelProviderConfig)
    embeddingConfig: ModelProviderConfig = Field(default_factory=ModelProviderConfig)
    modelConfigs: dict[str, ModelProviderConfig] = Field(default_factory=dict)
    embeddingConfigs: dict[str, ModelProviderConfig] = Field(default_factory=dict)
    settingError: str | None = None
    serviceConfigs: ServiceConfigs = Field(default_factory=ServiceConfigs)
    language: str = "zh"
    citationHighlight: str = "highlight"
    mindmap: bool = True
    promptTemplate: str = DEFAULT_PROMPT_TEMPLATE_NAME
    promptTemplateText: str = DEFAULT_PROMPT_TEMPLATE_TEXT
    promptTemplates: dict[str, str] = Field(default_factory=dict)
    retrieval: RetrievalSettings = Field(default_factory=RetrievalSettings)
    options: dict[str, list[SelectOption]] = Field(default_factory=dict)


class SendMessagePayload(BaseModel):
    conversationId: str
    content: str
    settings: ChatSettings
    selectedFileIds: list[str] = Field(default_factory=list)


class SendMessageResult(BaseModel):
    message: ChatMessage
    references: list[ReferenceDocument]
    trace: RagTraceSummary | None = None


class RagEvalDatasetPayload(BaseModel):
    name: str
    description: str = ""
    tags: list[str] = Field(default_factory=list)


class RagEvalDatasetItem(BaseModel):
    id: str
    name: str
    description: str = ""
    tags: list[str] = Field(default_factory=list)
    exampleCount: int = 0
    runCount: int = 0
    permissionLeakCount: int = 0
    permissionLeakTotal: int = 0
    permissionLeakRate: float | None = None
    latestRunStatus: str | None = None
    createdAt: str
    updatedAt: str


class RagEvalExamplePayload(BaseModel):
    question: str
    expectedAnswer: str | None = None
    expectedSourceIds: list[str] = Field(default_factory=list)
    expectedKeywords: list[str] = Field(default_factory=list)
    evaluatorUserId: str | None = None
    selectedFileIds: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)


class RagEvalRunPayload(BaseModel):
    selectedFileIds: list[str] | None = None
    strategies: list[str] = Field(default_factory=list)
    experimentTag: str | None = None


class RagEvalExampleItem(BaseModel):
    id: str
    datasetId: str
    question: str
    expectedAnswer: str | None = None
    expectedSourceIds: list[str] = Field(default_factory=list)
    expectedKeywords: list[str] = Field(default_factory=list)
    evaluatorUserId: str
    selectedFileIds: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    createdAt: str
    updatedAt: str


class RagEvalRunItem(BaseModel):
    id: str
    datasetId: str
    exampleId: str | None = None
    evaluatorUserId: str
    status: str
    question: str
    answer: str = ""
    metrics: dict[str, Any] = Field(default_factory=dict)
    settingsSnapshot: dict[str, Any] = Field(default_factory=dict)
    traceId: str | None = None
    error: str | None = None
    createdAt: str
    updatedAt: str


class RagEvalRunDetail(RagEvalRunItem):
    references: list[dict[str, Any]] = Field(default_factory=list)
    settingsSnapshot: dict[str, Any] = Field(default_factory=dict)
    trace: RagTraceDetail | None = None


class RagPipelineRunResult(BaseModel):
    answerText: str
    formattedAnswer: str
    retrievalContent: str
    references: list[ReferenceDocument]
    citations: list[Citation] = Field(default_factory=list)
    trace: RagTraceSummary | None = None
    traceData: dict[str, Any] = Field(default_factory=dict)
    messageId: str
    turnIndex: int = 0


class RenameConversationPayload(BaseModel):
    title: str


class CreateDirectoryPayload(BaseModel):
    name: str
    fileIds: list[str] = Field(default_factory=list)


class UpdateDirectoryPayload(BaseModel):
    name: str | None = None
    fileIds: list[str] | None = None


class MoveFilesPayload(BaseModel):
    fileIds: list[str]
    directoryId: str | None = None


class UploadIndexingOptions(BaseModel):
    chunkSize: int | None = Field(default=None, ge=1, le=50000)
    chunkOverlap: int | None = Field(default=None, ge=0, le=50000)
    embeddingModel: str | None = None
    reindex: bool = False


class ReactApiService:
    def __init__(self) -> None:
        self.app_runtime: Any | None = None

    def configure(self, app_runtime: Any | None) -> None:
        self.app_runtime = app_runtime

    @property
    def chat_page(self):
        if self.app_runtime is None or not hasattr(self.app_runtime, "chat_page"):
            raise HTTPException(
                status_code=503,
                detail="React 聊天接口需要通过 register_react_api(app, runtime) 注入应用运行态。",
            )
        return self.app_runtime.chat_page

    def _conversation_to_api(self, conversation: DbConversation) -> Conversation:
        return Conversation(
            id=conversation.id,
            title=conversation.name,
            updatedAt=_to_iso(conversation.date_updated or conversation.date_created),
            messageCount=_message_count(conversation.data_source or {}),
            pinned=bool(conversation.is_public),
        )

    def _trace_summary_to_api(self, trace: Any) -> RagTraceSummary:
        data = trace.data or {}
        return RagTraceSummary(
            traceId=str(trace.trace_id),
            conversationId=str(trace.conversation_id),
            messageId=trace.message_id,
            turnIndex=trace.turn_index,
            userId=str(trace.user_id),
            question=str(trace.question),
            status=str(trace.status),
            createdAt=_to_iso(trace.date_created),
            durationMs=int((data.get("durations_ms") or {}).get("total") or 0),
        )

    def _trace_detail_to_api(self, trace: Any) -> RagTraceDetail:
        data = trace.data or {}
        return RagTraceDetail(
            traceId=str(trace.trace_id),
            conversationId=str(trace.conversation_id),
            messageId=trace.message_id,
            turnIndex=trace.turn_index,
            userId=str(trace.user_id),
            question=str(trace.question),
            status=str(trace.status),
            createdAt=_to_iso(trace.date_created),
            durationMs=int((data.get("durations_ms") or {}).get("total") or 0),
            data=data,
        )

    def _eval_dataset_to_api(self, dataset: Any) -> RagEvalDatasetItem:
        examples = eval_store.list_examples(dataset.id, dataset.owner_user_id)
        runs = eval_store.list_runs(
            owner_user_id=dataset.owner_user_id,
            dataset_id=dataset.id,
            limit=1,
        )
        all_runs = eval_store.list_runs(
            owner_user_id=dataset.owner_user_id,
            dataset_id=dataset.id,
            limit=1000,
        )
        completed_runs = [run for run in all_runs if run.status == "completed"]
        leak_count = sum(
            1
            for run in completed_runs
            if bool((run.metrics or {}).get("acl_leak_detected"))
        )
        leak_total = len(completed_runs)
        return RagEvalDatasetItem(
            id=str(dataset.id),
            name=str(dataset.name),
            description=str(dataset.description or ""),
            tags=list(dataset.tags or []),
            exampleCount=len(examples),
            runCount=len(all_runs),
            permissionLeakCount=leak_count,
            permissionLeakTotal=leak_total,
            permissionLeakRate=(leak_count / leak_total if leak_total else None),
            latestRunStatus=runs[0].status if runs else None,
            createdAt=_to_iso(dataset.date_created),
            updatedAt=_to_iso(dataset.date_updated),
        )

    def _eval_example_to_api(self, example: Any) -> RagEvalExampleItem:
        return RagEvalExampleItem(
            id=str(example.id),
            datasetId=str(example.dataset_id),
            question=str(example.question),
            expectedAnswer=example.expected_answer,
            expectedSourceIds=list(example.expected_source_ids or []),
            expectedKeywords=list(example.expected_keywords or []),
            evaluatorUserId=str(example.evaluator_user_id),
            selectedFileIds=list(example.selected_file_ids or []),
            tags=list(example.tags or []),
            createdAt=_to_iso(example.date_created),
            updatedAt=_to_iso(example.date_updated),
        )

    def _eval_run_metrics_to_api(
        self, run: Any, trace_data: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        metrics = dict(run.metrics or {})
        if not run.example_id:
            return metrics

        example = eval_store.get_example(str(run.example_id), str(run.owner_user_id))
        if example is None:
            return metrics
        if trace_data is None and run.trace_id:
            trace = get_trace(run.trace_id, run.evaluator_user_id)
            trace_data = trace.data if trace is not None else {}
        if not trace_data:
            return metrics

        refreshed_metrics = calculate_metrics(
            EvalMetricInputs(
                answer=str(run.answer or ""),
                references=list(run.references or []),
                trace_data=trace_data or {},
                expected_source_ids=list(example.expected_source_ids or []),
                expected_keywords=list(example.expected_keywords or []),
                error=run.error,
                tags=list(example.tags or []),
            ),
            acl_leak_detected=bool(metrics.get("acl_leak_detected")),
        )
        refreshed_metrics.update(
            {key: value for key, value in metrics.items() if key.startswith("ragas_")}
        )
        if metrics.get("evaluation_variant"):
            refreshed_metrics["evaluation_variant"] = metrics["evaluation_variant"]
        if metrics.get("strategy_label"):
            refreshed_metrics["strategy_label"] = metrics["strategy_label"]
        if metrics.get("experiment_tag"):
            refreshed_metrics["experiment_tag"] = metrics["experiment_tag"]
        return refreshed_metrics

    def _settings_for_eval_variant(
        self,
        settings: ChatSettings,
        variant: str,
    ) -> ChatSettings:
        next_settings = settings.model_copy(deep=True)
        next_settings.retrieval.llmRerank = False
        if variant == "normal_query":
            next_settings.retrieval.enhancement = "none"
            return next_settings
        if variant == "rewrite":
            next_settings.retrieval.enhancement = "rewrite"
            return next_settings
        if variant == "hyde":
            next_settings.retrieval.enhancement = "hyde"
            return next_settings
        if variant == "fusion":
            next_settings.retrieval.enhancement = "fusion"
            next_settings.retrieval.retrievalMode = "hybrid"
            return next_settings
        if variant == "graph":
            next_settings.serviceConfigs.graph.enabled = True
            next_settings.serviceConfigs.graph.provider = "lightrag"
            next_settings.serviceConfigs.graph.searchType = "local"
            return next_settings
        if variant == "current":
            return next_settings
        if variant == "vector":
            next_settings.retrieval.retrievalMode = "vector"
            next_settings.retrieval.rerank = False
            next_settings.retrieval.mmr = False
        elif variant == "text":
            next_settings.retrieval.retrievalMode = "text"
            next_settings.retrieval.rerank = False
            next_settings.retrieval.mmr = False
        elif variant == "hybrid_rrf":
            next_settings.retrieval.retrievalMode = "hybrid"
            next_settings.retrieval.rerank = False
            next_settings.retrieval.mmr = False
        elif variant == "hybrid_rrf+rerank":
            next_settings.retrieval.retrievalMode = "hybrid"
            next_settings.retrieval.rerank = True
            next_settings.retrieval.mmr = False
        elif variant == "hybrid_rrf+rerank+mmr":
            next_settings.retrieval.retrievalMode = "hybrid"
            next_settings.retrieval.rerank = True
            next_settings.retrieval.mmr = True
        else:
            raise ValueError(f"Unknown eval comparison variant: {variant}")
        return next_settings

    def _normalize_eval_strategies(
        self,
        strategies: list[str] | None,
        *,
        default: list[str] | None = None,
    ) -> list[str]:
        allowed = set(RAG_EVAL_STRATEGY_LABELS)
        output: list[str] = []
        for strategy in strategies or []:
            normalized = str(strategy).strip()
            if not normalized:
                continue
            if normalized not in allowed:
                raise HTTPException(
                    status_code=400,
                    detail=f"未知评测策略：{normalized}",
                )
            if normalized not in output:
                output.append(normalized)
        if output:
            return output
        return list(default or ["current"])

    def _strategy_snapshot(
        self,
        settings: ChatSettings,
        *,
        strategy: str,
        experiment_tag: str | None = None,
    ) -> dict[str, Any]:
        graph_config = _sync_graph_service_config(settings)
        retrieval = settings.retrieval.model_dump(exclude_none=True)
        retrieval_mode = str(retrieval.get("retrievalMode") or "hybrid")
        enhancement = str(retrieval.get("enhancement") or "none")
        snapshot = settings.model_dump(exclude_none=True)
        snapshot.update(
            {
                "evaluation_variant": strategy,
                "strategy_id": strategy,
                "strategy_label": RAG_EVAL_STRATEGY_LABELS.get(strategy, strategy),
                "experiment_tag": experiment_tag,
                "retrieval_strategy": {
                    "retrieval_mode": retrieval_mode,
                    "enhancement": enhancement,
                    "topK": retrieval.get("topK"),
                    "firstRoundMultiplier": retrieval.get("firstRoundMultiplier"),
                    "rerank": bool(retrieval.get("rerank")),
                    "llm_rerank": bool(retrieval.get("llmRerank")),
                    "mmr": bool(retrieval.get("mmr")),
                    "prioritize_table": bool(retrieval.get("prioritizeTable")),
                    "graph_rag": {
                        "enabled": bool(graph_config.enabled),
                        "provider": graph_config.provider or "lightrag",
                        "search_type": graph_config.searchType or "local",
                        "implemented": True,
                    },
                    "rrf": {
                        "enabled": retrieval_mode == "hybrid",
                        "k": 60 if retrieval_mode == "hybrid" else None,
                    },
                    "query_rewrite": {
                        "enabled": enhancement == "rewrite",
                        "implemented": True,
                    },
                    "hyde": {
                        "enabled": enhancement == "hyde",
                        "implemented": True,
                    },
                    "rag_fusion": {
                        "enabled": enhancement == "fusion",
                        "implemented": True,
                        "query_count": 4 if enhancement == "fusion" else 0,
                    },
                },
            }
        )
        return snapshot

    def _settings_snapshot(
        self,
        settings: ChatSettings,
        *,
        variant: str | None = None,
    ) -> dict[str, Any]:
        return self._strategy_snapshot(settings, strategy=variant or "current")

    def _ragas_eval_enabled(self) -> bool:
        return _as_bool(
            getattr(flowsettings, "KH_RAGAS_EVAL_ENABLED", False)
        ) or _as_bool(os.environ.get("KH_RAGAS_EVAL_ENABLED", ""))

    def _append_ragas_metrics(
        self,
        metrics: dict[str, Any],
        *,
        question: str,
        answer: str,
        trace_data: dict[str, Any] | None,
        expected_answer: str | None,
    ) -> dict[str, Any]:
        for name in ragas_metrics.RAGAS_METRIC_NAMES:
            metrics.setdefault(f"ragas_{name}", None)

        if not self._ragas_eval_enabled():
            metrics["ragas_enabled"] = False
            return metrics

        try:
            contexts = ragas_metrics.extract_ragas_contexts(trace_data)
            ragas_result = ragas_metrics.calculate_ragas_metrics(
                question=question,
                answer=answer,
                contexts=contexts,
                ground_truth=expected_answer,
            )
            for name in ragas_metrics.RAGAS_METRIC_NAMES:
                metrics[f"ragas_{name}"] = ragas_result.get(name)
            metrics["ragas_enabled"] = bool(ragas_result.get("ragas_enabled"))
            if ragas_result.get("ragas_error"):
                metrics["ragas_error"] = ragas_result["ragas_error"]
        except Exception as exc:
            metrics["ragas_enabled"] = False
            metrics["ragas_error"] = str(exc)
        return metrics

    def _eval_run_to_api(self, run: Any) -> RagEvalRunItem:
        return RagEvalRunItem(
            id=str(run.id),
            datasetId=str(run.dataset_id),
            exampleId=run.example_id,
            evaluatorUserId=str(run.evaluator_user_id),
            status=str(run.status),
            question=str(run.question),
            answer=str(run.answer or ""),
            metrics=self._eval_run_metrics_to_api(run),
            settingsSnapshot=dict(run.settings_snapshot or {}),
            traceId=run.trace_id,
            error=run.error,
            createdAt=_to_iso(run.date_created),
            updatedAt=_to_iso(run.date_updated),
        )

    def _eval_run_detail_to_api(self, run: Any) -> RagEvalRunDetail:
        trace_detail = None
        if run.trace_id:
            trace = get_trace(run.trace_id, run.evaluator_user_id)
            if trace is not None:
                trace_detail = self._trace_detail_to_api(trace)
        base = self._eval_run_to_api(run).model_dump()
        if trace_detail is not None:
            base["metrics"] = self._eval_run_metrics_to_api(run, trace_detail.data)
        base["settingsSnapshot"] = dict(run.settings_snapshot or {})
        return RagEvalRunDetail(
            **base,
            references=list(run.references or []),
            trace=trace_detail,
        )

    def list_conversations(self, user_id: str) -> list[Conversation]:
        with Session(engine) as session:
            results = session.exec(_conversation_statement(user_id)).all()
            return [self._conversation_to_api(result) for result in results]

    def create_conversation(self, user_id: str) -> Conversation:
        with Session(engine) as session:
            conversation = DbConversation(user=user_id)
            session.add(conversation)
            session.commit()
            session.refresh(conversation)
            return self._conversation_to_api(conversation)

    def delete_conversation(self, conversation_id: str, user_id: str) -> None:
        with Session(engine) as session:
            statement = select(DbConversation).where(
                DbConversation.id == conversation_id
            )
            conversation = session.exec(statement).one_or_none()
            if conversation is None:
                return
            if conversation.user != user_id:
                raise HTTPException(status_code=403, detail="只能删除自己的会话。")
            session.delete(conversation)
            session.commit()

    def rename_conversation(
        self, conversation_id: str, title: str, user_id: str
    ) -> Conversation:
        title = title.strip()
        if not title:
            raise HTTPException(status_code=400, detail="名称不能为空。")
        if len(title) > 40:
            raise HTTPException(status_code=400, detail="名称不能超过 40 个字符。")

        with Session(engine) as session:
            statement = select(DbConversation).where(
                DbConversation.id == conversation_id
            )
            conversation = session.exec(statement).one_or_none()
            if conversation is None:
                raise HTTPException(status_code=404, detail="会话不存在。")
            if conversation.user != user_id:
                raise HTTPException(status_code=403, detail="只能重命名自己的会话。")
            conversation.name = title
            conversation.date_updated = datetime.now(CHINA_TZ)
            session.add(conversation)
            session.commit()
            session.refresh(conversation)
            return self._conversation_to_api(conversation)

    def list_messages(self, conversation_id: str, user_id: str) -> list[ChatMessage]:
        conversation = _get_conversation_or_404(conversation_id, user_id)
        rows = (
            conversation.data_source.get("messages", [])
            if conversation.data_source
            else []
        )
        retrieval_history = (
            conversation.data_source.get("retrieval_messages", [])
            if conversation.data_source
            else []
        )
        messages: list[ChatMessage] = []
        base_time = conversation.date_created or datetime.now(CHINA_TZ)
        for index, row in enumerate(rows):
            if not isinstance(row, (list, tuple)):
                continue
            user_content, assistant_content = (list(row) + [None, None])[:2]
            created_at = _to_iso(base_time)
            if user_content:
                messages.append(
                    ChatMessage(
                        id=f"{conversation_id}-{index}-user",
                        conversationId=conversation_id,
                        role="user",
                        content=_html_to_text(str(user_content)),
                        createdAt=created_at,
                    )
                )
            if assistant_content:
                references = self._references_from_retrieval(
                    [retrieval_history[index]] if index < len(retrieval_history) else []
                )
                trace = get_trace_by_message(
                    f"{conversation_id}-{index}-assistant",
                    user_id,
                )
                if trace is not None:
                    graph_reference = self._graph_reference_from_trace(trace.data or {})
                    if graph_reference is not None:
                        references = [graph_reference, *references]
                message_citations = [
                    citation
                    for reference in references
                    for citation in reference.citations
                ]
                messages.append(
                    ChatMessage(
                        id=f"{conversation_id}-{index}-assistant",
                        conversationId=conversation_id,
                        role="assistant",
                        content=_html_to_text(str(assistant_content)),
                        createdAt=created_at,
                        citations=message_citations,
                    )
                )
        return messages

    def list_traces(self, conversation_id: str, user_id: str) -> list[RagTraceSummary]:
        _get_conversation_or_404(conversation_id, user_id)
        return [
            self._trace_summary_to_api(trace)
            for trace in list_conversation_traces(conversation_id, user_id)
        ]

    def get_trace_detail(self, trace_id: str, user_id: str) -> RagTraceDetail:
        trace = get_trace(trace_id, user_id)
        if trace is None:
            raise HTTPException(status_code=404, detail="Trace 不存在。")
        _get_conversation_or_404(trace.conversation_id, user_id)
        return self._trace_detail_to_api(trace)

    def get_message_trace(self, message_id: str, user_id: str) -> RagTraceDetail | None:
        trace = get_trace_by_message(message_id, user_id)
        if trace is None:
            return None
        _get_conversation_or_404(trace.conversation_id, user_id)
        return self._trace_detail_to_api(trace)

    def get_message_references(
        self, message_id: str, user_id: str
    ) -> list[ReferenceDocument]:
        trace = get_trace_by_message(message_id, user_id)
        if trace is None:
            return []
        conversation = _get_conversation_or_404(trace.conversation_id, user_id)
        references: list[ReferenceDocument] = []
        data_source = conversation.data_source or {}
        retrieval_history = data_source.get("retrieval_messages", [])
        if (
            trace.turn_index is not None
            and isinstance(retrieval_history, list)
            and trace.turn_index < len(retrieval_history)
        ):
            references = self._references_from_retrieval(
                [retrieval_history[trace.turn_index]]
            )
        graph_reference = self._graph_reference_from_trace(trace.data or {})
        if graph_reference is not None:
            references = [graph_reference, *references]
        return references

    def _option_values(self, values: list[Any] | tuple[Any, ...]) -> list[SelectOption]:
        output: list[SelectOption] = []
        for item in values:
            if isinstance(item, (list, tuple)) and len(item) >= 2:
                output.append(SelectOption(label=str(item[0]), value=str(item[1])))
            else:
                output.append(SelectOption(label=str(item), value=str(item)))
        return output

    @staticmethod
    def _model_config_from_info(
        info: dict[str, Any],
        fallback_name: str,
        fallback_timeout: int,
    ) -> ModelProviderConfig:
        spec = info.get("spec") if isinstance(info, dict) else {}
        spec = spec if isinstance(spec, dict) else {}
        return ModelProviderConfig(
            name=str(info.get("name") or fallback_name) if isinstance(info, dict) else fallback_name,
            baseUrl=str(spec.get("base_url") or ""),
            model=str(spec.get("model") or ""),
            apiKey="",
            timeout=int(spec.get("timeout") or fallback_timeout),
            isDefault=bool(info.get("default", False)) if isinstance(info, dict) else True,
            hasApiKey=bool(spec.get("api_key")),
        )

    @staticmethod
    def _openai_compatible_spec(
        model_type: str,
        config: ModelProviderConfig,
        existing: dict[str, Any] | None = None,
        fallback_api_key: str = "",
        fallback_timeout: int = 60,
    ) -> dict[str, Any]:
        existing = existing or {}
        api_key = config.apiKey or str(existing.get("api_key") or fallback_api_key)
        spec = {
            "__type__": model_type,
            "base_url": config.baseUrl.strip(),
            "model": config.model.strip(),
            "api_key": api_key,
            "timeout": config.timeout or fallback_timeout,
        }
        return {key: value for key, value in spec.items() if value not in ("", None)}

    @staticmethod
    def _empty_model_config(fallback_timeout: int) -> ModelProviderConfig:
        return ModelProviderConfig(timeout=fallback_timeout, isDefault=False)

    def _llm_config(self, selected_name: str) -> ModelProviderConfig:
        try:
            from ktem.llms.manager import llms

            info = llms.info()
            name = selected_name or llms.get_default_name()
            return self._model_config_from_info(
                info.get(name, {}), name, fallback_timeout=60
            )
        except Exception:
            return ModelProviderConfig(name=selected_name or "deepseek", timeout=60)

    def _llm_configs(self) -> dict[str, ModelProviderConfig]:
        try:
            from ktem.llms.manager import llms

            return {
                name: self._model_config_from_info(info, name, fallback_timeout=60)
                for name, info in llms.info().items()
            }
        except Exception:
            return {}

    def _embedding_config(self, selected_name: str) -> ModelProviderConfig:
        try:
            from ktem.embeddings.manager import embedding_models_manager

            info = embedding_models_manager.info()
            name = selected_name or embedding_models_manager.get_default_name()
            return self._model_config_from_info(
                info.get(name, {}), name, fallback_timeout=30
            )
        except Exception:
            return ModelProviderConfig(name=selected_name or "ollama", timeout=30)

    def _embedding_configs(self) -> dict[str, ModelProviderConfig]:
        try:
            from ktem.embeddings.manager import embedding_models_manager

            return {
                name: self._model_config_from_info(info, name, fallback_timeout=30)
                for name, info in embedding_models_manager.info().items()
            }
        except Exception:
            return {}

    def _service_configs(self, stored: dict[str, Any]) -> ServiceConfigs:
        raw = stored.get("service_configs")
        base = raw if isinstance(raw, dict) else {}
        configs = ServiceConfigs(**base)
        if "graph" not in base:
            configs.graph.enabled = bool(stored.get("graph_enabled", False))
            configs.graph.provider = stored.get("graph_provider") or "lightrag"
            configs.graph.searchType = stored.get("graph_search_type") or "local"
        if configs.rerank.apiKey:
            configs.rerank.apiKey = ""
            configs.rerank.hasApiKey = True
        return configs

    def get_chat_settings(self, user_id: str) -> ChatSettings:
        runtime = self.app_runtime
        stored = {}
        if runtime is not None and hasattr(runtime, "chat_page"):
            stored = runtime.chat_page._read_chat_runtime_settings().get(
                str(user_id), {}
            )

        selected_template = (
            stored.get("prompt_template_select") or DEFAULT_PROMPT_TEMPLATE_NAME
        )
        template_text = DEFAULT_PROMPT_TEMPLATE_TEXT
        templates = DEFAULT_PROMPT_TEMPLATES
        if runtime is not None and hasattr(runtime, "chat_page"):
            templates = runtime.chat_page._load_prompt_template_map(user_id)
            if selected_template not in templates:
                selected_template = next(iter(templates), DEFAULT_PROMPT_TEMPLATE_NAME)
            template_text = templates.get(
                selected_template, DEFAULT_PROMPT_TEMPLATE_TEXT
            )
        options = self._chat_setting_options(user_id)

        return ChatSettings(
            suggestedChat=bool(stored.get("suggestedChat", True)),
            reasoningMethod=stored.get("reasoning_type") or "simple",
            model=stored.get("model_type") or "",
            embeddingModel=stored.get("embedding_model") or "",
            modelConfig=self._empty_model_config(60),
            embeddingConfig=self._empty_model_config(30),
            modelConfigs=self._llm_configs(),
            embeddingConfigs=self._embedding_configs(),
            serviceConfigs=self._service_configs(stored),
            language=stored.get("language") or "zh",
            citationHighlight=stored.get("citation") or "highlight",
            mindmap=bool(stored.get("use_mindmap", True)),
            promptTemplate=selected_template,
            promptTemplateText=template_text,
            promptTemplates=templates,
            retrieval=RetrievalSettings(
                topK=int(stored.get("retrieval_top_k", 10)),
                firstRoundMultiplier=int(stored.get("first_round_multiplier", 10)),
                retrievalMode=stored.get("retrieval_mode") or "hybrid",
                enhancement=_normalize_retrieval_enhancement(
                    stored.get("retrieval_enhancement")
                ),
                rerank=bool(stored.get("use_reranking", True)),
                llmRerank=bool(stored.get("use_llm_reranking", False)),
                mmr=bool(stored.get("use_mmr", False)),
                prioritizeTable=bool(stored.get("prioritize_table", False)),
            ),
            options=options,
        )

    def _chat_setting_options(
        self, user_id: str = "default"
    ) -> dict[str, list[SelectOption]]:
        if self.app_runtime is None:
            return {}
        settings = self.app_runtime.default_settings
        reasoning_choices = settings.reasoning.settings.get("use").choices
        language_choices = settings.reasoning.settings.get("lang").choices
        model_choices = [SelectOption(label="默认", value="")]
        try:
            from ktem.llms.manager import llms

            model_choices += [
                SelectOption(label=name, value=name) for name in llms.options().keys()
            ]
        except Exception:
            pass
        embedding_choices = [SelectOption(label="默认", value="")]
        try:
            from ktem.embeddings.manager import embedding_models_manager

            embedding_choices += [
                SelectOption(label=name, value=name)
                for name in embedding_models_manager.options().keys()
                if name != "default"
            ]
        except Exception:
            pass

        templates = self.chat_page._load_prompt_template_map(user_id)
        return {
            "reasoningMethod": self._option_values(reasoning_choices),
            "language": self._option_values(language_choices),
            "model": model_choices,
            "embeddingModel": embedding_choices,
            "citationHighlight": [
                SelectOption(label="高亮", value="highlight"),
                SelectOption(label="内联", value="inline"),
                SelectOption(label="关闭", value="off"),
            ],
            "retrievalMode": [
                SelectOption(label="Hybrid", value="hybrid"),
                SelectOption(label="Vector", value="vector"),
                SelectOption(label="Full Text", value="text"),
            ],
            "retrievalEnhancement": [
                SelectOption(label="None", value="none"),
                SelectOption(label="Query Rewrite", value="rewrite"),
                SelectOption(label="HyDE", value="hyde"),
                SelectOption(label="RAG-Fusion", value="fusion"),
            ],
            "graphProvider": [
                SelectOption(label="LightRAG", value="lightrag"),
                SelectOption(label="NanoGraphRAG", value="nano"),
            ],
            "graphSearchType": [
                SelectOption(label="Local", value="local"),
                SelectOption(label="Global", value="global"),
                SelectOption(label="Hybrid", value="hybrid"),
            ],
            "promptTemplate": [
                SelectOption(label=name, value=name) for name in templates.keys()
            ],
        }

    def save_chat_settings(self, settings: ChatSettings, user_id: str) -> ChatSettings:
        self._save_new_model_configs(settings)
        _sync_graph_service_config(settings)
        stored = {}
        if self.app_runtime is not None and hasattr(self.app_runtime, "chat_page"):
            stored = self.chat_page._read_chat_runtime_settings().get(str(user_id), {})
        previous_service_configs = self._service_configs_for_save(stored)
        service_configs = settings.serviceConfigs.model_dump()
        previous_rerank_key = (
            previous_service_configs.get("rerank", {}).get("apiKey")
            if isinstance(previous_service_configs, dict)
            else ""
        )
        if not service_configs.get("rerank", {}).get("apiKey") and previous_rerank_key:
            service_configs["rerank"]["apiKey"] = previous_rerank_key

        template_name = (
            settings.promptTemplate or DEFAULT_PROMPT_TEMPLATE_NAME
        ).strip()
        if not template_name:
            raise HTTPException(status_code=400, detail="模板名称不能为空。")

        incoming_templates = {}
        for name, text in (settings.promptTemplates or {}).items():
            normalized_name = (name or "").strip()
            if normalized_name:
                incoming_templates[normalized_name] = text or ""
        templates = incoming_templates or self.chat_page._load_prompt_template_map(
            user_id
        )
        templates[template_name] = settings.promptTemplateText or ""
        self.chat_page._write_prompt_template_map(user_id, templates)

        self.chat_page.save_chat_runtime_settings(
            user_id,
            settings.reasoningMethod,
            settings.model,
            settings.embeddingModel,
            settings.language,
            settings.citationHighlight,
            settings.mindmap,
            settings.retrieval.topK,
            settings.retrieval.firstRoundMultiplier,
            settings.retrieval.retrievalMode,
            settings.retrieval.enhancement,
            settings.retrieval.rerank,
            settings.retrieval.llmRerank,
            settings.retrieval.mmr,
            settings.retrieval.prioritizeTable,
            template_name,
            service_configs,
        )
        return self.get_chat_settings(user_id)

    def _service_configs_for_save(self, stored: dict[str, Any]) -> dict[str, Any]:
        raw = stored.get("service_configs")
        return raw if isinstance(raw, dict) else {}

    def _save_new_model_configs(self, settings: ChatSettings) -> None:
        llm_name = settings.modelConfig.name.strip()
        if llm_name and settings.modelConfig.model.strip():
            from ktem.llms.manager import llms

            if llm_name in llms.info():
                raise HTTPException(
                    status_code=400,
                    detail=f"LLM 配置 `{llm_name}` 已存在，请使用新的配置名称。",
                )
            else:
                spec = self._openai_compatible_spec(
                    "kotaemon.llms.ChatOpenAI",
                    settings.modelConfig,
                    {},
                    fallback_timeout=60,
                )
                llms.add(llm_name, spec, settings.modelConfig.isDefault)
                if llms._allowed_names is not None:
                    llms._allowed_names.add(llm_name)
                    llms.load()
                settings.model = llm_name
                settings.modelConfig = ModelProviderConfig()

        embedding_name = settings.embeddingConfig.name.strip()
        if embedding_name and settings.embeddingConfig.model.strip():
            from ktem.embeddings.manager import embedding_models_manager

            if embedding_name in embedding_models_manager.info():
                raise HTTPException(
                    status_code=400,
                    detail=f"Embedding 配置 `{embedding_name}` 已存在，请使用新的配置名称。",
                )
            else:
                spec = self._openai_compatible_spec(
                    "kotaemon.embeddings.OpenAIEmbeddings",
                    settings.embeddingConfig,
                    {},
                    fallback_api_key="ollama",
                    fallback_timeout=30,
                )
                embedding_models_manager.add(
                    embedding_name,
                    spec,
                    settings.embeddingConfig.isDefault,
                )
                if embedding_models_manager._allowed_names is not None:
                    embedding_models_manager._allowed_names.add(embedding_name)
                    embedding_models_manager.load()
                settings.embeddingModel = embedding_name
                settings.embeddingConfig = ModelProviderConfig()

    def list_references(self, user_id: str) -> list[ReferenceDocument]:
        if self.app_runtime is None:
            return []

        references: list[ReferenceDocument] = []
        for index in self.app_runtime.index_manager.indices:
            source_table = index._resources.get("Source")
            if source_table is None:
                continue
            with Session(engine) as session:
                statement = select(source_table)
                for (source,) in session.execute(statement).all():
                    if not can_read_source(index, source, user_id):
                        continue
                    references.append(
                        ReferenceDocument(
                            id=str(source.id),
                            title=source.name,
                            source=index.name,
                            summary=self._source_summary(source),
                            updatedAt=_to_iso(source.date_created),
                            permission=self._source_permission_label(
                                index, source, user_id
                            ),
                            citations=[],
                        )
                    )
        return sorted(references, key=lambda item: item.updatedAt, reverse=True)

    def _file_index(self):
        if self.app_runtime is None:
            raise HTTPException(status_code=503, detail="应用运行态尚未初始化。")
        for index in self.app_runtime.index_manager.indices:
            if (
                index._resources.get("Source") is not None
                and index._resources.get("FileGroup") is not None
            ):
                return index
        raise HTTPException(status_code=503, detail="文件索引器尚未准备好。")

    def _can_access_group(self, group: Any, index: Any, user_id: str) -> bool:
        return not index.config.get("private", False) or group.user == user_id

    def _source_permission_label(self, index: Any, source: Any, user_id: str) -> str:
        principal = resolve_principal(user_id)
        rows = list_source_permissions(index, str(source.id))
        if str(getattr(source, "user", "") or "default") == principal.id:
            return "owner"
        for row in rows:
            if (
                row.principal_type == principal.type
                and row.principal_id == principal.id
                and row.permission == "owner"
            ):
                return "owner"
        if any(
            row.principal_type == "public"
            and row.principal_id == "*"
            and row.permission in {"read", "owner"}
            for row in rows
        ):
            return "public"
        return "read"

    def _file_permissions(
        self, index: Any, source_id: str
    ) -> list[SourcePermissionItem]:
        return [
            SourcePermissionItem(
                principalType=row.principal_type,
                principalId=row.principal_id,
                permission=row.permission,
            )
            for row in list_source_permissions(index, source_id)
        ]

    def list_file_workspace(self, user_id: str) -> FileWorkspaceState:
        index = self._file_index()
        Source = index._resources["Source"]
        FileGroup = index._resources["FileGroup"]
        directories: list[FileDirectory] = []
        files: list[FileItem] = []
        file_to_directory: dict[str, str] = {}

        with Session(engine) as session:
            group_statement = select(FileGroup)
            if index.config.get("private", False):
                group_statement = group_statement.where(FileGroup.user == user_id)
            for (group,) in session.execute(group_statement).all():
                file_ids = self._visible_file_ids(
                    index,
                    user_id,
                    [
                        str(file_id)
                        for file_id in (group.data or {}).get("files", [])
                        if file_id
                    ],
                )
                directories.append(
                    FileDirectory(
                        id=str(group.id),
                        name=str(group.name),
                        updatedAt=_to_iso(group.date_created),
                        fileIds=file_ids,
                    )
                )
                for file_id in file_ids:
                    file_to_directory.setdefault(file_id, str(group.id))

            source_statement = select(Source)
            for (source,) in session.execute(source_statement).all():
                if not can_read_source(index, source, user_id):
                    continue
                files.append(
                    FileItem(
                        id=str(source.id),
                        name=str(source.name),
                        source=index.name,
                        summary=self._source_summary(source),
                        updatedAt=_to_iso(source.date_created),
                        size=int(source.size or 0),
                        directoryId=file_to_directory.get(str(source.id)),
                        permission=self._source_permission_label(
                            index, source, user_id
                        ),
                    )
                )

        directories.sort(key=lambda item: item.name.lower())
        files.sort(key=lambda item: item.updatedAt, reverse=True)
        return FileWorkspaceState(directories=directories, files=files)

    def get_file_detail(
        self, file_id: str, user_id: str, type_filter: str | None = None
    ) -> FileDetail:
        index = self._file_index()
        Source = index._resources["Source"]
        Index = index._resources["Index"]
        workspace = self.list_file_workspace(user_id)
        workspace_file = next(
            (file for file in workspace.files if file.id == file_id), None
        )
        if workspace_file is None:
            raise HTTPException(status_code=404, detail="文件不存在或无权访问。")

        with Session(engine) as session:
            source = session.execute(select(Source).where(Source.id == file_id)).first()
            if source is None:
                raise HTTPException(status_code=404, detail="文件不存在。")
            matches = session.execute(
                select(Index).where(
                    Index.source_id == file_id,
                    Index.relation_type == "document",
                )
            )
            doc_ids = [row.target_id for (row,) in matches]

        docs = index._docstore.get(doc_ids) if doc_ids else []
        docs = sorted(
            docs, key=lambda doc: doc.metadata.get("page_label", float("inf"))
        )

        chunk_type_counts: dict[str, int] = {}
        for doc in docs:
            doc_type = str(doc.metadata.get("type") or "text")
            chunk_type_counts[doc_type] = chunk_type_counts.get(doc_type, 0) + 1

        filtered_docs = docs
        if type_filter and type_filter != "all":
            want = type_filter.lower()
            filtered_docs = [
                doc
                for doc in docs
                if str(doc.metadata.get("type") or "text").lower() == want
            ]

        chunks = [
            FileChunk(
                id=str(getattr(doc, "doc_id", "") or index_number),
                index=index_number + 1,
                type=str(doc.metadata.get("type") or "text"),
                text=str(getattr(doc, "text", "") or ""),
                pageLabel=(
                    str(doc.metadata.get("page_label"))
                    if doc.metadata.get("page_label") is not None
                    else None
                ),
                metadata={
                    key: value
                    for key, value in (doc.metadata or {}).items()
                    if isinstance(value, (str, int, float, bool)) or value is None
                },
            )
            for index_number, doc in enumerate(filtered_docs)
        ]

        return FileDetail(
            file=workspace_file,
            chunkCount=len(docs),
            chunkTypeCounts=chunk_type_counts,
            chunks=chunks,
            permissions=self._file_permissions(index, file_id),
        )

    def update_file_permissions(
        self, file_id: str, payload: UpdateFilePermissionsPayload, user_id: str
    ) -> FileDetail:
        index = self._file_index()
        Source = index._resources["Source"]
        with Session(engine) as session:
            source = session.execute(select(Source).where(Source.id == file_id)).first()
            if source is None:
                raise HTTPException(status_code=404, detail="文件不存在。")
            source_obj = source[0]

        try:
            set_source_acl(
                index,
                source_obj,
                user_id,
                [entry.model_dump() for entry in payload.permissions],
            )
        except PermissionError:
            raise HTTPException(status_code=403, detail="只有文件所有者可以修改权限。")
        return self.get_file_detail(file_id, user_id)

    def _accessible_file_ids(self, index: Any, user_id: str) -> set[str]:
        Source = index._resources["Source"]
        with Session(engine) as session:
            rows = session.execute(select(Source)).all()
        return {
            str(source.id)
            for (source,) in rows
            if can_read_source(index, source, user_id)
        }

    def _visible_file_ids(
        self, index: Any, user_id: str, file_ids: list[str]
    ) -> list[str]:
        return filter_source_ids(index, self._unique_file_ids(file_ids), user_id)

    def create_directory(
        self, payload: CreateDirectoryPayload, user_id: str
    ) -> FileDirectory:
        index = self._file_index()
        FileGroup = index._resources["FileGroup"]
        name = payload.name.strip()
        if not name:
            raise HTTPException(status_code=400, detail="目录名称不能为空。")
        if len(name) > 40:
            raise HTTPException(status_code=400, detail="目录名称不能超过 40 个字符。")

        with Session(engine) as session:
            existing = (
                session.query(FileGroup).filter_by(name=name, user=user_id).first()
            )
            if existing:
                raise HTTPException(status_code=409, detail=f"目录 {name} 已存在。")
            group = FileGroup(
                name=name,
                user=user_id,
                data={"files": self._visible_file_ids(index, user_id, payload.fileIds)},
            )
            session.add(group)
            session.commit()
            session.refresh(group)
            return FileDirectory(
                id=str(group.id),
                name=str(group.name),
                updatedAt=_to_iso(group.date_created),
                fileIds=list((group.data or {}).get("files", [])),
            )

    def update_directory(
        self, directory_id: str, payload: UpdateDirectoryPayload, user_id: str
    ) -> FileDirectory:
        index = self._file_index()
        FileGroup = index._resources["FileGroup"]
        with Session(engine) as session:
            group = session.query(FileGroup).filter_by(id=directory_id).first()
            if not group:
                raise HTTPException(status_code=404, detail="目录不存在。")
            if not self._can_access_group(group, index, user_id):
                raise HTTPException(status_code=403, detail="无权更新该目录。")
            if payload.name is not None:
                name = payload.name.strip()
                if not name:
                    raise HTTPException(status_code=400, detail="目录名称不能为空。")
                group.name = name
            if payload.fileIds is not None:
                group.data = {
                    "files": self._visible_file_ids(index, user_id, payload.fileIds)
                }
            session.add(group)
            session.commit()
            session.refresh(group)
            return FileDirectory(
                id=str(group.id),
                name=str(group.name),
                updatedAt=_to_iso(group.date_created),
                fileIds=list((group.data or {}).get("files", [])),
            )

    def delete_directory(self, directory_id: str, user_id: str) -> None:
        index = self._file_index()
        FileGroup = index._resources["FileGroup"]
        with Session(engine) as session:
            group = session.query(FileGroup).filter_by(id=directory_id).first()
            if not group:
                return
            if not self._can_access_group(group, index, user_id):
                raise HTTPException(status_code=403, detail="无权删除该目录。")
            session.delete(group)
            session.commit()

    def move_files(self, payload: MoveFilesPayload, user_id: str) -> FileWorkspaceState:
        index = self._file_index()
        FileGroup = index._resources["FileGroup"]
        ordered_file_ids = self._visible_file_ids(index, user_id, payload.fileIds)
        file_ids = set(ordered_file_ids)
        if not file_ids:
            return self.list_file_workspace(user_id)

        with Session(engine) as session:
            target_group = None
            if payload.directoryId:
                target_group = (
                    session.query(FileGroup).filter_by(id=payload.directoryId).first()
                )
                if not target_group:
                    raise HTTPException(status_code=404, detail="目标目录不存在。")
                if not self._can_access_group(target_group, index, user_id):
                    raise HTTPException(status_code=403, detail="无权移动到该目录。")

            group_statement = select(FileGroup)
            if index.config.get("private", False):
                group_statement = group_statement.where(FileGroup.user == user_id)
            for (group,) in session.execute(group_statement).all():
                current = [
                    str(file_id)
                    for file_id in (group.data or {}).get("files", [])
                    if file_id
                ]
                next_files = [file_id for file_id in current if file_id not in file_ids]
                if next_files != current:
                    group.data = {"files": next_files}
                    session.add(group)

            if target_group:
                current = [
                    str(file_id)
                    for file_id in (target_group.data or {}).get("files", [])
                    if file_id
                ]
                target_group.data = {
                    "files": self._unique_file_ids(current + ordered_file_ids)
                }
                session.add(target_group)
            session.commit()

        return self.list_file_workspace(user_id)

    def _unique_file_ids(self, file_ids: list[str]) -> list[str]:
        output: list[str] = []
        seen: set[str] = set()
        for file_id in file_ids:
            normalized = str(file_id).strip()
            if normalized and normalized not in seen:
                output.append(normalized)
                seen.add(normalized)
        return output

    def _source_summary(self, source: Any) -> str:
        note = source.note or {}
        bits = []
        if note.get("loader"):
            bits.append(f"Loader: {note['loader']}")
        if note.get("chunk_size"):
            overlap = note.get("chunk_overlap", 0)
            bits.append(f"Chunk: {note['chunk_size']}/{overlap}")
        if note.get("tokens"):
            bits.append(f"Tokens: {note['tokens']}")
        if source.size:
            bits.append(f"Size: {source.size} bytes")
        return " | ".join(bits) or str(source.path or source.name)

    def _references_from_retrieval(
        self, retrieval_history: list[str]
    ) -> list[ReferenceDocument]:
        documents_by_title: dict[str, ReferenceDocument] = {}
        for msg_index, retrieval in enumerate(retrieval_history or []):
            if not retrieval:
                continue
            chunks = re.findall(
                r"<details\b.*?</details>",
                retrieval,
                flags=re.IGNORECASE | re.S,
            )
            for chunk_index, chunk in enumerate(chunks):
                title_match = re.search(r"<summary[^>]*>(.*?)</summary>", chunk, re.S)
                raw_title = (
                    _html_to_text(title_match.group(1)) if title_match else "引用依据"
                )
                excerpt = _html_to_text(chunk)
                if not excerpt:
                    continue
                document_id = raw_title or f"retrieval-{msg_index}-{chunk_index}"
                citation = Citation(
                    id=f"cit-{msg_index}-{chunk_index}",
                    documentId=document_id,
                    title=raw_title,
                    excerpt=excerpt[:600],
                    score=0.0,
                    highlight=excerpt[:120],
                )
                document = documents_by_title.setdefault(
                    document_id,
                    ReferenceDocument(
                        id=document_id,
                        title=raw_title,
                        source="Chat retrieval",
                        summary=excerpt[:240],
                        updatedAt=_now(),
                        citations=[],
                    ),
                )
                document.citations.append(citation)
        return list(documents_by_title.values())

    def _graph_reference_from_trace(
        self, trace_data: dict[str, Any]
    ) -> ReferenceDocument | None:
        graph = trace_data.get("graph_rag") or {}
        if not graph.get("enabled"):
            return None

        entities = list(graph.get("entities") or [])
        relationships = list(graph.get("relationships") or [])
        paths = list(graph.get("paths") or [])
        fragments = list(graph.get("answer_fragments") or [])
        if not any([entities, relationships, paths, fragments]):
            return None

        def label(record: dict[str, Any], *keys: str) -> str:
            for key in keys:
                value = record.get(key)
                if value not in (None, ""):
                    return str(value)
            return ""

        citations: list[Citation] = []
        for index, entity in enumerate(entities[:6]):
            if not isinstance(entity, dict):
                continue
            name = label(entity, "entity", "entity_name", "name") or "实体"
            description = label(entity, "description", "type") or name
            citations.append(
                Citation(
                    id=f"graph-entity-{index}",
                    documentId="graph-rag-reference",
                    title=f"实体：{name}",
                    excerpt=description,
                    score=1.0,
                    highlight=name,
                )
            )

        for index, rel in enumerate(relationships[:8]):
            if not isinstance(rel, dict):
                continue
            source = label(rel, "source", "src_id", "source_id")
            target = label(rel, "target", "tgt_id", "target_id")
            description = label(rel, "description", "keywords")
            title = f"关系：{source} -> {target}" if source or target else "关系"
            citations.append(
                Citation(
                    id=f"graph-relation-{index}",
                    documentId="graph-rag-reference",
                    title=title,
                    excerpt=description or title,
                    score=1.0,
                    highlight=source or target or "关系",
                )
            )

        for index, path in enumerate(paths[:5]):
            if not isinstance(path, dict):
                continue
            nodes = path.get("nodes") or []
            path_text = " -> ".join(str(node) for node in nodes) if isinstance(nodes, list) else str(nodes)
            description = label(path, "description") or path_text
            citations.append(
                Citation(
                    id=f"graph-path-{index}",
                    documentId="graph-rag-reference",
                    title=f"路径：{path_text or index + 1}",
                    excerpt=description,
                    score=1.0,
                    highlight=path_text,
                )
            )

        for index, fragment in enumerate(fragments[:3]):
            text = str(fragment or "").strip()
            if not text:
                continue
            citations.append(
                Citation(
                    id=f"graph-fragment-{index}",
                    documentId="graph-rag-reference",
                    title=f"图谱答案片段 {index + 1}",
                    excerpt=text,
                    score=1.0,
                    highlight=text[:80],
                )
            )

        provider = graph.get("provider") or "GraphRAG"
        search_type = graph.get("search_type") or "local"
        summary = (
            f"{provider} / {search_type}: "
            f"{len(entities)} entities, {len(relationships)} relationships, "
            f"{len(paths)} paths"
        )
        return ReferenceDocument(
            id="graph-rag-reference",
            title="GraphRAG 图谱证据",
            source="GraphRAG",
            summary=summary,
            updatedAt=_now(),
            citations=citations,
        )

    def _fallback_answer_from_references(
        self,
        question: str,
        references: list[ReferenceDocument],
        *,
        error: Exception | None = None,
    ) -> str:
        graph_reference = next(
            (reference for reference in references if reference.id == "graph-rag-reference"),
            None,
        )
        relation_lines: list[str] = []
        entity_lines: list[str] = []
        if graph_reference:
            for citation in graph_reference.citations:
                if citation.id.startswith("graph-relation"):
                    text = str(citation.excerpt or citation.title or "").strip()
                    if text and text not in relation_lines:
                        relation_lines.append(text)
                elif citation.id.startswith("graph-entity"):
                    text = str(citation.title or "").replace("实体：", "").strip()
                    if text and text not in entity_lines:
                        entity_lines.append(text)

        answer_parts = [
            "模型回答生成失败，但已根据检索到的证据整理出以下结论："
        ]
        if relation_lines:
            answer_parts.extend(f"- {line}" for line in relation_lines[:6])
        elif references:
            for reference in references[:2]:
                for citation in reference.citations[:3]:
                    text = str(citation.excerpt or citation.title or "").strip()
                    if text:
                        answer_parts.append(f"- {text[:220]}")
        else:
            answer_parts.append("- 本次没有可用的知识引用证据。")

        if entity_lines:
            answer_parts.append(f"涉及实体：{'、'.join(entity_lines[:8])}。")
        if error:
            answer_parts.append(
                f"生成失败原因：{type(error).__name__}，请检查模型额度或模型配置后重试。"
            )
        return "\n".join(answer_parts)

    def _selected_components(self, user_id: str, selected_ids: list[str] | None = None):
        components = []
        selected_ids = selected_ids or []
        file_index = self._file_index()
        for index in self.app_runtime.index_manager.indices:
            if index is not file_index:
                continue
            if index.selector is None:
                continue
            if isinstance(index.selector, int):
                components.append(
                    ("select" if selected_ids else "all", selected_ids, user_id)
                )
            else:
                components.extend(
                    ("select", selected_ids, user_id)
                    if selected_ids
                    else index.default_selector
                )
        return components

    def _effective_principal_trace(
        self, user_id: str, selected_file_ids: list[str]
    ) -> dict[str, Any]:
        principal = resolve_principal(user_id)
        permission_entries: list[dict[str, Any]] = []
        filtered_selection_count = 0
        try:
            index = self._file_index()
            visible_ids = set(self._visible_file_ids(index, user_id, selected_file_ids))
            filtered_selection_count = len(
                [
                    source_id
                    for source_id in selected_file_ids
                    if source_id not in visible_ids
                ]
            )
            for source_id in selected_file_ids:
                if str(source_id) not in visible_ids:
                    continue
                permission_entries.append(
                    {
                        "source_id": str(source_id),
                        "read": True,
                    }
                )
        except Exception:
            permission_entries = []
            filtered_selection_count = 0
        return {
            "principal": {"type": principal.type, "id": principal.id},
            "roles": [],
            "departments": [],
            "permissions": permission_entries,
            "filtered_selection_count": filtered_selection_count,
        }

    async def upload_files(
        self,
        files: list[UploadFile],
        user_id: str,
        directory_id: str | None = None,
        options: UploadIndexingOptions | None = None,
    ) -> list[ReferenceDocument]:
        self.chat_page
        if not getattr(self.chat_page, "first_indexing_file_fn", None):
            raise HTTPException(status_code=503, detail="文件索引器尚未准备好。")
        options = options or UploadIndexingOptions()
        if (
            options.chunkSize is not None
            and options.chunkOverlap is not None
            and options.chunkOverlap >= options.chunkSize
        ):
            raise HTTPException(
                status_code=400, detail="chunk_overlap 必须小于 chunk_size。"
            )

        temp_dir = Path(tempfile.mkdtemp(prefix="react-upload-"))
        saved_paths: list[str] = []
        try:
            for file in files:
                if not file.filename:
                    continue
                path = temp_dir / Path(file.filename).name
                with path.open("wb") as output:
                    shutil.copyfileobj(file.file, output)
                saved_paths.append(str(path))

            if not saved_paths:
                return []

            settings = _load_user_settings(user_id, self.app_runtime)
            index = self._file_index()
            settings = deepcopy(settings)
            settings[f"index.options.{index.id}.quick_index_mode"] = False
            if options.embeddingModel:
                settings[f"index.options.{index.id}.embedding"] = options.embeddingModel
            if options.chunkSize is not None:
                settings[f"index.options.{index.id}.chunk_size"] = options.chunkSize
            if options.chunkOverlap is not None:
                settings[f"index.options.{index.id}.chunk_overlap"] = (
                    options.chunkOverlap
                )
            file_ids = await asyncio.to_thread(
                self.chat_page.first_indexing_file_fn,
                saved_paths,
                options.reindex,
                settings,
                user_id,
            )
            references = self.list_references(user_id)
            reference_by_id = {reference.id: reference for reference in references}
            uploaded_references = [
                reference_by_id[file_id]
                for file_id in file_ids
                if file_id and file_id in reference_by_id
            ]
            uploaded_file_ids = [reference.id for reference in uploaded_references]
            for uploaded_id in uploaded_file_ids:
                index = self._file_index()
                Source = index._resources["Source"]
                with Session(engine) as session:
                    row = session.execute(
                        select(Source).where(Source.id == uploaded_id)
                    ).first()
                if row is not None:
                    index._resources["PermissionService"].ensure_default_acl(
                        index, row[0], user_id
                    )
            if uploaded_file_ids and directory_id:
                self.move_files(
                    MoveFilesPayload(
                        fileIds=uploaded_file_ids, directoryId=directory_id
                    ),
                    user_id,
                )
            return uploaded_references
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    async def reembed_file(
        self,
        file_id: str,
        user_id: str,
        options: UploadIndexingOptions | None = None,
    ) -> FileDetail:
        index = self._file_index()
        Source = index._resources["Source"]
        with Session(engine) as session:
            row = session.execute(select(Source).where(Source.id == file_id)).first()
            if row is None:
                raise HTTPException(status_code=404, detail="文件不存在。")
            source = row[0]
            if self._source_permission_label(index, source, user_id) != "owner":
                raise HTTPException(
                    status_code=403, detail="只有文件所有者可以重新 embedding。"
                )
            stored_path = index._resources["FileStoragePath"] / str(source.path)
            if not stored_path.exists():
                raise HTTPException(
                    status_code=404, detail="原始文件不存在，无法重新 embedding。"
                )
            source_name = str(source.name)

        options = UploadIndexingOptions(
            chunkSize=options.chunkSize if options else None,
            chunkOverlap=options.chunkOverlap if options else None,
            embeddingModel=options.embeddingModel if options else None,
            reindex=True,
        )
        if (
            options.chunkSize is not None
            and options.chunkOverlap is not None
            and options.chunkOverlap >= options.chunkSize
        ):
            raise HTTPException(
                status_code=400, detail="chunk_overlap 必须小于 chunk_size。"
            )

        before_detail = self.get_file_detail(file_id, user_id)
        temp_dir = Path(tempfile.mkdtemp(prefix="react-reembed-"))
        try:
            temp_path = temp_dir / Path(source_name).name
            shutil.copy(stored_path, temp_path)
            try:
                await asyncio.to_thread(
                    self.reindex_existing_file,
                    file_id,
                    user_id,
                    temp_path,
                    options,
                )
            except HTTPException:
                raise
            except Exception as exc:
                raise HTTPException(
                    status_code=500,
                    detail=f"重新 embedding 失败：{exc}",
                ) from exc
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

        detail = self.get_file_detail(file_id, user_id)
        if detail.chunkCount == 0:
            prefix = "旧索引可能已被清空，" if before_detail.chunkCount > 0 else ""
            raise HTTPException(
                status_code=500,
                detail=(
                    f"重新 embedding 未生成任何 chunk。{prefix}"
                    "请检查后端索引日志，或删除后重新上传该文件。"
                ),
            )
        return detail

    def reindex_existing_file(
        self,
        file_id: str,
        user_id: str,
        file_path: Path,
        options: UploadIndexingOptions,
    ) -> None:
        index = self._file_index()
        settings = deepcopy(_load_user_settings(user_id, self.app_runtime))
        settings[f"index.options.{index.id}.quick_index_mode"] = False
        if options.embeddingModel:
            settings[f"index.options.{index.id}.embedding"] = options.embeddingModel
        if options.chunkSize is not None:
            settings[f"index.options.{index.id}.chunk_size"] = options.chunkSize
        if options.chunkOverlap is not None:
            settings[f"index.options.{index.id}.chunk_overlap"] = options.chunkOverlap

        indexing_pipeline = index.get_indexing_pipeline(settings, user_id)
        pipeline = indexing_pipeline.route(file_path)

        self._delete_file_chunks(index, file_id)

        file_name = file_path.name
        extra_info = default_file_metadata_func(str(file_path))
        extra_info["file_id"] = file_id
        extra_info["collection_name"] = pipeline.collection_name

        docs = pipeline.loader.load_data(file_path, extra_info=extra_info)
        for _ in pipeline.handle_docs(docs, file_id, file_name):
            pass
        if isinstance(indexing_pipeline, ProductGraphIndexingMixin):
            indexing_pipeline.collection_graph_id = collection_graph_id(index.id)
            for _ in indexing_pipeline.index_graph_for_docs([file_id], docs):
                pass
        pipeline.finish(
            file_id,
            file_path,
            chunk_size=(
                int(pipeline.splitter._kwargs.get("chunk_size", 0))
                if pipeline.splitter
                else None
            ),
            chunk_overlap=(
                int(pipeline.splitter._kwargs.get("chunk_overlap", 0))
                if pipeline.splitter
                else None
            ),
        )

    def _delete_file_chunks(self, index: Any, file_id: str) -> None:
        Index = index._resources["Index"]
        vectorstore = index._resources["VectorStore"]
        docstore = index._resources["DocStore"]
        vs_ids: list[str] = []
        ds_ids: list[str] = []
        with Session(engine) as session:
            rows = session.execute(
                select(Index).where(Index.source_id == str(file_id))
            ).all()
            for (row,) in rows:
                if row.relation_type == "vector":
                    vs_ids.append(row.target_id)
                elif row.relation_type == "document":
                    ds_ids.append(row.target_id)
                session.delete(row)
            session.commit()

        if vs_ids and vectorstore:
            vectorstore.delete(vs_ids)
        if ds_ids:
            docstore.delete(ds_ids)

    def delete_file(self, file_id: str, user_id: str) -> None:
        index = self._file_index()
        Source = index._resources["Source"]
        stored_path: Path | None = None
        stored_hash: str | None = None
        with Session(engine) as session:
            row = session.execute(select(Source).where(Source.id == file_id)).first()
            if row is not None:
                source = row[0]
                if self._source_permission_label(index, source, user_id) != "owner":
                    raise HTTPException(
                        status_code=403, detail="只有文件所有者可以删除文件。"
                    )
                stored_hash = str(source.path)
                stored_path = index._resources["FileStoragePath"] / stored_hash

        self._delete_file_chunks(index, file_id)
        if stored_path is not None:
            with Session(engine) as session:
                still_referenced = session.execute(
                    select(Source.id).where(
                        Source.path == stored_hash,
                        Source.id != str(file_id),
                    )
                ).first()
            if still_referenced is None:
                stored_path.unlink(missing_ok=True)

        FileGroup = index._resources["FileGroup"]
        with Session(engine) as session:
            session.execute(
                delete(SourcePermission).where(
                    SourcePermission.index_id == int(index.id),
                    SourcePermission.source_id == str(file_id),
                )
            )
            session.execute(delete(Source).where(Source.id == str(file_id)))
            group_statement = select(FileGroup)
            for (group,) in session.execute(group_statement).all():
                current = [
                    str(item) for item in (group.data or {}).get("files", []) if item
                ]
                next_files = [item for item in current if item != file_id]
                if next_files != current:
                    group.data = {"files": next_files}
                    session.add(group)
            session.commit()

    def _append_exchange_to_db(
        self,
        conversation_id: str,
        user_id: str,
        user_text: str,
        assistant_text: str,
        retrieval_content: str,
    ) -> None:
        with Session(engine) as session:
            statement = select(DbConversation).where(
                DbConversation.id == conversation_id
            )
            conversation = session.exec(statement).one_or_none()
            if conversation is None:
                raise HTTPException(status_code=404, detail="会话不存在。")
            if conversation.user != user_id:
                raise HTTPException(status_code=403, detail="只能更新自己的会话。")

            data_source = deepcopy(conversation.data_source or {})
            messages = data_source.get("messages", [])
            messages.append([user_text, assistant_text])
            retrieval_messages = data_source.get("retrieval_messages", [])
            retrieval_messages.append(retrieval_content)
            plot_history = data_source.get("plot_history", [])
            plot_history.append(None)
            data_source.update(
                {
                    "messages": messages,
                    "retrieval_messages": retrieval_messages,
                    "plot_history": plot_history,
                    "state": data_source.get("state", STATE),
                    "likes": data_source.get("likes", []),
                }
            )
            conversation.data_source = data_source
            conversation.date_updated = datetime.now(CHINA_TZ)
            if len(messages) == 1 and conversation.name.startswith("Untitled -"):
                conversation.name = user_text[:40] or conversation.name
            session.add(conversation)
            session.commit()

    def run_rag_once(
        self,
        payload: SendMessagePayload,
        user_id: str,
        emit_token,
        *,
        chat_history: list[Any] | None = None,
        state: dict[str, Any] | None = None,
        turn_index: int = 0,
        message_id: str | None = None,
    ) -> RagPipelineRunResult:
        chat_history = chat_history or []
        state = state or STATE
        settings = _load_user_settings(user_id, self.app_runtime)
        selected_components = self._selected_components(
            user_id, payload.selectedFileIds
        )
        prompt_text = payload.settings.promptTemplateText
        prompt_templates = payload.settings.promptTemplates or {}
        if prompt_templates.get(payload.settings.promptTemplate):
            prompt_text = prompt_templates[payload.settings.promptTemplate]
        elif not prompt_text:
            templates = self.chat_page._load_prompt_template_map(user_id)
            if payload.settings.promptTemplate in templates:
                prompt_text = templates[payload.settings.promptTemplate]
        graph_config = _sync_graph_service_config(payload.settings)
        pipeline, reasoning_state = self.chat_page.create_pipeline(
            settings,
            payload.settings.reasoningMethod,
            payload.settings.model,
            payload.settings.embeddingModel,
            payload.settings.mindmap,
            payload.settings.citationHighlight,
            payload.settings.language,
            payload.settings.retrieval.topK,
            payload.settings.retrieval.firstRoundMultiplier,
            payload.settings.retrieval.retrievalMode,
            payload.settings.retrieval.enhancement,
            payload.settings.retrieval.rerank,
            payload.settings.retrieval.llmRerank,
            payload.settings.retrieval.mmr,
            payload.settings.retrieval.prioritizeTable,
            prompt_text,
            deepcopy(state),
            None,
            user_id,
            *selected_components,
            session_graph_enabled=graph_config.enabled,
            session_graph_provider=graph_config.provider,
            session_graph_search_type=graph_config.searchType,
        )

        queue: asyncio.Queue[Any] = asyncio.Queue()
        pipeline.set_output_queue(queue)
        retrieval_params = {
            "topK": payload.settings.retrieval.topK,
            "firstRoundMultiplier": payload.settings.retrieval.firstRoundMultiplier,
            "retrievalMode": payload.settings.retrieval.retrievalMode,
            "enhancement": payload.settings.retrieval.enhancement,
            "rerank": payload.settings.retrieval.rerank,
            "llmRerank": payload.settings.retrieval.llmRerank,
            "mmr": payload.settings.retrieval.mmr,
            "prioritizeTable": payload.settings.retrieval.prioritizeTable,
            "graph": {
                "enabled": graph_config.enabled,
                "provider": graph_config.provider,
                "searchType": graph_config.searchType,
            },
            "promptTemplate": payload.settings.promptTemplate,
            "promptTemplateText": prompt_text,
            "llmModel": payload.settings.model,
            "embeddingModel": payload.settings.embeddingModel,
        }
        trace_recorder = RagTraceRecorder(
            conversation_id=payload.conversationId,
            user_id=user_id,
            question=payload.content,
            selected_file_ids=payload.selectedFileIds,
            retrieval_params=retrieval_params,
            effective_principal=self._effective_principal_trace(
                user_id, payload.selectedFileIds
            ),
            turn_index=turn_index,
        )
        message_id = message_id or f"{payload.conversationId}-{turn_index}-assistant"
        trace_recorder.set_message(message_id)
        set_active_recorder(trace_recorder)

        answer_text = ""
        retrieval_content = ""
        last_emit_len = 0
        try:
            for response in pipeline.stream(
                payload.content, payload.conversationId, chat_history
            ):
                if not getattr(response, "channel", None):
                    continue
                if response.channel == "chat":
                    raw_content = response.content
                    if raw_content is None:
                        answer_text = ""
                        last_emit_len = 0
                        continue
                    content = str(raw_content)
                    answer_text += content
                    if content:
                        emit_token(content)
                        last_emit_len = len(answer_text)
                elif response.channel == "info":
                    content = response.content
                    if isinstance(content, str):
                        retrieval_content += _reference_html_only(content)
                elif response.channel == "citation":
                    retrieval_content += str(response.content or "")
        except Exception as exc:
            trace_recorder.record_error("pipeline", exc)
            trace_data = trace_recorder.finish("failed")
            references = self._references_from_retrieval([retrieval_content])
            graph_reference = self._graph_reference_from_trace(trace_data)
            if graph_reference is not None:
                references = [graph_reference, *references]
            citations = references[0].citations if references else []
            trace_row = save_trace(trace_data)
            trace_summary = self._trace_summary_to_api(trace_row)
            answer_text = self._fallback_answer_from_references(
                payload.content,
                references,
                error=exc,
            )
            formatted_answer = self.chat_page.format_answer_with_refs(
                answer_text, "", answer_text
            )
            return RagPipelineRunResult(
                answerText=answer_text,
                formattedAnswer=formatted_answer,
                retrievalContent=retrieval_content,
                references=references,
                citations=citations,
                trace=trace_summary,
                traceData=trace_data,
                messageId=message_id,
                turnIndex=turn_index,
            )
        finally:
            set_active_recorder(None)

        if len(answer_text) > last_emit_len:
            emit_token(answer_text[last_emit_len:])

        if not answer_text:
            answer_text = getattr(
                flowsettings, "KH_CHAT_EMPTY_MSG_PLACEHOLDER", "(Sorry, I don't know)"
            )

        trace_data = trace_recorder.finish("completed")
        references = self._references_from_retrieval([retrieval_content])
        graph_reference = self._graph_reference_from_trace(trace_data)
        if graph_reference is not None:
            references = [graph_reference, *references]
        citations = references[0].citations if references else []
        trace_row = save_trace(trace_data)
        trace_summary = self._trace_summary_to_api(trace_row)
        formatted_answer = self.chat_page.format_answer_with_refs(
            answer_text, "", answer_text
        )
        return RagPipelineRunResult(
            answerText=answer_text,
            formattedAnswer=formatted_answer,
            retrievalContent=retrieval_content,
            references=references,
            citations=citations,
            trace=trace_summary,
            traceData=trace_data,
            messageId=message_id,
            turnIndex=turn_index,
        )

    def _run_pipeline_sync(
        self,
        payload: SendMessagePayload,
        user_id: str,
        emit_token,
    ) -> SendMessageResult:
        conversation = _get_conversation_or_404(payload.conversationId, user_id)
        if conversation.user != user_id:
            raise HTTPException(status_code=403, detail="只能在自己的会话中发送消息。")

        chat_history = (
            conversation.data_source.get("messages", [])
            if conversation.data_source
            else []
        )
        state = (
            conversation.data_source.get("state", STATE)
            if conversation.data_source
            else STATE
        )
        turn_index = len(chat_history)
        started = time.perf_counter()
        result = self.run_rag_once(
            payload,
            user_id,
            emit_token,
            chat_history=chat_history,
            state=deepcopy(state),
            turn_index=turn_index,
        )
        self._append_exchange_to_db(
            payload.conversationId,
            user_id,
            payload.content,
            result.formattedAnswer,
            result.retrievalContent,
        )

        message = ChatMessage(
            id=result.messageId,
            conversationId=payload.conversationId,
            role="assistant",
            content=result.formattedAnswer,
            createdAt=_now(),
            citations=result.citations,
        )
        print(f"React chat completed in {time.perf_counter() - started:.2f}s")
        return SendMessageResult(
            message=message, references=result.references, trace=result.trace
        )

    def send_message(
        self, payload: SendMessagePayload, user_id: str
    ) -> SendMessageResult:
        tokens: list[str] = []
        return self._run_pipeline_sync(payload, user_id, tokens.append)

    def list_eval_datasets(self, user_id: str) -> list[RagEvalDatasetItem]:
        return [
            self._eval_dataset_to_api(dataset)
            for dataset in eval_store.list_datasets(user_id)
        ]

    def create_eval_dataset(
        self, payload: RagEvalDatasetPayload, user_id: str
    ) -> RagEvalDatasetItem:
        name = payload.name.strip()
        if not name:
            raise HTTPException(status_code=400, detail="测试集名称不能为空。")
        dataset = eval_store.create_dataset(
            owner_user_id=user_id,
            name=name,
            description=payload.description,
            tags=payload.tags,
        )
        return self._eval_dataset_to_api(dataset)

    def update_eval_dataset(
        self, dataset_id: str, payload: RagEvalDatasetPayload, user_id: str
    ) -> RagEvalDatasetItem:
        try:
            dataset = eval_store.update_dataset(
                dataset_id,
                user_id,
                name=payload.name,
                description=payload.description,
                tags=payload.tags,
            )
        except KeyError:
            raise HTTPException(status_code=404, detail="测试集不存在。")
        return self._eval_dataset_to_api(dataset)

    def delete_eval_dataset(self, dataset_id: str, user_id: str) -> None:
        eval_store.delete_dataset(dataset_id, user_id)

    def list_eval_examples(
        self, dataset_id: str, user_id: str
    ) -> list[RagEvalExampleItem]:
        try:
            examples = eval_store.list_examples(dataset_id, user_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="测试集不存在。")
        return [self._eval_example_to_api(example) for example in examples]

    def create_eval_example(
        self,
        dataset_id: str,
        payload: RagEvalExamplePayload,
        user_id: str,
    ) -> RagEvalExampleItem:
        question = payload.question.strip()
        if not question:
            raise HTTPException(status_code=400, detail="问题不能为空。")
        try:
            example = eval_store.create_example(
                dataset_id=dataset_id,
                owner_user_id=user_id,
                question=question,
                expected_answer=payload.expectedAnswer,
                expected_source_ids=payload.expectedSourceIds,
                expected_keywords=payload.expectedKeywords,
                evaluator_user_id=payload.evaluatorUserId or user_id,
                selected_file_ids=payload.selectedFileIds,
                tags=payload.tags,
            )
        except KeyError:
            raise HTTPException(status_code=404, detail="测试集不存在。")
        return self._eval_example_to_api(example)

    def update_eval_example(
        self,
        example_id: str,
        payload: RagEvalExamplePayload,
        user_id: str,
    ) -> RagEvalExampleItem:
        question = payload.question.strip()
        if not question:
            raise HTTPException(status_code=400, detail="问题不能为空。")
        try:
            example = eval_store.update_example(
                example_id,
                user_id,
                question=question,
                expected_answer=payload.expectedAnswer,
                expected_source_ids=payload.expectedSourceIds,
                expected_keywords=payload.expectedKeywords,
                evaluator_user_id=payload.evaluatorUserId or user_id,
                selected_file_ids=payload.selectedFileIds,
                tags=payload.tags,
            )
        except KeyError:
            raise HTTPException(status_code=404, detail="样例不存在。")
        return self._eval_example_to_api(example)

    def delete_eval_example(self, example_id: str, user_id: str) -> None:
        eval_store.delete_example(example_id, user_id)

    def _run_eval_example_sync(
        self,
        example: Any,
        owner_user_id: str,
        selected_file_ids: list[str] | None = None,
        run: Any | None = None,
        variant: str | None = None,
        experiment_tag: str | None = None,
    ) -> RagEvalRunDetail:
        settings = self.get_chat_settings(owner_user_id)
        strategy = variant or "current"
        settings = self._settings_for_eval_variant(settings, strategy)
        settings_snapshot = self._strategy_snapshot(
            settings,
            strategy=strategy,
            experiment_tag=experiment_tag,
        )
        effective_selected_file_ids = (
            list(selected_file_ids)
            if selected_file_ids is not None
            else list(example.selected_file_ids or [])
        )
        if run is None:
            run = eval_store.create_run(
                dataset_id=example.dataset_id,
                example_id=example.id,
                owner_user_id=owner_user_id,
                evaluator_user_id=example.evaluator_user_id,
                question=example.question,
                settings_snapshot=settings_snapshot,
            )
        payload = SendMessagePayload(
            conversationId=f"eval-{run.id}",
            content=example.question,
            settings=settings,
            selectedFileIds=effective_selected_file_ids,
        )
        message_id = f"eval-{run.id}-assistant"
        answer = ""
        references: list[dict[str, Any]] = []
        trace_id = None
        metrics: dict[str, Any]
        error = None
        try:
            result = self.run_rag_once(
                payload,
                example.evaluator_user_id,
                lambda _token: None,
                chat_history=[],
                state=deepcopy(STATE),
                turn_index=0,
                message_id=message_id,
            )
            answer = result.formattedAnswer
            references = [reference.model_dump() for reference in result.references]
            trace_id = result.trace.traceId if result.trace else None
            try:
                index = self._file_index()
            except Exception:
                index = None
            acl_leak = eval_store.detect_acl_leak(
                index=index,
                trace_data=result.traceData,
                evaluator_user_id=example.evaluator_user_id,
            )
            metrics = calculate_metrics(
                EvalMetricInputs(
                    answer=answer,
                    references=references,
                    trace_data=result.traceData,
                    expected_source_ids=list(example.expected_source_ids or []),
                    expected_keywords=list(example.expected_keywords or []),
                    tags=list(example.tags or []),
                ),
                acl_leak_detected=acl_leak,
            )
            self._append_ragas_metrics(
                metrics,
                question=example.question,
                answer=answer,
                trace_data=result.traceData,
                expected_answer=example.expected_answer,
            )
            if variant:
                metrics["evaluation_variant"] = variant
            metrics["strategy_label"] = settings_snapshot["strategy_label"]
            if experiment_tag:
                metrics["experiment_tag"] = experiment_tag
            status = "completed"
        except Exception as exc:
            error = getattr(exc, "detail", None) or str(exc)
            trace = get_trace_by_message(message_id, example.evaluator_user_id)
            trace_data = trace.data if trace is not None else {}
            trace_id = trace.trace_id if trace is not None else None
            metrics = calculate_metrics(
                EvalMetricInputs(
                    answer=answer,
                    references=references,
                    trace_data=trace_data,
                    expected_source_ids=list(example.expected_source_ids or []),
                    expected_keywords=list(example.expected_keywords or []),
                    error=error,
                    tags=list(example.tags or []),
                ),
                acl_leak_detected=False,
            )
            self._append_ragas_metrics(
                metrics,
                question=example.question,
                answer=answer,
                trace_data=trace_data,
                expected_answer=example.expected_answer,
            )
            if variant:
                metrics["evaluation_variant"] = variant
            metrics["strategy_label"] = settings_snapshot["strategy_label"]
            if experiment_tag:
                metrics["experiment_tag"] = experiment_tag
            status = "failed"

        finished = eval_store.finish_run(
            run.id,
            status=status,
            answer=answer,
            references=references,
            metrics=metrics,
            trace_id=trace_id,
            error=error,
        )
        return self._eval_run_detail_to_api(finished)

    def run_eval_example(self, example_id: str, user_id: str) -> RagEvalRunDetail:
        try:
            example = eval_store.require_example(example_id, user_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="样例不存在。")
        return self._run_eval_example_sync(example, user_id)

    def start_eval_example(
        self,
        example_id: str,
        user_id: str,
        selected_file_ids: list[str] | None = None,
        strategy: str = "current",
        experiment_tag: str | None = None,
    ) -> RagEvalRunDetail:
        try:
            example = eval_store.require_example(example_id, user_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="样例不存在。")

        settings = self._settings_for_eval_variant(self.get_chat_settings(user_id), strategy)
        run = eval_store.create_run(
            dataset_id=example.dataset_id,
            example_id=example.id,
            owner_user_id=user_id,
            evaluator_user_id=example.evaluator_user_id,
            question=example.question,
            settings_snapshot=self._strategy_snapshot(
                settings,
                strategy=strategy,
                experiment_tag=experiment_tag,
            ),
        )
        return self._eval_run_detail_to_api(run)

    def start_eval_example_comparison(
        self,
        example_id: str,
        user_id: str,
        selected_file_ids: list[str] | None = None,
        strategies: list[str] | None = None,
        experiment_tag: str | None = None,
    ) -> list[RagEvalRunItem]:
        try:
            example = eval_store.require_example(example_id, user_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="样例不存在。")

        variants = self._normalize_eval_strategies(
            strategies,
            default=list(RAG_EVAL_COMPARISON_VARIANTS),
        )
        base_settings = self.get_chat_settings(user_id)
        runs: list[RagEvalRunItem] = []
        for variant in variants:
            settings = self._settings_for_eval_variant(base_settings, variant)
            run = eval_store.create_run(
                dataset_id=example.dataset_id,
                example_id=example.id,
                owner_user_id=user_id,
                evaluator_user_id=example.evaluator_user_id,
                question=example.question,
                settings_snapshot=self._strategy_snapshot(
                    settings,
                    strategy=variant,
                    experiment_tag=experiment_tag,
                ),
            )
            runs.append(self._eval_run_to_api(run))
        return runs

    def finish_started_eval_example(
        self,
        run_id: str,
        example_id: str,
        owner_user_id: str,
        selected_file_ids: list[str] | None = None,
        variant: str | None = None,
        experiment_tag: str | None = None,
    ) -> None:
        try:
            example = eval_store.require_example(example_id, owner_user_id)
        except KeyError:
            eval_store.finish_run(
                run_id,
                status="failed",
                answer="",
                references=[],
                metrics={"error": "example_not_found"},
                error="样例不存在。",
            )
            return

        try:
            run = eval_store.get_run(run_id, owner_user_id)
            if run is None:
                return
            self._run_eval_example_sync(
                example,
                owner_user_id,
                selected_file_ids=selected_file_ids,
                run=run,
                variant=variant,
                experiment_tag=experiment_tag,
            )
        except Exception as exc:
            metrics = {"error": str(exc)}
            if variant:
                metrics["evaluation_variant"] = variant
                metrics["strategy_label"] = RAG_EVAL_STRATEGY_LABELS.get(
                    variant,
                    variant,
                )
            if experiment_tag:
                metrics["experiment_tag"] = experiment_tag
            eval_store.finish_run(
                run_id,
                status="failed",
                answer="",
                references=[],
                metrics=metrics,
                error=getattr(exc, "detail", None) or str(exc),
            )

    def start_eval_dataset(
        self,
        dataset_id: str,
        user_id: str,
        selected_file_ids: list[str] | None = None,
        strategies: list[str] | None = None,
        experiment_tag: str | None = None,
    ) -> list[RagEvalRunItem]:
        examples = self.list_eval_examples(dataset_id, user_id)
        runs: list[RagEvalRunItem] = []
        base_settings = self.get_chat_settings(user_id)
        variants = self._normalize_eval_strategies(strategies)
        for item in examples:
            example = eval_store.require_example(item.id, user_id)
            for variant in variants:
                settings = self._settings_for_eval_variant(base_settings, variant)
                run = eval_store.create_run(
                    dataset_id=example.dataset_id,
                    example_id=example.id,
                    owner_user_id=user_id,
                    evaluator_user_id=example.evaluator_user_id,
                    question=example.question,
                    settings_snapshot=self._strategy_snapshot(
                        settings,
                        strategy=variant,
                        experiment_tag=experiment_tag,
                    ),
                )
                runs.append(self._eval_run_to_api(run))
        return runs

    def list_eval_runs(
        self,
        user_id: str,
        dataset_id: str | None = None,
        example_id: str | None = None,
        limit: int = 50,
    ) -> list[RagEvalRunItem]:
        runs = eval_store.list_runs(
            owner_user_id=user_id,
            dataset_id=dataset_id,
            example_id=example_id,
            limit=limit,
        )
        return [self._eval_run_to_api(run) for run in runs]

    def get_eval_run_detail(self, run_id: str, user_id: str) -> RagEvalRunDetail:
        run = eval_store.get_run(run_id, user_id)
        if run is None:
            raise HTTPException(status_code=404, detail="评测结果不存在。")
        return self._eval_run_detail_to_api(run)

    async def stream_message(self, payload: SendMessagePayload, user_id: str):
        queue: asyncio.Queue[tuple[str, Any]] = asyncio.Queue()
        loop = asyncio.get_running_loop()

        def emit_token(token: str) -> None:
            loop.call_soon_threadsafe(queue.put_nowait, ("token", token))

        async def produce():
            try:
                result = await asyncio.to_thread(
                    self._run_pipeline_sync,
                    payload,
                    user_id,
                    emit_token,
                )
                await queue.put(("done", result))
            except Exception as exc:
                await queue.put(("error", exc))

        asyncio.create_task(produce())

        while True:
            event, data = await queue.get()
            if event == "token":
                yield _sse("token", {"token": data})
            elif event == "done":
                yield _sse("done", data.model_dump())
                break
            else:
                message = getattr(data, "detail", None) or str(data)
                yield _sse("error", {"message": message})
                break


def _sse(event: str, data: dict | str) -> str:
    payload = data if isinstance(data, str) else json.dumps(data, ensure_ascii=False)
    return f"event: {event}\ndata: {payload}\n\n"


service = ReactApiService()
router = APIRouter(prefix="/api/react", tags=["react-frontend"])


def _upload_indexing_options(
    chunk_size: int | None = None,
    chunk_overlap: int | None = None,
    embedding_model: str | None = None,
    reindex: bool = False,
) -> UploadIndexingOptions:
    return UploadIndexingOptions(
        chunkSize=chunk_size,
        chunkOverlap=chunk_overlap,
        embeddingModel=embedding_model,
        reindex=reindex,
    )


@router.get("/health")
async def health():
    return {
        "status": "ok",
        "mode": "backend",
        "runtime": service.app_runtime is not None,
        "time": _now(),
    }


@router.get("/references/default", response_model=list[ReferenceDocument])
async def get_default_references(request: Request):
    return service.list_references(_require_user_id(request))


@router.get("/conversations", response_model=list[Conversation])
async def list_conversations(request: Request):
    return service.list_conversations(_require_user_id(request))


@router.post("/conversations", response_model=Conversation)
async def create_conversation(request: Request):
    return service.create_conversation(_require_user_id(request))


@router.delete("/conversations/{conversation_id}")
async def delete_conversation(conversation_id: str, request: Request):
    service.delete_conversation(conversation_id, _require_user_id(request))
    return {"ok": True}


@router.patch("/conversations/{conversation_id}", response_model=Conversation)
async def rename_conversation(
    conversation_id: str, payload: RenameConversationPayload, request: Request
):
    return service.rename_conversation(
        conversation_id, payload.title, _require_user_id(request)
    )


@router.get(
    "/conversations/{conversation_id}/messages", response_model=list[ChatMessage]
)
async def list_messages(conversation_id: str, request: Request):
    return service.list_messages(conversation_id, _require_user_id(request))


@router.get(
    "/conversations/{conversation_id}/traces",
    response_model=list[RagTraceSummary],
)
async def list_conversation_trace_runs(conversation_id: str, request: Request):
    return service.list_traces(conversation_id, _require_user_id(request))


@router.get("/traces/{trace_id}", response_model=RagTraceDetail)
async def get_trace_run(trace_id: str, request: Request):
    return service.get_trace_detail(trace_id, _require_user_id(request))


@router.get("/messages/{message_id}/trace", response_model=RagTraceDetail | None)
async def get_assistant_message_trace(message_id: str, request: Request):
    return service.get_message_trace(message_id, _require_user_id(request))


@router.get("/messages/{message_id}/references", response_model=list[ReferenceDocument])
async def get_assistant_message_references(message_id: str, request: Request):
    return service.get_message_references(message_id, _require_user_id(request))


@router.post("/chat/send", response_model=SendMessageResult)
async def send_message(payload: SendMessagePayload, request: Request):
    return await asyncio.to_thread(
        service.send_message,
        payload,
        _require_user_id(request),
    )


@router.post("/chat/stream")
async def stream_message(payload: SendMessagePayload, request: Request):
    user_id = _require_user_id(request)
    return StreamingResponse(
        service.stream_message(payload, user_id),
        media_type="text/event-stream",
    )


@router.get("/eval/datasets", response_model=list[RagEvalDatasetItem])
async def list_eval_datasets(request: Request):
    return service.list_eval_datasets(_require_user_id(request))


@router.post("/eval/datasets", response_model=RagEvalDatasetItem)
async def create_eval_dataset(payload: RagEvalDatasetPayload, request: Request):
    return service.create_eval_dataset(payload, _require_user_id(request))


@router.patch("/eval/datasets/{dataset_id}", response_model=RagEvalDatasetItem)
async def update_eval_dataset(
    dataset_id: str, payload: RagEvalDatasetPayload, request: Request
):
    return service.update_eval_dataset(dataset_id, payload, _require_user_id(request))


@router.delete("/eval/datasets/{dataset_id}")
async def delete_eval_dataset(dataset_id: str, request: Request):
    service.delete_eval_dataset(dataset_id, _require_user_id(request))
    return {"ok": True}


@router.get(
    "/eval/datasets/{dataset_id}/examples",
    response_model=list[RagEvalExampleItem],
)
async def list_eval_examples(dataset_id: str, request: Request):
    return service.list_eval_examples(dataset_id, _require_user_id(request))


@router.post(
    "/eval/datasets/{dataset_id}/examples",
    response_model=RagEvalExampleItem,
)
async def create_eval_example(
    dataset_id: str, payload: RagEvalExamplePayload, request: Request
):
    return service.create_eval_example(dataset_id, payload, _require_user_id(request))


@router.patch("/eval/examples/{example_id}", response_model=RagEvalExampleItem)
async def update_eval_example(
    example_id: str, payload: RagEvalExamplePayload, request: Request
):
    return service.update_eval_example(example_id, payload, _require_user_id(request))


@router.delete("/eval/examples/{example_id}")
async def delete_eval_example(example_id: str, request: Request):
    service.delete_eval_example(example_id, _require_user_id(request))
    return {"ok": True}


@router.post("/eval/examples/{example_id}/run", response_model=RagEvalRunDetail)
async def run_eval_example(
    example_id: str,
    request: Request,
    background_tasks: BackgroundTasks,
    payload: RagEvalRunPayload | None = Body(default=None),
):
    payload = payload or RagEvalRunPayload()
    user_id = _require_user_id(request)
    run = service.start_eval_example(
        example_id,
        user_id,
        selected_file_ids=payload.selectedFileIds,
        experiment_tag=payload.experimentTag,
    )
    background_tasks.add_task(
        service.finish_started_eval_example,
        run.id,
        example_id,
        user_id,
        payload.selectedFileIds,
        "current",
        payload.experimentTag,
    )
    return run


@router.post(
    "/eval/examples/{example_id}/compare",
    response_model=list[RagEvalRunItem],
)
async def compare_eval_example(
    example_id: str,
    request: Request,
    background_tasks: BackgroundTasks,
    payload: RagEvalRunPayload | None = Body(default=None),
):
    payload = payload or RagEvalRunPayload()
    user_id = _require_user_id(request)
    runs = service.start_eval_example_comparison(
        example_id,
        user_id,
        selected_file_ids=payload.selectedFileIds,
        strategies=payload.strategies,
        experiment_tag=payload.experimentTag,
    )
    for run in runs:
        variant = str(run.settingsSnapshot.get("strategy_id") or "current")
        background_tasks.add_task(
            service.finish_started_eval_example,
            run.id,
            example_id,
            user_id,
            payload.selectedFileIds,
            variant,
            payload.experimentTag,
        )
    return runs


@router.post(
    "/eval/datasets/{dataset_id}/run",
    response_model=list[RagEvalRunItem],
)
async def run_eval_dataset(
    dataset_id: str,
    request: Request,
    background_tasks: BackgroundTasks,
    payload: RagEvalRunPayload | None = Body(default=None),
):
    payload = payload or RagEvalRunPayload()
    user_id = _require_user_id(request)
    runs = service.start_eval_dataset(
        dataset_id,
        user_id,
        selected_file_ids=payload.selectedFileIds,
        strategies=payload.strategies,
        experiment_tag=payload.experimentTag,
    )
    for run in runs:
        if not run.exampleId:
            continue
        variant = str(run.settingsSnapshot.get("strategy_id") or "current")
        background_tasks.add_task(
            service.finish_started_eval_example,
            run.id,
            run.exampleId,
            user_id,
            payload.selectedFileIds,
            variant,
            payload.experimentTag,
        )
    return runs


@router.get("/eval/runs", response_model=list[RagEvalRunItem])
async def list_eval_runs(
    request: Request,
    dataset_id: str | None = None,
    example_id: str | None = None,
    limit: int = 50,
):
    return service.list_eval_runs(
        _require_user_id(request),
        dataset_id=dataset_id,
        example_id=example_id,
        limit=limit,
    )


@router.get("/eval/runs/{run_id}", response_model=RagEvalRunDetail)
async def get_eval_run_detail(run_id: str, request: Request):
    return service.get_eval_run_detail(run_id, _require_user_id(request))


@router.post("/files/upload", response_model=list[ReferenceDocument])
async def upload_files(
    request: Request,
    files: list[UploadFile] = File(...),
    directory_id: str | None = None,
    chunk_size: int | None = None,
    chunk_overlap: int | None = None,
    embedding_model: str | None = None,
    reindex: bool = False,
):
    options = _upload_indexing_options(
        chunk_size, chunk_overlap, embedding_model, reindex
    )
    return await service.upload_files(
        files,
        _require_user_id(request),
        directory_id,
        options,
    )


@router.post("/files/{file_id}/reembed", response_model=FileDetail)
async def reembed_file(
    file_id: str,
    request: Request,
    chunk_size: int | None = None,
    chunk_overlap: int | None = None,
    embedding_model: str | None = None,
):
    return await service.reembed_file(
        file_id,
        _require_user_id(request),
        _upload_indexing_options(chunk_size, chunk_overlap, embedding_model, True),
    )


@router.get("/files/workspace", response_model=FileWorkspaceState)
async def list_file_workspace(request: Request):
    return service.list_file_workspace(_require_user_id(request))


@router.patch("/files/{file_id}/permissions", response_model=FileDetail)
async def update_file_permissions(
    file_id: str, payload: UpdateFilePermissionsPayload, request: Request
):
    return service.update_file_permissions(file_id, payload, _require_user_id(request))


@router.post("/files/directories", response_model=FileDirectory)
async def create_file_directory(payload: CreateDirectoryPayload, request: Request):
    return service.create_directory(payload, _require_user_id(request))


@router.patch("/files/directories/{directory_id}", response_model=FileDirectory)
async def update_file_directory(
    directory_id: str, payload: UpdateDirectoryPayload, request: Request
):
    return service.update_directory(directory_id, payload, _require_user_id(request))


@router.delete("/files/directories/{directory_id}")
async def delete_file_directory(directory_id: str, request: Request):
    service.delete_directory(directory_id, _require_user_id(request))
    return {"ok": True}


@router.post("/files/move", response_model=FileWorkspaceState)
async def move_files(payload: MoveFilesPayload, request: Request):
    return service.move_files(payload, _require_user_id(request))


@router.delete("/files/{file_id}")
async def delete_file(file_id: str, request: Request):
    service.delete_file(file_id, _require_user_id(request))
    return {"ok": True}


@router.get("/files/{file_id}", response_model=FileDetail)
async def get_file_detail(
    file_id: str, request: Request, type_filter: str | None = None
):
    return service.get_file_detail(file_id, _require_user_id(request), type_filter)


@router.get("/settings", response_model=ChatSettings, response_model_exclude_none=True)
async def get_chat_settings(request: Request):
    return service.get_chat_settings(_require_user_id(request))


@router.put("/settings", response_model=ChatSettings, response_model_exclude_none=True)
async def save_chat_settings(settings: ChatSettings, request: Request):
    return service.save_chat_settings(settings, _require_user_id(request))


def register_react_api(app: FastAPI, app_runtime: Any | None = None) -> None:
    service.configure(app_runtime)
    app.include_router(router)
