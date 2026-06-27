from __future__ import annotations

import re
from hashlib import sha256
from typing import Any, Generator
from uuid import uuid5, NAMESPACE_URL

from ktem.db.engine import engine
from ktem.embeddings.manager import embedding_models_manager
from sqlalchemy.orm import Session

from kotaemon.base import Document, Param, RetrievedDocument

from ..pipelines import BaseFileIndexRetriever, IndexDocumentPipeline


GRAPH_RELATION_TYPE = "graph"
_ID_PATTERN = re.compile(r"\b[A-Z]{2,5}-(?:20\d{2}-)?\d{3,4}\b")
_PERSON_PATTERN = re.compile(r"[\u4e00-\u9fff]{2,4}")
_AMOUNT_PATTERN = re.compile(r"\d{1,3}(?:,\d{3})*(?:\.\d+)?\s*元")
_STATUS_PATTERN = re.compile(r"(已退回|已完成|已关闭|审批中|待确认|未执行|未触发回滚|已回滚)")


def graph_provider_name(index_settings: dict[str, Any]) -> str:
    provider = str(index_settings.get("GRAPH_RAG_PROVIDER") or "lightrag").lower()
    if provider in {"nano", "nanographrag", "nano_graphrag"}:
        return "nano"
    return "lightrag"


def collection_graph_id(index_id: int | str) -> str:
    return str(uuid5(NAMESPACE_URL, f"securerag:file-index:{index_id}:graph"))


def _path_records(relationships: list[dict[str, Any]]) -> list[dict[str, Any]]:
    paths = []
    for rel in relationships:
        source = rel.get("source") or rel.get("src_id") or rel.get("source_id")
        target = rel.get("target") or rel.get("tgt_id") or rel.get("target_id")
        if source and target:
            paths.append(
                {
                    "nodes": [str(source), str(target)],
                    "description": str(rel.get("description") or ""),
                    "weight": rel.get("weight"),
                }
            )
    return paths


def graph_trace_payload(
    *,
    provider: str,
    search_type: str,
    graph_id: str | None,
    entities: Any = None,
    relationships: Any = None,
    sources: Any = None,
    answer_fragment: str | None = None,
) -> dict[str, Any]:
    def frame_to_records(frame: Any, fields: list[str]) -> list[dict[str, Any]]:
        if frame is None:
            return []
        try:
            records = frame.to_dict("records")
        except Exception:
            records = frame if isinstance(frame, list) else []
        output = []
        for record in records or []:
            if not isinstance(record, dict):
                continue
            output.append(
                {field: record.get(field) for field in fields if field in record}
            )
        return output

    entity_records = frame_to_records(
        entities,
        ["entity", "entity_name", "name", "type", "description", "rank"],
    )
    relationship_records = frame_to_records(
        relationships,
        ["source", "target", "description", "keywords", "weight", "rank"],
    )
    source_records = frame_to_records(sources, ["id", "content"])
    return {
        "provider": provider,
        "enabled": True,
        "search_type": search_type,
        "graph_id": graph_id,
        "entities": entity_records,
        "relationships": relationship_records,
        "paths": _path_records(relationship_records),
        "sources": source_records,
        "answer_fragments": [answer_fragment] if answer_fragment else [],
    }


def _trim_text(text: str, limit: int = 180) -> str:
    compact = " ".join(str(text or "").split())
    if len(compact) <= limit:
        return compact
    return f"{compact[:limit].rstrip()}..."


def _query_terms(query: str) -> list[str]:
    terms = [term.upper() for term in _ID_PATTERN.findall(query or "")]
    for token in re.findall(r"[\u4e00-\u9fff]{2,}|[A-Za-z0-9_-]{2,}", query or ""):
        upper = token.upper()
        if upper not in terms:
            terms.append(upper)
    return terms


def _score_chunk_for_query(text: str, query_terms: list[str]) -> int:
    upper_text = text.upper()
    score = 0
    for term in query_terms:
        if term and term in upper_text:
            score += 3 if _ID_PATTERN.fullmatch(term) else 1
    return score


