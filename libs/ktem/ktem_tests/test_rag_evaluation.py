from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

from sqlalchemy import Column, DateTime, Integer, JSON, String, create_engine
from sqlalchemy.orm import declarative_base
from sqlmodel import SQLModel

from ktem import react_api
from ktem.evaluation import EvalMetricInputs, calculate_metrics
from ktem.evaluation.models import RagEvalDataset, RagEvalExample, RagEvalRun
from ktem.evaluation.ragas_metrics import extract_ragas_contexts
from ktem.permissions import permission_service
from ktem.permissions.models import SourcePermission
from ktem.trace import RagTraceRecorder, save_trace
from ktem.trace.models import RagTraceRun


def _engine(monkeypatch):
    test_engine = create_engine("sqlite:///:memory:")
    monkeypatch.setattr("ktem.evaluation.service.engine", test_engine)
    monkeypatch.setattr("ktem.trace.trace_service.engine", test_engine)
    monkeypatch.setattr(permission_service, "engine", test_engine)
    monkeypatch.setattr(react_api, "engine", test_engine)
    SQLModel.metadata.create_all(
        test_engine,
        tables=[
            RagEvalDataset.__table__,
            RagEvalExample.__table__,
            RagEvalRun.__table__,
            RagTraceRun.__table__,
            SourcePermission.__table__,
        ],
    )
    return test_engine


def test_create_dataset_and_example(monkeypatch):
    _engine(monkeypatch)
    service = react_api.ReactApiService()

    dataset = service.create_eval_dataset(
        react_api.RagEvalDatasetPayload(name="回归集", tags=["smoke"]),
        "alice",
    )
    example = service.create_eval_example(
        dataset.id,
        react_api.RagEvalExamplePayload(
            question="制度是什么？",
            expectedSourceIds=["doc-1"],
            expectedKeywords=["制度"],
            evaluatorUserId="bob",
            selectedFileIds=["doc-1"],
        ),
        "alice",
    )

    assert dataset.name == "回归集"
    assert example.evaluatorUserId == "bob"
    assert service.list_eval_examples(dataset.id, "alice")[0].expectedSourceIds == [
        "doc-1"
    ]


def test_metric_hit_rates_are_calculated():
    metrics = calculate_metrics(
        EvalMetricInputs(
            answer="本制度要求加密存储。",
            references=[],
            trace_data={
                "context_chunks": [
                    {"source_id": "doc-1"},
                    {"source_id": "doc-3"},
                ],
                "citation_chunks": [{"source_id": "doc-1"}],
                "durations_ms": {"total": 123},
                "tokens": {
                    "prompt_tokens": 10,
                    "completion_tokens": 20,
                    "total_tokens": 30,
                },
            },
            expected_source_ids=["doc-1", "doc-2"],
            expected_keywords=["制度", "权限"],
        )
    )

    assert metrics["expected_source_hit_rate"] == 0.5
    assert metrics["keyword_hit_rate"] == 0.5
    assert metrics["citation_present"] is True
    assert metrics["latency_ms"] == 123


def test_ranking_metrics_are_calculated_for_sorted_expected_source_hit():
    metrics = calculate_metrics(
        EvalMetricInputs(
            answer="命中了正确制度。",
            references=[],
            trace_data={
                "retrieval_params": {"topK": 3},
                "context_chunks": [
                    {"source_id": "doc-0"},
                    {"source_id": "doc-2"},
                    {"source_id": "doc-1"},
                ],
            },
            expected_source_ids=["doc-1", "doc-2"],
            expected_keywords=[],
        )
    )

    assert metrics["hit_at_k"] is True
    assert metrics["hit_k"] == 3
    assert metrics["mrr"] == 0.5
    assert round(metrics["ndcg_at_k"], 3) == 0.693


