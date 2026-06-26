from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

from sqlalchemy import Column, DateTime, Integer, JSON, String, create_engine
from sqlalchemy.orm import declarative_base
from sqlmodel import SQLModel

from ktem.index.file.pipelines import DocumentRetrievalPipeline
from ktem.index.file.pipelines import IndexPipeline
from ktem.evidence import assess_answer_support, decide_evidence_gate
from ktem.permissions.models import SourcePermission
from ktem.permissions import permission_service
from ktem.reasoning.prompt_optimization.rag_fusion import DEFAULT_RAG_FUSION_PROMPT
from ktem.reasoning.simple import FullQAPipeline, RAG_QA_GUARDRAIL_PROMPT
from ktem.trace import (
    RagTraceRecorder,
    get_trace_by_message,
    save_trace,
    set_active_recorder,
)
from ktem.trace.models import RagTraceRun
from ktem import react_api


class DummyRetriever:
    def __call__(self, text, top_k, **kwargs):
        return [
            SimpleNamespace(
                doc_id="chunk-owned", text="allowed text", metadata={"file_id": "owned"}
            )
        ]


class TraceableTestRetrievalPipeline(DocumentRetrievalPipeline):
    @property
    def vector_retrieval(self):
        return DummyRetriever()


class DummyQueryPipeline:
    def __init__(self, text: str):
        self.text = text

    def __call__(self, question: str):
        return SimpleNamespace(text=self.text)


class RetryRetriever:
    def __init__(self, docs):
        self.docs = docs
        self.queries = []

    def __call__(self, text, **kwargs):
        self.queries.append(text)
        return self.docs


def _doc(doc_id, text, file_id, **metadata):
    return SimpleNamespace(
        doc_id=doc_id,
        text=text,
        content=text,
        score=metadata.pop("score", None),
        metadata={"file_id": file_id, **metadata},
    )


def _tables(test_engine):
    Base = declarative_base()

    class Source(Base):
        __tablename__ = "trace_source"

        id = Column(String, primary_key=True)
        name = Column(String)
        path = Column(String)
        size = Column(Integer, default=0)
        date_created = Column(DateTime, default=datetime.utcnow)
        user = Column(String, default="")
        note = Column(JSON, default={})

    class IndexTable(Base):
        __tablename__ = "trace_index"

        id = Column(Integer, primary_key=True)
        source_id = Column(String)
        target_id = Column(String)
        relation_type = Column(String)
        user = Column(String, default="")

    Base.metadata.create_all(test_engine)
    return Source, IndexTable


def test_rag_fusion_prompt_requires_distinct_retrieval_angles():
    prompt = DEFAULT_RAG_FUSION_PROMPT

    assert "Time applicability" in prompt
    assert "old policy/new policy boundary" in prompt
    assert "event date versus hire date" in prompt
    assert "Person and role applicability" in prompt
    assert "probation status" in prompt
    assert "Amount and standard lookup" in prompt
    assert "Approval and process" in prompt
    assert "not simple paraphrases" in prompt


def test_rag_fusion_query_pipeline_adds_missing_angle_queries():
    from ktem.reasoning.prompt_optimization.rag_fusion import RagFusionQueryPipeline

    question = "2024 年 7 月入职的上海销售团队试用期员工深圳出差怎么报销？"
    queries = RagFusionQueryPipeline._ensure_angle_coverage(
        question=question,
        queries=[
            "2024年7月入职上海销售试用期员工深圳出差报销规定",
            "今年7月新加入上海销售部门人员去深圳参加客户会议差旅报销",
        ],
        max_queries=5,
    )

    assert queries[0] == question
    assert len(queries) == 5
    assert any("生效日期" in query and "旧制度" in query for query in queries[1:])
    assert any("身份适用" in query and "报销资格" in query for query in queries[1:])
    assert any("住宿费" in query and "餐补" in query for query in queries[1:])
    assert any("提前审批" in query and "所需材料" in query for query in queries[1:])


def test_reasoning_appends_qa_guardrail_once():
    assert FullQAPipeline._append_system_guardrail("") == RAG_QA_GUARDRAIL_PROMPT

    custom_prompt = "请用中文回答。"
    guarded_prompt = FullQAPipeline._append_system_guardrail(custom_prompt)

    assert guarded_prompt.startswith(custom_prompt)
    assert "event date, hire date, submission date" in guarded_prompt
    assert "probation status" in guarded_prompt
    assert (
        FullQAPipeline._append_system_guardrail(guarded_prompt).count(
            RAG_QA_GUARDRAIL_PROMPT
        )
        == 1
    )