def _fallback_graph_from_chunks(
    query: str,
    chunks: list[Document],
    *,
    graph_id: str,
    provider: str,
    search_type: str,
) -> tuple[dict[str, Any], list[RetrievedDocument]]:
    query_terms = _query_terms(query)
    scored_chunks = []
    for chunk in chunks:
        text = str(getattr(chunk, "text", "") or "")
        if not text.strip():
            continue
        score = _score_chunk_for_query(text, query_terms)
        if score:
            scored_chunks.append((score, chunk))
    if not scored_chunks:
        scored_chunks = [(0, chunk) for chunk in chunks[:5]]
    scored_chunks.sort(key=lambda item: item[0], reverse=True)

    entity_map: dict[str, dict[str, Any]] = {}
    relationship_map: dict[tuple[str, str, str], dict[str, Any]] = {}
    source_records: list[dict[str, Any]] = []

    def add_entity(name: str, entity_type: str, description: str) -> None:
        name = str(name or "").strip()
        if not name:
            return
        entity_map.setdefault(
            name,
            {
                "entity": name,
                "type": entity_type,
                "description": description,
                "rank": len(entity_map) + 1,
            },
        )

    def add_relation(
        source: str,
        target: str,
        description: str,
        keywords: str,
        *,
        weight: int = 1,
    ) -> None:
        source = str(source or "").strip()
        target = str(target or "").strip()
        if not source or not target:
            return
        key = (source, target, keywords)
        relationship_map.setdefault(
            key,
            {
                "source": source,
                "target": target,
                "description": description,
                "keywords": keywords,
                "weight": weight,
                "rank": len(relationship_map) + 1,
            },
        )

    for _, chunk in scored_chunks[:8]:
        text = str(getattr(chunk, "text", "") or "")
        source_records.append(
            {
                "id": str(getattr(chunk, "doc_id", "") or len(source_records)),
                "content": _trim_text(text, 420),
            }
        )
        ids = list(dict.fromkeys(_ID_PATTERN.findall(text)))
        amounts = list(dict.fromkeys(_AMOUNT_PATTERN.findall(text)))
        statuses = list(dict.fromkeys(_STATUS_PATTERN.findall(text)))
        reason_match = re.search(r"退回原因(?:是|为|：|:)?([^。\n|]+)", text)
        names = [
            name
            for name in dict.fromkeys(_PERSON_PATTERN.findall(text))
            if name
            not in {
                "编号",
                "事项",
                "申请人",
                "审批人",
                "状态",
                "金额",
                "原因",
                "记录",
                "系统",
                "检索",
            }
        ][:4]

        for item_id in ids:
            add_entity(item_id, "编号", _trim_text(text, 120))
        for amount in amounts:
            add_entity(amount, "金额", f"金额字段：{amount}")
        for status in statuses:
            add_entity(status, "状态", f"状态字段：{status}")
        for name in names:
            add_entity(name, "人员", f"人员字段：{name}")

        primary = ids[0] if ids else None
        if primary:
            for amount in amounts[:2]:
                add_relation(primary, amount, f"{primary} 的金额为 {amount}", "金额", weight=8)
            for status in statuses[:3]:
                add_relation(primary, status, f"{primary} 的状态为 {status}", "状态", weight=7)
            if reason_match:
                reason = _trim_text(reason_match.group(1), 80)
                add_entity(reason, "原因", f"退回原因：{reason}")
                add_relation(
                    primary,
                    reason,
                    f"{primary} 的退回原因是 {reason}",
                    "退回原因",
                    weight=9,
                )
            for related_id in ids[1:4]:
                add_relation(
                    primary,
                    related_id,
                    f"{primary} 与 {related_id} 在同一证据片段中出现",
                    "关联编号",
                    weight=6,
                )
            for name in names[:3]:
                add_relation(
                    name,
                    primary,
                    f"{name} 与 {primary} 相关",
                    "人员关联",
                    weight=5,
                )

    entities = list(entity_map.values())[:12]
    relationships = list(relationship_map.values())[:12]
    if not entities and not relationships:
        return {}, []

    payload = graph_trace_payload(
        provider=f"{provider}-fallback",
        search_type=search_type,
        graph_id=graph_id,
        entities=entities,
        relationships=relationships,
        sources=source_records[:5],
    )
    docs = [
        RetrievedDocument(
            text=(
                f"GraphRAG fallback evidence: {len(entities)} entities, "
                f"{len(relationships)} relationships."
            ),
            metadata={
                "file_name": "GraphRAG 图谱证据",
                "type": "graph",
                "retrieval_channel": "graph",
                "graph_id": graph_id,
                "graph_provider": f"{provider}-fallback",
                "llm_trulens_score": 1.0,
            },
            score=1.0,
        )
    ]
    return payload, docs


