import json
from pathlib import Path
from typing import cast
from unittest.mock import patch

from openai.types.create_embedding_response import CreateEmbeddingResponse

from kotaemon.base import Document, RetrievedDocument
from kotaemon.embeddings import AzureOpenAIEmbeddings
from kotaemon.indices import VectorIndexing, VectorRetrieval
from kotaemon.indices.qa.citation_qa import DEFAULT_QA_TEXT_PROMPT
from kotaemon.storages import ChromaVectorStore, InMemoryDocumentStore

with open(Path(__file__).parent / "resources" / "embedding_openai.json") as f:
    openai_embedding = CreateEmbeddingResponse.model_validate(json.load(f))


def test_default_qa_prompt_requires_temporal_and_identity_boundaries():
    prompt = DEFAULT_QA_TEXT_PROMPT

    assert "event date, hire date, submission date, approval date" in prompt
    assert "old/new policy boundaries" in prompt
    assert "cannot be uniquely determined" in prompt
    assert "probation status" in prompt
    assert "Do not assume that a rule for regular employees" in prompt


@patch(
    "openai.resources.embeddings.Embeddings.create",
    side_effect=lambda *args, **kwargs: openai_embedding,
)
def test_indexing(tmp_path):
    db = ChromaVectorStore(path=str(tmp_path))
    doc_store = InMemoryDocumentStore()
    embedding = AzureOpenAIEmbeddings(
        azure_deployment="text-embedding-ada-002",
        azure_endpoint="https://test.openai.azure.com/",
        api_key="some-key",
        api_version="version",
    )

    pipeline = VectorIndexing(vector_store=db, embedding=embedding, doc_store=doc_store)
    pipeline.doc_store = cast(InMemoryDocumentStore, pipeline.doc_store)
    pipeline.vector_store = cast(ChromaVectorStore, pipeline.vector_store)
    assert pipeline.vector_store._collection.count() == 0, "Expected empty collection"
    assert len(pipeline.doc_store._store) == 0, "Expected empty doc store"
    pipeline(text=Document(text="Hello world"))
    assert pipeline.vector_store._collection.count() == 1, "Index 1 item"
    assert len(pipeline.doc_store._store) == 1, "Expected 1 document"


@patch(
    "openai.resources.embeddings.Embeddings.create",
    side_effect=lambda *args, **kwargs: openai_embedding,
)
def test_retrieving(tmp_path):
    db = ChromaVectorStore(path=str(tmp_path))
    doc_store = InMemoryDocumentStore()
    embedding = AzureOpenAIEmbeddings(
        azure_deployment="text-embedding-ada-002",
        azure_endpoint="https://test.openai.azure.com/",
        api_key="some-key",
        api_version="version",
    )

    index_pipeline = VectorIndexing(
        vector_store=db, embedding=embedding, doc_store=doc_store
    )
    retrieval_pipeline = VectorRetrieval(
        vector_store=db, doc_store=doc_store, embedding=embedding
    )

    index_pipeline(text=Document(text="Hello world"))
    output = retrieval_pipeline(text="Hello world")
    output1 = retrieval_pipeline(text="Hello world")

    assert len(output) == 1, "Expect 1 results"
    assert output == output1, "Expect identical results"


def test_hybrid_rrf_fuses_duplicate_chunks_and_keeps_channel_ranks():
    pipeline = VectorRetrieval.__new__(VectorRetrieval)
    shared_vector = pipeline._annotate_candidates(
        [
            RetrievedDocument(text="shared", id_="shared"),
            RetrievedDocument(text="vector only", id_="v"),
        ],
        "vector",
    )
    shared_text = pipeline._annotate_candidates(
        [
            RetrievedDocument(text="text only", id_="t"),
            RetrievedDocument(text="shared", id_="shared"),
        ],
        "text",
    )

    fused = pipeline._rrf_fuse(
        vector_candidates=shared_vector,
        text_candidates=shared_text,
    )

    assert [doc.doc_id for doc in fused] == ["shared", "t", "v"]
    shared = fused[0]
    assert shared.retrieval_metadata["retrieval_channels"] == ["vector", "text"]
    assert shared.retrieval_metadata["vector_rank"] == 1
    assert shared.retrieval_metadata["text_rank"] == 2
    assert shared.retrieval_metadata["final_rank"] == 1
    assert shared.retrieval_metadata["rrf_score"] == (1 / 61) + (1 / 62)
    assert "text_rank" not in shared_vector[0].retrieval_metadata


def test_rrf_fuses_across_query_variants():
    pipeline = VectorRetrieval.__new__(VectorRetrieval)
    q1 = pipeline._annotate_candidates(
        [
            RetrievedDocument(text="shared", id_="shared"),
            RetrievedDocument(text="q1 only", id_="q1"),
        ],
        "vector",
        query="query one",
        query_index=1,
    )
    q2 = pipeline._annotate_candidates(
        [
            RetrievedDocument(text="q2 only", id_="q2"),
            RetrievedDocument(text="shared", id_="shared"),
        ],
        "text",
        query="query two",
        query_index=2,
    )

    fused = pipeline._rrf_fuse(
        vector_candidates=q1,
        text_candidates=q2,
        candidate_runs=[("q1:vector", q1), ("q2:text", q2)],
    )

    assert fused[0].doc_id == "shared"
    assert {doc.doc_id for doc in fused[1:]} == {"q1", "q2"}
    shared = fused[0]
    assert shared.retrieval_metadata["retrieval_channels"] == ["vector", "text"]
    assert shared.retrieval_metadata["vector_rank"] == 1
    assert shared.retrieval_metadata["text_rank"] == 2
    assert shared.retrieval_metadata["fusion_rank_contributions"] == {
        "q1:vector": 1,
        "q2:text": 2,
    }
    assert shared.retrieval_metadata["rrf_score"] == (1 / 61) + (1 / 62)