def test_trace_records_retrieval_params_and_durations(monkeypatch):
    test_engine = create_engine("sqlite:///:memory:")
    monkeypatch.setattr("ktem.trace.trace_service.engine", test_engine)
    SQLModel.metadata.create_all(test_engine, tables=[RagTraceRun.__table__])

    recorder = RagTraceRecorder(
        conversation_id="conv",
        user_id="alice",
        question="question",
        selected_file_ids=["owned"],
        retrieval_params={"topK": 3, "retrievalMode": "hybrid", "rerank": True},
        effective_principal={"principal": {"type": "user", "id": "alice"}},
        turn_index=0,
    )
    with recorder.timer("retrieval"):
        pass
    recorder.set_message("conv-0-assistant")
    row = save_trace(recorder.finish("completed"))

    assert row.data["retrieval_params"]["topK"] == 3
    assert row.data["durations_ms"]["retrieval"] >= 0
    assert row.data["durations_ms"]["total"] >= 0
    assert row.data["vector_candidate_chunks"] == []
    assert row.data["text_candidate_chunks"] == []
    assert row.data["fused_candidate_chunks"] == []
    assert row.data["reranked_candidate_chunks"] == []
    assert get_trace_by_message("conv-0-assistant", "alice").trace_id == row.trace_id


def test_answer_support_checker_outputs_three_statuses():
    docs = [
        _doc(
            "chunk-1",
            "上海住宿标准为 500 元。员工需要提前审批。",
            "policy",
            file_name="policy.md",
        )
    ]

    assessment = assess_answer_support(
        "上海住宿标准为 500 元。餐补为 80 元。资料中没有提供打车上限。",
        docs,
    )

    statuses = [item["status"] for item in assessment["checks"]]
    assert statuses == ["supported", "unsupported", "insufficient"]
    assert assessment["supported_count"] == 1
    assert assessment["unsupported_count"] == 1
    assert assessment["insufficient_count"] == 1
    assert assessment["evidence_coverage"] == 0.5
    assert assessment["checks"][0]["evidence"][0]["source_name"] == "policy.md"


def test_answer_support_accepts_short_chinese_fact_with_number_and_key_terms():
    docs = [
        _doc(
            "chunk-1",
            "一线城市住宿标准为每天 500 元，其他城市住宿标准为每天 350 元。",
            "policy",
            file_name="expense_policy.md",
        )
    ]

    assessment = assess_answer_support(
        "上海出差的住宿标准是每天500元。",
        docs,
    )

    assert assessment["checks"][0]["status"] == "supported"
    assert assessment["evidence_coverage"] == 1


def test_answer_support_splits_mixed_supported_and_insufficient_clause():
    docs = [
        _doc(
            "chunk-1",
            "一线城市住宿标准为每天 500 元，其他城市住宿标准为每天 350 元。",
            "policy",
            file_name="expense_policy.md",
        )
    ]

    assessment = assess_answer_support(
        "上海出差住宿标准每天500元是正确的，但材料中未提及餐补的具体金额（如80元），因此无法确认餐补标准是否正确。",
        docs,
    )

    statuses = [item["status"] for item in assessment["checks"]]
    assert statuses == ["supported", "insufficient", "insufficient"]
    assert assessment["supported_count"] == 1
    assert assessment["unsupported_count"] == 0
    assert assessment["insufficient_count"] == 2
    assert assessment["evidence_coverage"] == 1


def test_answer_support_normalizes_temporal_policy_dates():
    docs = [
        _doc(
            "chunk-1",
            "旧制度适用于 2024 年 7 月发生的差旅报销事项；新制度适用于 2026 年及以后发生的报销事项。",
            "policy",
            file_name="policy.md",
        )
    ]

    assessment = assess_answer_support(
        "2024年7月发生的差旅报销应按照旧制度执行。",
        docs,
    )

    assert assessment["checks"][0]["status"] == "supported"
    assert assessment["evidence_coverage"] == 1