def test_ranking_metrics_are_zero_when_expected_sources_miss():
    metrics = calculate_metrics(
        EvalMetricInputs(
            answer="没有命中正确制度。",
            references=[],
            trace_data={
                "context_chunks": [
                    {"source_id": "doc-0"},
                    {"source_id": "doc-2"},
                ],
            },
            expected_source_ids=["doc-1"],
            expected_keywords=[],
        )
    )

    assert metrics["hit_at_k"] is False
    assert metrics["mrr"] == 0
    assert metrics["ndcg_at_k"] == 0


def test_source_metrics_match_file_name_aliases_from_trace_chunks():
    metrics = calculate_metrics(
        EvalMetricInputs(
            answer="命中了正确制度。",
            references=[],
            trace_data={
                "retrieval_params": {"topK": 2},
                "context_chunks": [
                    {
                        "source_id": "uuid-1",
                        "source_name": "long_policy_mixed.md",
                        "metadata": {"file_id": "uuid-1"},
                    },
                    {"source_id": "uuid-2", "source_name": "other.md"},
                ],
                "citation_chunks": [
                    {"source_id": "uuid-1", "source_name": "long_policy_mixed.md"},
                    {"source_id": "uuid-2", "source_name": "other.md"},
                ],
            },
            expected_source_ids=["long_policy_mixed.md"],
            expected_keywords=[],
        )
    )

    assert metrics["expected_source_hit_rate"] == 1
    assert metrics["hit_at_k"] is True
    assert metrics["mrr"] == 1
    assert metrics["ndcg_at_k"] == 1
    assert metrics["citation_support_rate"] == 0.5


def test_metrics_report_summary_and_detail_layer_usage():
    metrics = calculate_metrics(
        EvalMetricInputs(
            answer="文档整体涵盖假期和权限。",
            references=[],
            trace_data={
                "context_chunks": [
                    {
                        "source_id": "doc-1",
                        "type": "summary",
                        "retrieval_layer": "summary_layer",
                    },
                    {
                        "source_id": "doc-1",
                        "type": "text",
                        "retrieval_layer": "detail_layer",
                    },
                ],
            },
            expected_source_ids=["doc-1"],
            expected_keywords=[],
            tags=["global_summary"],
        )
    )

    assert metrics["summary_layer_context_count"] == 1
    assert metrics["detail_layer_context_count"] == 1
    assert metrics["used_summary_layer"] is True


def test_metrics_report_multihop_graph_readiness():
    metrics = calculate_metrics(
        EvalMetricInputs(
            answer="张三属于销售团队，销售团队适用报销制度A。",
            references=[],
            trace_data={
                "context_chunks": [
                    {
                        "source_id": "policy",
                        "source_name": "expense.md",
                        "type": "graph",
                        "retrieval_layer": "graph_layer",
                    }
                ],
                "graph_rag": {
                    "enabled": True,
                    "entities": [
                        {"entity": "张三"},
                        {"entity": "销售团队"},
                        {"entity": "报销制度A"},
                    ],
                    "relationships": [
                        {"source": "张三", "target": "销售团队"},
                        {"source": "销售团队", "target": "报销制度A"},
                    ],
                    "paths": [
                        {"nodes": ["张三", "销售团队", "报销制度A"]},
                    ],
                    "answer_fragments": [
                        "张三通过销售团队关联到报销制度A。",
                    ],
                },
            },
            expected_source_ids=["policy"],
            expected_keywords=["销售团队", "报销制度A"],
            tags=["multi_hop"],
        )
    )

    assert metrics["graph_layer_context_count"] == 1
    assert metrics["graph_rag_enabled"] is True
    assert metrics["graph_entity_count"] == 3
    assert metrics["graph_relationship_count"] == 2
    assert metrics["graph_path_count"] == 1
    assert metrics["graph_answer_fragment_count"] == 1
    assert metrics["multi_hop_graph_ready"] is True


def test_no_answer_refusal_accuracy_is_true_for_refusal_answer():
    metrics = calculate_metrics(
        EvalMetricInputs(
            answer="根据当前信息不足，无法回答该问题。",
            references=[],
            trace_data={},
            expected_source_ids=[],
            expected_keywords=[],
            tags=["no_answer"],
        )
    )

    assert metrics["refusal_accuracy"] is True
    assert metrics["refusal_detected"] is True
    assert metrics["expected_refusal"] is True