class ProductGraphIndexingMixin:
    graph_provider: str = Param("lightrag", help="GraphRAG provider")
    collection_graph_id: str = Param("", help="Collection-wide graph id")
    prompts: dict[str, str] = Param({}, help="GraphRAG prompt overrides")
    index_batch_size: int = Param(5, help="GraphRAG indexing batch size")

    def _graph_pipeline(self):
        provider = graph_provider_name({"GRAPH_RAG_PROVIDER": self.graph_provider})
        if provider == "nano":
            from .nano_pipelines import NanoGraphRAGIndexingPipeline

            return NanoGraphRAGIndexingPipeline()
        from .lightrag_pipelines import LightRAGIndexingPipeline

        return LightRAGIndexingPipeline()

    def _store_file_id_with_graph_id(self, file_ids: list[str | None]) -> str:
        graph_id = self.collection_graph_id or collection_graph_id(self.index_id)
        with Session(engine) as session:
            for file_id in file_ids:
                if not file_id:
                    continue
                existing = (
                    session.query(self.Index)
                    .filter(
                        self.Index.source_id == file_id,
                        self.Index.target_id == graph_id,
                        self.Index.relation_type == GRAPH_RELATION_TYPE,
                    )
                    .first()
                )
                if existing:
                    continue
                session.add(
                    self.Index(
                        source_id=file_id,
                        target_id=graph_id,
                        relation_type=GRAPH_RELATION_TYPE,
                    )
                )
            session.commit()
        return graph_id

    def index_graph_for_docs(
        self,
        file_ids: list[str | None],
        docs: list[Document],
    ) -> Generator[Document, None, None]:
        graph_docs = [
            doc
            for doc in docs
            if (doc.metadata or {}).get("type", "text") == "text"
            and str(getattr(doc, "text", "") or "").strip()
        ]
        if not graph_docs:
            return
        graph_pipeline = self._graph_pipeline()
        for name in (
            "Index",
            "Source",
            "VS",
            "DS",
            "FSPath",
            "PermissionService",
            "index_id",
            "user_id",
            "private",
        ):
            if hasattr(self, name):
                setattr(graph_pipeline, name, getattr(self, name))
        graph_pipeline.collection_graph_id = (
            self.collection_graph_id or collection_graph_id(self.index_id)
        )
        graph_pipeline.prompts = self.prompts
        graph_pipeline.index_batch_size = self.index_batch_size
        graph_id = self._store_file_id_with_graph_id(file_ids)
        try:
            yield from graph_pipeline.call_graphrag_index(graph_id, graph_docs)
        except Exception as exc:
            yield Document(
                channel="debug",
                text=f"[GraphRAG] Index skipped: {type(exc).__name__}: {exc}",
            )


class ProductGraphIndexingPipeline(ProductGraphIndexingMixin, IndexDocumentPipeline):
    @classmethod
    def get_pipeline(cls, user_settings, index_settings):
        use_quick_index_mode = user_settings.get("quick_index_mode", False)
        return cls(
            embedding=embedding_models_manager[
                index_settings.get(
                    "embedding", embedding_models_manager.get_default_name()
                )
            ],
            run_embedding_in_thread=use_quick_index_mode,
            reader_mode=user_settings.get("reader_mode", "default"),
            graph_provider=graph_provider_name(index_settings),
            index_batch_size=int(user_settings.get("graph_batch_size", 5) or 5),
        )

    def stream(self, file_paths, reindex: bool = False, **kwargs):
        file_ids, errors, all_docs = yield from super().stream(
            file_paths, reindex=reindex, **kwargs
        )
        self.collection_graph_id = collection_graph_id(self.index_id)
        yield from self.index_graph_for_docs(file_ids, all_docs)
        return file_ids, errors, all_docs