def test_answer_support_accepts_process_claim_without_number():
    docs = [
        _doc(
            "chunk-1",
            "员工因客户拜访或行业会议需要离开常驻城市工作的，应提交出差申请。普通报销由直属主管初审，部门负责人确认费用合理性。",
            "policy",
            file_name="policy.md",
        )
    ]

    assessment = assess_answer_support(
        "出差前需提交出差申请并经直属主管审批。",
        docs,
    )

    assert assessment["checks"][0]["status"] == "supported"


def test_answer_support_accepts_material_list_claim_without_number():
    docs = [
        _doc(
            "chunk-1",
            "差旅材料应包含申请记录、出行凭证、发票、行程说明和必要的客户或会议证明。",
            "policy",
            file_name="policy.md",
        )
    ]

    assessment = assess_answer_support(
        "报销材料需包含出差申请记录、出行凭证、住宿发票、行程说明以及客户会议证明。",
        docs,
    )

    assert assessment["checks"][0]["status"] == "supported"


def test_trace_records_answer_verification_gate_and_retry_docs():
    recorder = RagTraceRecorder(
        conversation_id="conv",
        user_id="alice",
        question="上海住宿标准是多少？",
        selected_file_ids=["policy"],
        retrieval_params={},
        effective_principal={},
    )
    assessment = assess_answer_support(
        "上海住宿标准为 500 元。",
        [_doc("chunk-1", "上海住宿标准为 500 元。", "policy")],
    )
    gate = decide_evidence_gate(assessment)
    retry_doc = _doc("chunk-2", "补充证据", "policy")

    recorder.record_answer_verification(
        assessment,
        gate=gate,
        retry_triggered=True,
        retry_query="上海住宿标准",
        retry_docs=[retry_doc],
        final_action="retried",
    )

    verification = recorder.data["answer_verification"]
    assert verification["evidence_coverage"] == 1
    assert verification["gate"]["status"] == "supported"
    assert verification["retry"]["triggered"] is True
    assert verification["retry"]["added_context_count"] == 1
    assert verification["retry"]["added_context_chunks"][0]["chunk_id"] == "chunk-2"


def test_reasoning_refuses_when_answer_still_lacks_evidence_after_retry():
    recorder = RagTraceRecorder(
        conversation_id="conv",
        user_id="alice",
        question="餐补是多少？",
        selected_file_ids=["policy"],
        retrieval_params={},
        effective_principal={},
    )
    retriever = RetryRetriever([_doc("retry-1", "住宿标准为 500 元。", "policy")])
    pipeline = FullQAPipeline(retrievers=[retriever])
    answer = SimpleNamespace(
        text="餐补为 80 元。",
        content="餐补为 80 元。",
        metadata={"citation": None},
    )

    docs, assessment, action = pipeline.verify_answer_support(
        answer,
        [_doc("chunk-1", "住宿标准为 500 元。", "policy")],
        "餐补是多少？",
        [],
        recorder,
    )

    assert action == "refused"
    assert "无法可靠回答" in answer.text
    assert len(docs) == 2
    assert retriever.queries
    assert assessment["unsupported_count"] >= 1
    assert recorder.data["answer_verification"]["final_action"] == "refused"


def test_trace_records_rewrite_and_hyde_inputs():
    recorder = RagTraceRecorder(
        conversation_id="conv",
        user_id="alice",
        question="这个咋报销？",
        selected_file_ids=["owned"],
        retrieval_params={"enhancement": "rewrite"},
        effective_principal={},
    )

    recorder.record_retrieval_enhancement(
        strategy="rewrite",
        original_question="这个咋报销？",
        rewritten_question="差旅报销政策和申请流程是什么？",
        hyde_document=None,
        retrieval_query="差旅报销政策和申请流程是什么？",
    )
    assert recorder.data["retrieval_enhancement"]["strategy"] == "rewrite"
    assert recorder.data["query_rewrite"]["enabled"] is True
    assert recorder.data["query_rewrite"]["rewritten_question"] == "差旅报销政策和申请流程是什么？"

    recorder.record_retrieval_enhancement(
        strategy="hyde",
        original_question="这个咋报销？",
        rewritten_question=None,
        hyde_document="差旅报销政策规定员工应提交发票和审批单。",
        retrieval_query="差旅报销政策规定员工应提交发票和审批单。",
    )
    assert recorder.data["retrieval_enhancement"]["strategy"] == "hyde"
    assert recorder.data["hyde"]["enabled"] is True
    assert recorder.data["hyde"]["document"] == "差旅报销政策规定员工应提交发票和审批单。"