def test_no_answer_refusal_accuracy_is_false_for_forced_answer():
    metrics = calculate_metrics(
        EvalMetricInputs(
            answer="公司明年计划上市。",
            references=[],
            trace_data={},
            expected_source_ids=[],
            expected_keywords=[],
            tags=["no_answer"],
        )
    )

    assert metrics["refusal_accuracy"] is False


def test_citation_support_rate_counts_expected_source_citations():
    metrics = calculate_metrics(
        EvalMetricInputs(
            answer="答案引用了部分正确来源。",
            references=[],
            trace_data={
                "citation_chunks": [
                    {"source_id": "doc-1"},
                    {"source_id": "doc-9"},
                    {"source_id": "doc-2"},
                ],
            },
            expected_source_ids=["doc-1", "doc-2"],
            expected_keywords=[],
        )
    )

    assert metrics["citation_support_rate"] == 2 / 3


def test_metrics_include_answer_verification_online_indicators():
    metrics = calculate_metrics(
        EvalMetricInputs(
            answer="上海住宿标准为 500 元。",
            references=[],
            trace_data={
                "answer_verification": {
                    "evidence_coverage": 0.5,
                    "supported_count": 1,
                    "unsupported_count": 1,
                    "insufficient_count": 2,
                    "final_action": "refused",
                    "retry": {"triggered": True},
                }
            },
            expected_source_ids=[],
            expected_keywords=[],
        )
    )

    assert metrics["evidence_coverage"] == 0.5
    assert metrics["evidence_supported_count"] == 1
    assert metrics["evidence_unsupported_count"] == 1
    assert metrics["evidence_insufficient_count"] == 2
    assert metrics["hallucination_risk_count"] == 3
    assert metrics["answer_verification_action"] == "refused"
    assert metrics["answer_verification_retry"] is True


def test_run_example_generates_result_and_trace(monkeypatch):
    _engine(monkeypatch)
    service = react_api.ReactApiService()
    monkeypatch.setattr(
        service, "get_chat_settings", lambda user_id: react_api.ChatSettings()
    )
    monkeypatch.setattr(service, "_file_index", lambda: None)

    def fake_run(payload, user_id, emit_token, **kwargs):
        recorder = RagTraceRecorder(
            conversation_id=payload.conversationId,
            user_id=user_id,
            question=payload.content,
            selected_file_ids=payload.selectedFileIds,
            retrieval_params={"topK": 10},
            effective_principal={"principal": {"type": "user", "id": user_id}},
        )
        recorder.set_message(kwargs["message_id"])
        recorder.record_context(
            [
                SimpleNamespace(
                    doc_id="chunk-1",
                    text="allowed text",
                    metadata={"file_id": "doc-1", "file_name": "policy.pdf"},
                )
            ]
        )
        trace_row = save_trace(recorder.finish("completed"))
        return react_api.RagPipelineRunResult(
            answerText="allowed text",
            formattedAnswer="allowed text",
            retrievalContent="",
            references=[],
            trace=service._trace_summary_to_api(trace_row),
            traceData=trace_row.data,
            messageId=kwargs["message_id"],
        )

    monkeypatch.setattr(service, "run_rag_once", fake_run)
    dataset = service.create_eval_dataset(
        react_api.RagEvalDatasetPayload(name="回归集"),
        "alice",
    )
    example = service.create_eval_example(
        dataset.id,
        react_api.RagEvalExamplePayload(
            question="制度是什么？",
            expectedSourceIds=["doc-1"],
            expectedKeywords=["allowed"],
            evaluatorUserId="bob",
        ),
        "alice",
    )

    run = service.run_eval_example(example.id, "alice")

    assert run.status == "completed"
    assert run.traceId is not None
    assert run.trace is not None
    assert run.metrics["expected_source_hit_rate"] == 1
    assert run.metrics["keyword_hit_rate"] == 1
    assert run.metrics["ragas_enabled"] is False