class ProductGraphRetriever(BaseFileIndexRetriever):
    file_ids: list[str] = []
    provider: str = "lightrag"
    search_type: str = "local"

    def _graph_ids(self) -> list[str]:
        allowed_file_ids = self.PermissionService.filter_source_ids(
            self, self.file_ids or [], self.user_id
        )
        self._allowed_file_ids = allowed_file_ids
        if not allowed_file_ids:
            return []
        from ktem.db.engine import engine

        with Session(engine) as session:
            rows = (
                session.query(self.Index.target_id)
                .filter(self.Index.source_id.in_(allowed_file_ids))
                .filter(self.Index.relation_type == GRAPH_RELATION_TYPE)
                .all()
            )
        graph_ids = []
        for row in rows:
            graph_id = row[0]
            if graph_id and graph_id not in graph_ids:
                graph_ids.append(graph_id)
        return graph_ids

    def _pipeline(self, graph_id: str):
        if self.provider == "nano":
            from .nano_pipelines import NanoGraphRAGRetrieverPipeline

            pipeline = NanoGraphRAGRetrieverPipeline()
        else:
            from .lightrag_pipelines import LightRAGRetrieverPipeline

            pipeline = LightRAGRetrieverPipeline()
        pipeline.Index = self.Index
        pipeline.file_ids = getattr(self, "_allowed_file_ids", self.file_ids)
        pipeline.search_type = self.search_type
        pipeline._product_graph_id = graph_id
        return pipeline

    def _allowed_chunks(self, limit: int = 80) -> list[Document]:
        allowed_file_ids = getattr(self, "_allowed_file_ids", self.file_ids)
        if not allowed_file_ids:
            return []
        with Session(engine) as session:
            rows = (
                session.query(self.Index.target_id)
                .filter(self.Index.source_id.in_(allowed_file_ids))
                .filter(self.Index.relation_type == "document")
                .limit(limit)
                .all()
            )
        chunk_ids = [row[0] for row in rows if row[0]]
        if not chunk_ids:
            return []
        try:
            return list(self.DS.get(chunk_ids))
        except Exception:
            return []

    def _fallback_docs(self, text: str, graph_id: str) -> list[RetrievedDocument]:
        payload, docs = _fallback_graph_from_chunks(
            text,
            self._allowed_chunks(),
            graph_id=graph_id,
            provider=self.provider,
            search_type=self.search_type,
        )
        if payload:
            try:
                from ktem.trace import get_active_recorder

                recorder = get_active_recorder()
            except Exception:
                recorder = None
            if recorder:
                recorder.record_graph_retrieval(payload)
        return docs

    def run(self, text: str, **kwargs) -> list[RetrievedDocument]:
        docs: list[RetrievedDocument] = []
        for graph_id in self._graph_ids():
            pipeline = self._pipeline(graph_id)
            try:
                graph_docs = pipeline.run(text)
            except Exception:
                graph_docs = []
            if not graph_docs:
                graph_docs = self._fallback_docs(text, graph_id)
            for doc in graph_docs:
                metadata = dict(getattr(doc, "metadata", {}) or {})
                metadata.setdefault(
                    "file_id",
                    ",".join(getattr(self, "_allowed_file_ids", self.file_ids)),
                )
                metadata.setdefault("file_name", "GraphRAG")
                if metadata.get("type") != "plot":
                    metadata["type"] = "graph"
                metadata["retrieval_channel"] = "graph"
                metadata["graph_id"] = graph_id
                metadata["graph_provider"] = self.provider
                doc.metadata = metadata
                if not getattr(doc, "doc_id", None):
                    digest = sha256(
                        f"{graph_id}:{len(docs)}:{getattr(doc, 'text', '')}".encode()
                    ).hexdigest()[:16]
                    doc.doc_id = f"graph-{digest}"
                docs.append(doc)
        return docs