def test_reasoning_builds_enhanced_retrieval_query():
    recorder = RagTraceRecorder(
        conversation_id="conv",
        user_id="alice",
        question="这个咋报销？",
        selected_file_ids=["owned"],
        retrieval_params={},
        effective_principal={},
    )
    pipeline = FullQAPipeline(
        retrievers=[],
        rewrite_pipeline=DummyQueryPipeline("差旅报销政策和申请流程是什么？"),
        hyde_pipeline=DummyQueryPipeline("差旅报销政策规定员工应提交发票和审批单。"),
        rag_fusion_pipeline=DummyQueryPipeline("[]"),
    )

    pipeline.retrieval_enhancement = "rewrite"
    assert (
        pipeline._build_retrieval_query("这个咋报销？", recorder)
        == "差旅报销政策和申请流程是什么？"
    )
    assert recorder.data["retrieval_enhancement"]["strategy"] == "rewrite"

    pipeline.retrieval_enhancement = "hyde"
    assert (
        pipeline._build_retrieval_query("这个咋报销？", recorder)
        == "差旅报销政策规定员工应提交发票和审批单。"
    )
    assert recorder.data["retrieval_enhancement"]["strategy"] == "hyde"


def test_reasoning_builds_rag_fusion_query_variants():
    recorder = RagTraceRecorder(
        conversation_id="conv",
        user_id="alice",
        question="2024 年上海销售团队差旅餐补和住宿上限是多少？",
        selected_file_ids=["owned"],
        retrieval_params={},
        effective_principal={},
    )
    fusion_pipeline = DummyQueryPipeline(
        '["2024 上海销售团队差旅餐补标准", '
        '"上海销售住宿上限 2024 差旅政策", '
        '"销售团队差旅报销条件和例外 2024"]'
    )
    fusion_pipeline.__call__ = lambda question: SimpleNamespace(
        text=fusion_pipeline.text,
        metadata={
            "queries": [
                "2024 年上海销售团队差旅餐补和住宿上限是多少？",
                "2024 上海销售团队差旅餐补标准",
                "上海销售住宿上限 2024 差旅政策",
                "销售团队差旅报销条件和例外 2024",
            ],
            "raw_response": fusion_pipeline.text,
        },
    )
    pipeline = FullQAPipeline(
        retrievers=[],
        rag_fusion_pipeline=fusion_pipeline,
    )

    pipeline.retrieval_enhancement = "fusion"
    retrieval_query, query_variants = pipeline._build_retrieval_plan(
        "2024 年上海销售团队差旅餐补和住宿上限是多少？",
        recorder,
    )

    assert retrieval_query == "2024 年上海销售团队差旅餐补和住宿上限是多少？"
    assert len(query_variants) == 5
    assert recorder.data["retrieval_enhancement"]["strategy"] == "fusion"
    assert recorder.data["rag_fusion"]["enabled"] is True
    assert recorder.data["rag_fusion"]["queries"] == query_variants
    assert any("生效日期" in query and "旧制度" in query for query in query_variants)
    assert any("身份适用" in query and "报销资格" in query for query in query_variants)


def test_trace_records_retrieval_candidate_stages():
    recorder = RagTraceRecorder(
        conversation_id="conv",
        user_id="alice",
        question="question",
        selected_file_ids=["owned"],
        retrieval_params={"rerank": True},
        effective_principal={},
    )
    vector_doc = _doc("vector-1", "vector text", "owned", score=0.8)
    text_doc = _doc("text-1", "text match", "owned")
    text_doc.retrieval_metadata = {
        "retrieval_channel": "text",
        "text_rank": 1,
        "rrf_score": 1 / 61,
        "final_rank": 1,
    }
    fused_docs = [text_doc, vector_doc]
    reranked_docs = [vector_doc]

    recorder.record_retrieval_candidates(
        vector_docs=[vector_doc],
        text_docs=[text_doc],
        fused_docs=fused_docs,
    )
    recorder.record_rerank(fused_docs, reranked_docs, rerank_enabled=True)

    assert recorder.data["vector_candidate_chunks"][0]["retrieval_channel"] == "vector"
    assert recorder.data["vector_candidate_chunks"][0]["rank_before_fusion"] == 1
    assert recorder.data["text_candidate_chunks"][0]["retrieval_channel"] == "text"
    assert (
        recorder.data["fused_candidate_chunks"][0]["retrieval_channel"]
        == "text"
    )
    assert recorder.data["fused_candidate_chunks"][0]["text_rank"] == 1
    assert recorder.data["fused_candidate_chunks"][0]["final_rank"] == 1
    assert recorder.data["fused_candidate_chunks"][0]["rrf_score"] == 1 / 61
    assert recorder.data["fused_candidate_chunks"][0]["rank_after_fusion"] == 1
    assert (
        recorder.data["reranked_candidate_chunks"][0]["retrieval_channel"] == "reranked"
    )
    assert recorder.data["reranked_candidate_chunks"][0]["rank_after_rerank"] == 1
    assert (
        recorder.data["candidate_chunks_before_rerank"]
        == recorder.data["fused_candidate_chunks"]
    )
    assert (
        recorder.data["candidate_chunks_after_rerank"]
        == recorder.data["reranked_candidate_chunks"]
    )
    assert recorder.data["rerank_enabled"] is True