def test_strategy_snapshot_records_retrieval_controls():
    service = react_api.ReactApiService()
    settings = react_api.ChatSettings()
    settings.retrieval.topK = 7
    settings.retrieval.firstRoundMultiplier = 4
    settings.retrieval.retrievalMode = "hybrid"
    settings.retrieval.rerank = True
    settings.retrieval.mmr = True

    snapshot = service._strategy_snapshot(
        settings,
        strategy="hybrid_rrf+rerank+mmr",
        experiment_tag="resume-exp",
    )

    retrieval = snapshot["retrieval_strategy"]
    assert snapshot["strategy_id"] == "hybrid_rrf+rerank+mmr"
    assert snapshot["experiment_tag"] == "resume-exp"
    assert retrieval["retrieval_mode"] == "hybrid"
    assert retrieval["enhancement"] == "none"
    assert retrieval["topK"] == 7
    assert retrieval["firstRoundMultiplier"] == 4
    assert retrieval["rerank"] is True
    assert retrieval["mmr"] is True
    assert retrieval["rrf"] == {"enabled": True, "k": 60}
    assert retrieval["query_rewrite"] == {"enabled": False, "implemented": True}
    assert retrieval["hyde"] == {"enabled": False, "implemented": True}
    assert retrieval["rag_fusion"] == {
        "enabled": False,
        "implemented": True,
        "query_count": 0,
    }


def test_graph_eval_variant_enables_lightrag_settings():
    service = react_api.ReactApiService()
    settings = service._settings_for_eval_variant(react_api.ChatSettings(), "graph")
    snapshot = service._strategy_snapshot(settings, strategy="graph")

    assert settings.retrieval.graphEnabled is True
    assert settings.retrieval.graphProvider == "lightrag"
    assert snapshot["retrieval_strategy"]["graph_rag"] == {
        "enabled": True,
        "provider": "lightrag",
        "search_type": "local",
        "implemented": True,
    }


def test_eval_variants_compare_normal_rewrite_hyde_and_fusion():
    service = react_api.ReactApiService()
    settings = react_api.ChatSettings()
    settings.retrieval.retrievalMode = "hybrid"
    settings.retrieval.enhancement = "hyde"

    normal = service._settings_for_eval_variant(settings, "normal_query")
    rewrite = service._settings_for_eval_variant(settings, "rewrite")
    hyde = service._settings_for_eval_variant(settings, "hyde")
    fusion = service._settings_for_eval_variant(settings, "fusion")

    assert normal.retrieval.retrievalMode == "hybrid"
    assert normal.retrieval.enhancement == "none"
    assert rewrite.retrieval.enhancement == "rewrite"
    assert hyde.retrieval.enhancement == "hyde"
    assert fusion.retrieval.enhancement == "fusion"
    assert fusion.retrieval.retrievalMode == "hybrid"


def test_start_eval_dataset_creates_runs_for_each_strategy(monkeypatch):
    _engine(monkeypatch)
    service = react_api.ReactApiService()
    monkeypatch.setattr(
        service, "get_chat_settings", lambda user_id: react_api.ChatSettings()
    )
    dataset = service.create_eval_dataset(
        react_api.RagEvalDatasetPayload(name="策略对比集"),
        "alice",
    )
    first = service.create_eval_example(
        dataset.id,
        react_api.RagEvalExamplePayload(question="问题 A", tags=["exact_id"]),
        "alice",
    )
    second = service.create_eval_example(
        dataset.id,
        react_api.RagEvalExamplePayload(question="问题 B", tags=["permission"]),
        "alice",
    )

    runs = service.start_eval_dataset(
        dataset.id,
        "alice",
        strategies=["vector", "hybrid_rrf"],
        experiment_tag="batch-exp",
    )

    assert len(runs) == 4
    assert {run.exampleId for run in runs} == {first.id, second.id}
    assert {run.settingsSnapshot["strategy_id"] for run in runs} == {
        "vector",
        "hybrid_rrf",
    }
    assert {run.settingsSnapshot["experiment_tag"] for run in runs} == {"batch-exp"}