def test_trace_marks_summary_and_detail_layers():
    recorder = RagTraceRecorder(
        conversation_id="conv",
        user_id="alice",
        question="这份文档整体讲了什么？",
        selected_file_ids=["policy"],
        retrieval_params={},
        effective_principal={},
    )
    summary_doc = _doc(
        "summary-1",
        "文档摘要：覆盖假期、报销和权限。",
        "policy",
        type="summary",
        retrieval_layer="summary_layer",
        summary_layer="document_summary",
        summary_scope="document",
    )
    summary_doc.retrieval_metadata = {"retrieval_layer": "summary_layer"}
    detail_doc = _doc(
        "chunk-1",
        "正式员工每年享有 10 天带薪年假。",
        "policy",
    )
    detail_doc.retrieval_metadata = {"retrieval_layer": "detail_layer"}

    recorder.record_retrieval_candidates(fused_docs=[summary_doc])
    recorder.record_context([summary_doc, detail_doc])

    assert recorder.data["fused_candidate_chunks"][0]["type"] == "summary"
    assert recorder.data["fused_candidate_chunks"][0]["retrieval_layer"] == "summary_layer"
    assert recorder.data["fused_candidate_chunks"][0]["summary_layer"] == "document_summary"
    assert recorder.data["context_chunks"][1]["retrieval_layer"] == "detail_layer"


def test_index_pipeline_builds_document_and_section_summary_chunks():
    pipeline = IndexPipeline.__new__(IndexPipeline)
    docs = [
        _doc(
            "doc-1",
            "# 综合制度长文档\n\n## 第一章 总则\n\n本制度适用于正式员工和试用期员工。\n\n## 第二章 年假制度\n\n正式员工每年享有 10 天带薪年假。",
            "policy",
            file_name="policy.md",
            type="text",
        )
    ]

    summary_chunks = pipeline.build_summary_chunks(
        text_docs=docs,
        detail_chunks=docs,
        file_id="policy",
        file_name="policy.md",
    )

    assert [chunk.metadata["summary_layer"] for chunk in summary_chunks] == [
        "document_summary",
        "section_summary",
        "section_summary",
    ]
    assert all(chunk.metadata["type"] == "summary" for chunk in summary_chunks)
    assert all(
        chunk.metadata["retrieval_layer"] == "summary_layer"
        for chunk in summary_chunks
    )
    assert "第二章 年假制度" in summary_chunks[0].text


def test_document_retrieval_pipeline_selects_summary_scope_for_global_questions():
    pipeline = DocumentRetrievalPipeline.__new__(DocumentRetrievalPipeline)

    summary_layer, summary_scope = pipeline._select_retrieval_layer(
        "这份文档整体涵盖哪些制度？",
        summary_chunk_ids=["summary-1", "summary-2"],
        detail_chunk_ids=["chunk-1", "chunk-2"],
    )
    detail_layer, detail_scope = pipeline._select_retrieval_layer(
        "BX-2048 当前是什么状态？",
        summary_chunk_ids=["summary-1", "summary-2"],
        detail_chunk_ids=["chunk-1", "chunk-2"],
    )

    assert summary_layer == "summary_layer"
    assert summary_scope == ["summary-1", "summary-2"]
    assert detail_layer == "detail_layer"
    assert detail_scope == ["chunk-1", "chunk-2"]


def test_trace_records_fusion_query_candidates():
    recorder = RagTraceRecorder(
        conversation_id="conv",
        user_id="alice",
        question="question",
        selected_file_ids=["owned"],
        retrieval_params={"enhancement": "fusion"},
        effective_principal={},
    )
    vector_doc = _doc("vector-1", "vector text", "owned", score=0.8)
    vector_doc.retrieval_metadata = {
        "retrieval_channel": "vector",
        "fusion_query": "query one",
        "fusion_query_index": 1,
    }
    text_doc = _doc("text-1", "text match", "owned")
    text_doc.retrieval_metadata = {
        "retrieval_channel": "text",
        "fusion_query": "query one",
        "fusion_query_index": 1,
    }

    recorder.record_retrieval_candidates(
        fusion_query_candidates=[
            {
                "query": "query one",
                "query_index": 1,
                "vector_docs": [vector_doc],
                "text_docs": [text_doc],
                "fused_docs": [vector_doc, text_doc],
            }
        ]
    )

    query_trace = recorder.data["fusion_query_candidates"][0]
    assert query_trace["query"] == "query one"
    assert query_trace["vector_candidate_chunks"][0]["fusion_query_index"] == 1
    assert query_trace["text_candidate_chunks"][0]["fusion_query"] == "query one"


def test_trace_records_rerank_disabled_without_error():
    recorder = RagTraceRecorder(
        conversation_id="conv",
        user_id="alice",
        question="question",
        selected_file_ids=["owned"],
        retrieval_params={"rerank": False},
        effective_principal={},
    )
    docs = [_doc("chunk-1", "candidate text", "owned")]

    recorder.record_retrieval_candidates(fused_docs=docs)
    recorder.record_rerank(docs, docs, rerank_enabled=False)

    assert recorder.data["rerank_enabled"] is False
    assert recorder.data["reranked_candidate_chunks"][0]["chunk_id"] == "chunk-1"
    assert (
        recorder.data["candidate_chunks_after_rerank"]
        == recorder.data["reranked_candidate_chunks"]
    )


def test_trace_records_acl_filter_counts(monkeypatch):
    test_engine = create_engine("sqlite:///:memory:")
    monkeypatch.setattr(permission_service, "engine", test_engine)
    monkeypatch.setattr("ktem.index.file.pipelines.engine", test_engine)
    SQLModel.metadata.create_all(test_engine, tables=[SourcePermission.__table__])
    Source, IndexTable = _tables(test_engine)

    with permission_service.Session(test_engine) as session:
        session.add_all(
            [
                Source(id="owned", name="owned.pdf", user="alice"),
                Source(id="hidden", name="hidden.pdf", user="bob"),
                IndexTable(
                    source_id="owned", target_id="chunk-owned", relation_type="document"
                ),
                IndexTable(
                    source_id="hidden",
                    target_id="chunk-hidden",
                    relation_type="document",
                ),
            ]
        )
        session.commit()

    index = SimpleNamespace(
        index_id=1, private=True, Source=Source, config={"private": True}
    )
    permission_service.ensure_default_acl(
        index, Source(id="owned", name="owned.pdf", user="alice")
    )
    permission_service.ensure_default_acl(
        index, Source(id="hidden", name="hidden.pdf", user="bob")
    )

    recorder = RagTraceRecorder(
        conversation_id="conv",
        user_id="alice",
        question="question",
        selected_file_ids=["hidden", "owned"],
        retrieval_params={},
        effective_principal={},
    )
    set_active_recorder(recorder)
    try:
        pipeline = TraceableTestRetrievalPipeline()
        pipeline.Index = IndexTable
        pipeline.Source = Source
        pipeline.PermissionService = permission_service
        pipeline.index_id = 1
        pipeline.private = True
        pipeline.user_id = "alice"
        pipeline.top_k = 5
        pipeline.get_extra_table = False
        pipeline.mmr = False

        pipeline.run("query", doc_ids=["hidden", "owned"])
    finally:
        set_active_recorder(None)

    acl = recorder.data["acl"]
    assert acl["pre_filter_source_count"] == 2
    assert acl["post_filter_source_count"] == 1
    assert acl["pre_filter_chunk_count"] == 2
    assert acl["post_filter_chunk_count"] == 1
    assert acl["filtered_source_count"] == 1
    assert acl["filtered_reason_summary"] == {"no_matching_acl_principal": 1}
    assert recorder.data["selected_file_ids"] == ["owned"]