def test_dataset_summary_reports_permission_leak_rate(monkeypatch):
    _engine(monkeypatch)
    service = react_api.ReactApiService()
    dataset = service.create_eval_dataset(
        react_api.RagEvalDatasetPayload(name="ACL 回归集"),
        "alice",
    )
    leaked_run = react_api.eval_store.create_run(
        dataset_id=dataset.id,
        example_id=None,
        owner_user_id="alice",
        evaluator_user_id="bob",
        question="leaked?",
    )
    clean_run = react_api.eval_store.create_run(
        dataset_id=dataset.id,
        example_id=None,
        owner_user_id="alice",
        evaluator_user_id="bob",
        question="clean?",
    )
    failed_run = react_api.eval_store.create_run(
        dataset_id=dataset.id,
        example_id=None,
        owner_user_id="alice",
        evaluator_user_id="bob",
        question="failed?",
    )
    react_api.eval_store.finish_run(
        leaked_run.id,
        status="completed",
        answer="",
        references=[],
        metrics={"acl_leak_detected": True},
    )
    react_api.eval_store.finish_run(
        clean_run.id,
        status="completed",
        answer="",
        references=[],
        metrics={"acl_leak_detected": False},
    )
    react_api.eval_store.finish_run(
        failed_run.id,
        status="failed",
        answer="",
        references=[],
        metrics={"acl_leak_detected": True},
    )

    summary = service.list_eval_datasets("alice")[0]

    assert summary.runCount == 3
    assert summary.permissionLeakCount == 1
    assert summary.permissionLeakTotal == 2
    assert summary.permissionLeakRate == 0.5


def test_ragas_contexts_extract_from_trace_chunks():
    assert extract_ragas_contexts(
        {
            "context_chunks": [
                {"text": "chunk from text"},
                {"content": "chunk from content"},
                {"metadata": {"page_content": "chunk from metadata"}},
                {"text": "chunk from text"},
            ],
            "candidate_chunks_after_rerank": [{"text": "fallback chunk"}],
        }
    ) == [
        "chunk from text",
        "chunk from content",
        "chunk from metadata",
    ]

    assert extract_ragas_contexts(
        {
            "context_chunks": [],
            "candidate_chunks_after_rerank": [
                {"page_content": "reranked page content"},
                {"metadata": {"content": "reranked metadata content"}},
            ],
        }
    ) == ["reranked page content", "reranked metadata content"]

    assert extract_ragas_contexts(None) == []


def test_run_example_adds_mocked_ragas_metrics(monkeypatch):
    _engine(monkeypatch)
    service = react_api.ReactApiService()
    monkeypatch.setenv("KH_RAGAS_EVAL_ENABLED", "true")
    monkeypatch.setattr(
        service, "get_chat_settings", lambda user_id: react_api.ChatSettings()
    )
    monkeypatch.setattr(service, "_file_index", lambda: None)

    observed = {}

    def fake_calculate_ragas_metrics(question, answer, contexts, ground_truth=None):
        observed["question"] = question
        observed["answer"] = answer
        observed["contexts"] = contexts
        observed["ground_truth"] = ground_truth
        return {
            "ragas_enabled": True,
            "faithfulness": 0.91,
            "answer_relevancy": 0.82,
            "context_precision": 0.73,
            "context_recall": 0.64,
        }

    monkeypatch.setattr(
        react_api.ragas_metrics,
        "calculate_ragas_metrics",
        fake_calculate_ragas_metrics,
    )

    def fake_run(payload, user_id, emit_token, **kwargs):
        recorder = RagTraceRecorder(
            conversation_id=payload.conversationId,
            user_id=user_id,
            question=payload.content,
            selected_file_ids=payload.selectedFileIds,
            retrieval_params={"topK": 10},
            effective_principal={"principal": {"type": "user", "id": user_id}},
        )
        recorder.set_message(kwargs["message_id"])
        recorder.record_context(
            [
                SimpleNamespace(
                    doc_id="chunk-1",
                    text="semantic support context",
                    metadata={"file_id": "doc-1", "file_name": "policy.pdf"},
                )
            ]
        )
        trace_row = save_trace(recorder.finish("completed"))
        return react_api.RagPipelineRunResult(
            answerText="semantic answer",
            formattedAnswer="semantic answer",
            retrievalContent="",
            references=[],
            trace=service._trace_summary_to_api(trace_row),
            traceData=trace_row.data,
            messageId=kwargs["message_id"],
        )

    monkeypatch.setattr(service, "run_rag_once", fake_run)
    dataset = service.create_eval_dataset(
        react_api.RagEvalDatasetPayload(name="RAGAS 回归集"),
        "alice",
    )
    example = service.create_eval_example(
        dataset.id,
        react_api.RagEvalExamplePayload(
            question="语义指标是什么？",
            expectedAnswer="semantic ground truth",
            expectedSourceIds=["doc-1"],
            expectedKeywords=["semantic"],
            evaluatorUserId="bob",
        ),
        "alice",
    )

    run = service.run_eval_example(example.id, "alice")

    assert run.status == "completed"
    assert observed == {
        "question": "语义指标是什么？",
        "answer": "semantic answer",
        "contexts": ["semantic support context"],
        "ground_truth": "semantic ground truth",
    }
    assert run.metrics["ragas_enabled"] is True
    assert run.metrics["ragas_faithfulness"] == 0.91
    assert run.metrics["ragas_answer_relevancy"] == 0.82
    assert run.metrics["ragas_context_precision"] == 0.73
    assert run.metrics["ragas_context_recall"] == 0.64


def test_evaluator_acl_detects_hidden_source_and_result_omits_hidden_text(monkeypatch):
    test_engine = _engine(monkeypatch)
    Base = declarative_base()

    class Source(Base):
        __tablename__ = "eval_source"

        id = Column(String, primary_key=True)
        name = Column(String)
        path = Column(String)
        size = Column(Integer, default=0)
        date_created = Column(DateTime, default=datetime.utcnow)
        user = Column(String, default="")
        note = Column(JSON, default={})

    Base.metadata.create_all(test_engine)
    index = SimpleNamespace(
        id=1,
        config={"private": True},
        _resources={"Source": Source},
        Source=Source,
    )
    with permission_service.Session(test_engine) as session:
        session.add_all(
            [
                Source(id="owned", name="owned.pdf", user="bob", note={}),
                Source(id="hidden", name="hidden.pdf", user="alice", note={}),
            ]
        )
        session.commit()
    permission_service.ensure_default_acl(
        index, Source(id="owned", name="owned.pdf", user="bob")
    )
    permission_service.ensure_default_acl(
        index, Source(id="hidden", name="hidden.pdf", user="alice")
    )

    allowed_alias_leak = react_api.eval_store.detect_acl_leak(
        index=index,
        trace_data={
            "context_chunks": [
                {
                    "source_id": "owned",
                    "source_name": "hidden.pdf",
                    "metadata": {"file_id": "owned", "file_name": "hidden.pdf"},
                    "text": "allowed text",
                }
            ]
        },
        evaluator_user_id="bob",
    )
    leak = react_api.eval_store.detect_acl_leak(
        index=index,
        trace_data={
            "context_chunks": [{"source_id": "hidden", "text": "secret hidden text"}]
        },
        evaluator_user_id="bob",
    )
    metrics = calculate_metrics(
        EvalMetricInputs(
            answer="allowed summary",
            references=[],
            trace_data={
                "context_chunks": [{"source_id": "owned", "text": "allowed text"}]
            },
            expected_source_ids=["hidden"],
            expected_keywords=["allowed"],
        ),
        acl_leak_detected=leak,
    )

    assert allowed_alias_leak is False
    assert leak is True
    assert metrics["acl_leak_detected"] is True
    assert "secret hidden text" not in str(
        {"answer": "allowed summary", "metrics": metrics}
    )