def test_filtered_chunk_text_never_appears_in_trace_api(monkeypatch):
    test_engine = create_engine("sqlite:///:memory:")
    monkeypatch.setattr(permission_service, "engine", test_engine)
    monkeypatch.setattr("ktem.trace.trace_service.engine", test_engine)
    SQLModel.metadata.create_all(
        test_engine, tables=[RagTraceRun.__table__, SourcePermission.__table__]
    )
    Source, _ = _tables(test_engine)
    with permission_service.Session(test_engine) as session:
        session.add_all(
            [
                Source(id="owned", name="owned.pdf", user="alice"),
                Source(id="hidden", name="hidden.pdf", user="bob"),
            ]
        )
        session.commit()

    recorder = RagTraceRecorder(
        conversation_id="conv",
        user_id="alice",
        question="question",
        selected_file_ids=["hidden", "owned"],
        retrieval_params={},
        effective_principal={},
    )
    recorder.record_acl_filter(
        index=SimpleNamespace(index_id=1, Source=Source, config={"private": True}),
        requested_source_ids=["hidden", "owned"],
        allowed_source_ids=["owned"],
        user_id="alice",
        pre_chunk_count=2,
        post_chunk_count=1,
    )
    recorder.record_context(
        [
            SimpleNamespace(
                doc_id="chunk-owned", text="allowed text", metadata={"file_id": "owned"}
            )
        ]
    )
    recorder.set_message("conv-0-assistant")
    row = save_trace(recorder.finish("completed"))
    serialized = str(row.data)

    assert "allowed text" in serialized
    assert "secret hidden text" not in serialized
    assert row.data["acl"]["filtered_source_count"] == 1
    assert row.data["selected_file_ids"] == ["owned"]
    assert "hidden" not in serialized


def test_acl_filtered_chunks_are_excluded_from_candidate_traces(monkeypatch):
    test_engine = create_engine("sqlite:///:memory:")
    monkeypatch.setattr(permission_service, "engine", test_engine)
    SQLModel.metadata.create_all(test_engine, tables=[SourcePermission.__table__])
    Source, _ = _tables(test_engine)

    with permission_service.Session(test_engine) as session:
        session.add_all(
            [
                Source(id="owned", name="owned.pdf", user="alice"),
                Source(id="hidden", name="hidden.pdf", user="bob"),
            ]
        )
        session.commit()

    recorder = RagTraceRecorder(
        conversation_id="conv",
        user_id="alice",
        question="question",
        selected_file_ids=["hidden", "owned"],
        retrieval_params={},
        effective_principal={},
    )
    recorder.record_acl_filter(
        index=SimpleNamespace(index_id=1, Source=Source, config={"private": True}),
        requested_source_ids=["hidden", "owned"],
        allowed_source_ids=["owned"],
        user_id="alice",
        pre_chunk_count=2,
        post_chunk_count=1,
    )
    owned = _doc("chunk-owned", "allowed text", "owned")
    hidden = _doc("chunk-hidden", "secret hidden text", "hidden")

    recorder.record_retrieval_candidates(
        vector_docs=[owned, hidden],
        text_docs=[hidden],
        fused_docs=[hidden, owned],
    )
    recorder.record_rerank([hidden, owned], [hidden, owned], rerank_enabled=False)

    serialized = str(recorder.data)
    assert "allowed text" in serialized
    assert "secret hidden text" not in serialized
    assert [item["chunk_id"] for item in recorder.data["vector_candidate_chunks"]] == [
        "chunk-owned"
    ]
    assert recorder.data["text_candidate_chunks"] == []
    assert [item["chunk_id"] for item in recorder.data["fused_candidate_chunks"]] == [
        "chunk-owned"
    ]
    assert [
        item["chunk_id"] for item in recorder.data["reranked_candidate_chunks"]
    ] == ["chunk-owned"]


def test_missing_message_trace_returns_none(monkeypatch):
    monkeypatch.setattr(
        react_api, "get_trace_by_message", lambda message_id, user_id: None
    )

    service = react_api.ReactApiService()

    assert service.get_message_trace("legacy-message", "alice") is None
