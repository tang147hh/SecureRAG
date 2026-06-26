from __future__ import annotations

from collections import defaultdict
import threading
import time
import uuid
from pathlib import Path
from typing import Optional, Sequence, cast

from theflow.settings import settings as flowsettings

from kotaemon.base import BaseComponent, Document, RetrievedDocument
from kotaemon.embeddings import BaseEmbeddings
from kotaemon.storages import BaseDocumentStore, BaseVectorStore

from .base import BaseIndexing, BaseRetrieval
from .rankings import BaseReranking, LLMReranking

VECTOR_STORE_FNAME = "vectorstore"
DOC_STORE_FNAME = "docstore"
RRF_K = 60


class VectorIndexing(BaseIndexing):
    """Ingest the document, run through the embedding, and store the embedding in a
    vector store.

    This pipeline supports the following set of inputs:
        - List of documents
        - List of texts
    """

    cache_dir: Optional[str] = getattr(flowsettings, "KH_CHUNKS_OUTPUT_DIR", None)
    vector_store: BaseVectorStore
    doc_store: Optional[BaseDocumentStore] = None
    embedding: BaseEmbeddings
    count_: int = 0

    def to_retrieval_pipeline(self, *args, **kwargs):
        """Convert the indexing pipeline to a retrieval pipeline"""
        return VectorRetrieval(
            vector_store=self.vector_store,
            doc_store=self.doc_store,
            embedding=self.embedding,
            **kwargs,
        )

    def write_chunk_to_file(self, docs: list[Document]):
        # save the chunks content into markdown format
        if self.cache_dir:
            file_name = docs[0].metadata.get("file_name")
            if not file_name:
                return

            file_name = Path(file_name)
            for i in range(len(docs)):
                markdown_content = ""
                if "page_label" in docs[i].metadata:
                    page_label = str(docs[i].metadata["page_label"])
                    markdown_content += f"Page label: {page_label}"
                if "file_name" in docs[i].metadata:
                    filename = docs[i].metadata["file_name"]
                    markdown_content += f"\nFile name: {filename}"
                if "section" in docs[i].metadata:
                    section = docs[i].metadata["section"]
                    markdown_content += f"\nSection: {section}"
                if "type" in docs[i].metadata:
                    if docs[i].metadata["type"] == "image":
                        image_origin = docs[i].metadata["image_origin"]
                        image_origin = f'<p><img src="{image_origin}"></p>'
                        markdown_content += f"\nImage origin: {image_origin}"
                if docs[i].text:
                    markdown_content += f"\ntext:\n{docs[i].text}"

                with open(
                    Path(self.cache_dir) / f"{file_name.stem}_{self.count_+i}.md",
                    "w",
                    encoding="utf-8",
                ) as f:
                    f.write(markdown_content)

    def add_to_docstore(self, docs: list[Document]):
        if self.doc_store:
            print("Adding documents to doc store")
            self.doc_store.add(docs)

    def add_to_vectorstore(self, docs: list[Document]):
        # in case we want to skip embedding
        if self.vector_store:
            print(f"Getting embeddings for {len(docs)} nodes")
            embeddings = self.embedding(docs)
            print("Adding embeddings to vector store")
            self.vector_store.add(
                embeddings=embeddings,
                ids=[t.doc_id for t in docs],
            )

    def run(self, text: str | list[str] | Document | list[Document]):
        input_: list[Document] = []
        if not isinstance(text, list):
            text = [text]

        for item in cast(list, text):
            if isinstance(item, str):
                input_.append(Document(text=item, id_=str(uuid.uuid4())))
            elif isinstance(item, Document):
                input_.append(item)
            else:
                raise ValueError(
                    f"Invalid input type {type(item)}, should be str or Document"
                )

        self.add_to_vectorstore(input_)
        self.add_to_docstore(input_)
        self.write_chunk_to_file(input_)
        self.count_ += len(input_)


class VectorRetrieval(BaseRetrieval):
    """Retrieve list of documents from vector store"""

    vector_store: BaseVectorStore
    doc_store: Optional[BaseDocumentStore] = None
    embedding: BaseEmbeddings
    rerankers: Sequence[BaseReranking] = []
    top_k: int = 5
    first_round_top_k_mult: int = 10
    retrieval_mode: str = "hybrid"  # vector, text, hybrid

    @staticmethod
    def _doc_key(doc: RetrievedDocument | Document) -> str:
        return str(getattr(doc, "doc_id", "") or getattr(doc, "id_", "") or "")

    def _annotate_candidates(
        self,
        documents: list[RetrievedDocument],
        channel: str,
        query: str | None = None,
        query_index: int | None = None,
    ) -> list[RetrievedDocument]:
        for rank, doc in enumerate(documents, start=1):
            metadata = dict(doc.retrieval_metadata or {})
            metadata[f"{channel}_rank"] = rank
            metadata["retrieval_channel"] = channel
            if query is not None:
                metadata["fusion_query"] = query
            if query_index is not None:
                metadata["fusion_query_index"] = query_index
                metadata["fusion_channel"] = f"q{query_index}:{channel}"
            doc.retrieval_metadata = metadata
        return documents

    def _rrf_fuse(
        self,
        *,
        vector_candidates: list[RetrievedDocument],
        text_candidates: list[RetrievedDocument],
        candidate_runs: list[tuple[str, list[RetrievedDocument]]] | None = None,
    ) -> list[RetrievedDocument]:
        by_id: dict[str, RetrievedDocument] = {}
        channel_ranks: dict[str, dict[str, int]] = {}
        fusion_ranks: dict[str, dict[str, int]] = {}

        runs = candidate_runs or [
            ("vector", vector_candidates),
            ("text", text_candidates),
        ]
        for channel, candidates in runs:
            for rank, doc in enumerate(candidates, start=1):
                doc_id = self._doc_key(doc)
                if not doc_id:
                    continue
                if doc_id not in by_id:
                    by_id[doc_id] = RetrievedDocument(**doc.to_dict())
                    by_id[doc_id].score = doc.score
                    by_id[doc_id].retrieval_metadata = dict(
                        doc.retrieval_metadata or {}
                    )
                    channel_ranks[doc_id] = {}
                    fusion_ranks[doc_id] = {}
                base_channel = channel.split(":", 1)[-1]
                previous_rank = channel_ranks[doc_id].get(base_channel)
                if previous_rank is None or rank < previous_rank:
                    channel_ranks[doc_id][base_channel] = rank
                previous_fusion_rank = fusion_ranks[doc_id].get(channel)
                if previous_fusion_rank is None or rank < previous_fusion_rank:
                    fusion_ranks[doc_id][channel] = rank

        scored: list[tuple[float, RetrievedDocument]] = []
        for doc_id, doc in by_id.items():
            ranks = channel_ranks[doc_id]
            rrf_score = sum(
                1.0 / (RRF_K + rank) for rank in fusion_ranks[doc_id].values()
            )
            channels = [channel for channel in ("vector", "text") if channel in ranks]
            metadata = dict(doc.retrieval_metadata or {})
            metadata.update(
                {
                    "retrieval_channel": "+".join(channels),
                    "retrieval_channels": channels,
                    "vector_rank": ranks.get("vector"),
                    "text_rank": ranks.get("text"),
                    "rrf_score": rrf_score,
                    "rrf_k": RRF_K,
                    "fusion_rank_contributions": fusion_ranks[doc_id],
                }
            )
            doc.retrieval_metadata = metadata
            scored.append((rrf_score, doc))

        scored.sort(
            key=lambda item: (
                -item[0],
                min(
                    item[1].retrieval_metadata.get("vector_rank") or 10**9,
                    item[1].retrieval_metadata.get("text_rank") or 10**9,
                ),
                item[1].retrieval_metadata.get("vector_rank") or 10**9,
                item[1].retrieval_metadata.get("text_rank") or 10**9,
            )
        )
        for final_rank, (_, doc) in enumerate(scored, start=1):
            metadata = dict(doc.retrieval_metadata or {})
            metadata["final_rank"] = final_rank
            metadata["rank_after_fusion"] = final_rank
            doc.retrieval_metadata = metadata
        return [doc for _, doc in scored]

    def _run_single_retrieval(
        self,
        text: str | Document,
        *,
        top_k_first_round: int,
        scope: list[str] | None,
        query_index: int | None = None,
        **kwargs,
    ) -> tuple[
        list[RetrievedDocument],
        list[RetrievedDocument],
        list[RetrievedDocument],
    ]:
        result: list[RetrievedDocument] = []
        vector_candidates: list[RetrievedDocument] = []
        text_candidates: list[RetrievedDocument] = []
        query = text.text if isinstance(text, Document) else text

        if self.retrieval_mode == "vector":
            emb = self.embedding(text)[0].embedding
            _, scores, ids = self.vector_store.query(
                embedding=emb, top_k=top_k_first_round, doc_ids=scope, **kwargs
            )
            docs = self.doc_store.get(ids)
            result = [
                RetrievedDocument(**doc.to_dict(), score=score)
                for doc, score in zip(docs, scores)
            ]
            vector_candidates = self._annotate_candidates(
                list(result),
                "vector",
                query=query,
                query_index=query_index,
            )
            result = vector_candidates
        elif self.retrieval_mode == "text":
            docs = []
            if scope:
                docs = self.doc_store.query(
                    query, top_k=top_k_first_round, doc_ids=scope
                )
            result = [RetrievedDocument(**doc.to_dict(), score=-1.0) for doc in docs]
            text_candidates = self._annotate_candidates(
                list(result),
                "text",
                query=query,
                query_index=query_index,
            )
            result = text_candidates
        elif self.retrieval_mode == "hybrid":
            # similarity search section
            emb = self.embedding(text)[0].embedding
            vs_docs: list[RetrievedDocument] = []
            vs_ids: list[str] = []
            vs_scores: list[float] = []

            def query_vectorstore():
                nonlocal vs_docs
                nonlocal vs_scores
                nonlocal vs_ids

                assert self.doc_store is not None
                _, vs_scores, vs_ids = self.vector_store.query(
                    embedding=emb, top_k=top_k_first_round, doc_ids=scope, **kwargs
                )
                if vs_ids:
                    vs_docs = self.doc_store.get(vs_ids)

            # full-text search section
            ds_docs: list[RetrievedDocument] = []

            def query_docstore():
                nonlocal ds_docs

                assert self.doc_store is not None
                if scope:
                    ds_docs = self.doc_store.query(
                        query, top_k=top_k_first_round, doc_ids=scope
                    )

            vs_query_thread = threading.Thread(target=query_vectorstore)
            ds_query_thread = threading.Thread(target=query_docstore)

            vs_query_thread.start()
            ds_query_thread.start()

            vs_query_thread.join()
            ds_query_thread.join()

            text_candidates = [
                RetrievedDocument(**doc.to_dict(), score=-1.0)
                for doc in ds_docs
            ]
            vector_candidates = [
                RetrievedDocument(**doc.to_dict(), score=score)
                for doc, score in zip(vs_docs, vs_scores)
            ]
            vector_candidates = self._annotate_candidates(
                vector_candidates,
                "vector",
                query=query,
                query_index=query_index,
            )
            text_candidates = self._annotate_candidates(
                text_candidates,
                "text",
                query=query,
                query_index=query_index,
            )
            result = self._rrf_fuse(
                vector_candidates=vector_candidates,
                text_candidates=text_candidates,
            )
            print(f"Got {len(vs_docs)} from vectorstore")
            print(f"Got {len(ds_docs)} from docstore")

        return result, vector_candidates, text_candidates

    def _run_fusion_retrieval(
        self,
        *,
        query_variants: list[str],
        top_k_first_round: int,
        scope: list[str] | None,
        **kwargs,
    ) -> tuple[
        list[RetrievedDocument],
        list[RetrievedDocument],
        list[RetrievedDocument],
        list[dict],
    ]:
        all_vector_candidates: list[RetrievedDocument] = []
        all_text_candidates: list[RetrievedDocument] = []
        candidate_runs: list[tuple[str, list[RetrievedDocument]]] = []
        per_query_candidates: list[dict] = []

        for query_index, query in enumerate(query_variants, start=1):
            query_result, vector_docs, text_docs = self._run_single_retrieval(
                query,
                top_k_first_round=top_k_first_round,
                scope=scope,
                query_index=query_index,
                **kwargs,
            )
            if self.retrieval_mode == "vector":
                candidate_runs.append((f"q{query_index}:vector", query_result))
            elif self.retrieval_mode == "text":
                candidate_runs.append((f"q{query_index}:text", query_result))
            else:
                candidate_runs.append((f"q{query_index}:vector", vector_docs))
                candidate_runs.append((f"q{query_index}:text", text_docs))

            all_vector_candidates.extend(vector_docs)
            all_text_candidates.extend(text_docs)
            per_query_candidates.append(
                {
                    "query": query,
                    "query_index": query_index,
                    "vector_docs": vector_docs,
                    "text_docs": text_docs,
                    "fused_docs": query_result,
                }
            )

        result = self._rrf_fuse(
            vector_candidates=all_vector_candidates,
            text_candidates=all_text_candidates,
            candidate_runs=candidate_runs,
        )
        query_hits: defaultdict[str, list[int]] = defaultdict(list)
        for query_info in per_query_candidates:
            query_index = query_info["query_index"]
            for doc in query_info["vector_docs"] + query_info["text_docs"]:
                doc_id = self._doc_key(doc)
                if doc_id and query_index not in query_hits[doc_id]:
                    query_hits[doc_id].append(query_index)
        for doc in result:
            metadata = dict(doc.retrieval_metadata or {})
            metadata["fusion_query_hits"] = query_hits.get(self._doc_key(doc), [])
            doc.retrieval_metadata = metadata
        return result, all_vector_candidates, all_text_candidates, per_query_candidates

    def _filter_docs(
        self, documents: list[RetrievedDocument], top_k: int | None = None
    ):
        if top_k:
            documents = documents[:top_k]
        return documents

    @staticmethod
    def _annotate_retrieval_layer(
        documents: list[RetrievedDocument], retrieval_layer: str | None
    ) -> list[RetrievedDocument]:
        if not retrieval_layer:
            return documents
        for doc in documents:
            metadata = dict(doc.retrieval_metadata or {})
            metadata["retrieval_layer"] = retrieval_layer
            doc.retrieval_metadata = metadata
        return documents

    def run(
        self, text: str | Document, top_k: Optional[int] = None, **kwargs
    ) -> list[RetrievedDocument]:
        """Retrieve a list of documents from vector store

        Args:
            text: the text to retrieve similar documents
            top_k: number of top similar documents to return

        Returns:
            list[RetrievedDocument]: list of retrieved documents
        """
        if top_k is None:
            top_k = self.top_k

        do_extend = kwargs.pop("do_extend", False)
        thumbnail_count = kwargs.pop("thumbnail_count", 3)
        query_variants = kwargs.pop("query_variants", None)
        retrieval_layer = kwargs.pop("retrieval_layer", None)

        if do_extend:
            top_k_first_round = top_k * self.first_round_top_k_mult
        else:
            top_k_first_round = top_k

        if self.doc_store is None:
            raise ValueError(
                "doc_store is not provided. Please provide a doc_store to "
                "retrieve the documents"
            )

        vector_candidates: list[RetrievedDocument] = []
        text_candidates: list[RetrievedDocument] = []
        fusion_query_candidates: list[dict] = []
        # TODO: should declare scope directly in the run params
        scope = kwargs.pop("scope", None)
        normalized_queries: list[str] = []
        for query in query_variants or []:
            normalized = " ".join(str(query).split())
            if normalized and normalized not in normalized_queries:
                normalized_queries.append(normalized)

        if len(normalized_queries) > 1:
            result, vector_candidates, text_candidates, fusion_query_candidates = (
                self._run_fusion_retrieval(
                    query_variants=normalized_queries,
                    top_k_first_round=top_k_first_round,
                    scope=scope,
                    **kwargs,
                )
            )
        else:
            result, vector_candidates, text_candidates = self._run_single_retrieval(
                text,
                top_k_first_round=top_k_first_round,
                scope=scope,
                **kwargs,
            )

        self._annotate_retrieval_layer(result, retrieval_layer)
        self._annotate_retrieval_layer(vector_candidates, retrieval_layer)
        self._annotate_retrieval_layer(text_candidates, retrieval_layer)
        for item in fusion_query_candidates:
            self._annotate_retrieval_layer(item.get("vector_docs") or [], retrieval_layer)
            self._annotate_retrieval_layer(item.get("text_docs") or [], retrieval_layer)
            self._annotate_retrieval_layer(item.get("fused_docs") or [], retrieval_layer)

        trace_recorder = None
        try:
            from ktem.trace import get_active_recorder

            trace_recorder = get_active_recorder()
        except Exception:
            trace_recorder = None

        before_rerank = list(result)
        rerank_started = time.perf_counter()
        rerank_enabled = bool(self.rerankers and text)
        if trace_recorder:
            trace_recorder.record_retrieval_candidates(
                vector_docs=vector_candidates,
                text_docs=text_candidates,
                fused_docs=before_rerank,
                fusion_query_candidates=fusion_query_candidates,
            )
        if rerank_enabled:
            for reranker in self.rerankers:
                # if reranker is LLMReranking, limit the document with top_k items only
                if isinstance(reranker, LLMReranking):
                    result = self._filter_docs(result, top_k=top_k)
                result = reranker.run(documents=result, query=text)
        if trace_recorder:
            if rerank_enabled:
                trace_recorder.add_duration(
                    "rerank", int((time.perf_counter() - rerank_started) * 1000)
                )
            trace_recorder.record_rerank(
                before_rerank,
                result,
                rerank_enabled=rerank_enabled,
            )

        result = self._filter_docs(result, top_k=top_k)
        print(f"Got raw {len(result)} retrieved documents")

        # add page thumbnails to the result if exists
        thumbnail_doc_ids: set[str] = set()
        # we should copy the text from retrieved text chunk
        # to the thumbnail to get relevant LLM score correctly
        text_thumbnail_docs: dict[str, RetrievedDocument] = {}

        non_thumbnail_docs = []
        raw_thumbnail_docs = []
        for doc in result:
            if doc.metadata.get("type") == "thumbnail":
                # change type to image to display on UI
                doc.metadata["type"] = "image"
                raw_thumbnail_docs.append(doc)
                continue
            if (
                "thumbnail_doc_id" in doc.metadata
                and len(thumbnail_doc_ids) < thumbnail_count
            ):
                thumbnail_id = doc.metadata["thumbnail_doc_id"]
                thumbnail_doc_ids.add(thumbnail_id)
                text_thumbnail_docs[thumbnail_id] = doc
            else:
                non_thumbnail_docs.append(doc)

        linked_thumbnail_docs = self.doc_store.get(list(thumbnail_doc_ids))
        print(
            "thumbnail docs",
            len(linked_thumbnail_docs),
            "non-thumbnail docs",
            len(non_thumbnail_docs),
            "raw-thumbnail docs",
            len(raw_thumbnail_docs),
        )
        additional_docs = []

        for thumbnail_doc in linked_thumbnail_docs:
            text_doc = text_thumbnail_docs[thumbnail_doc.doc_id]
            doc_dict = thumbnail_doc.to_dict()
            doc_dict["_id"] = text_doc.doc_id
            doc_dict["content"] = text_doc.content
            doc_dict["metadata"]["type"] = "image"
            for key in text_doc.metadata:
                if key not in doc_dict["metadata"]:
                    doc_dict["metadata"][key] = text_doc.metadata[key]

            additional_docs.append(RetrievedDocument(**doc_dict, score=text_doc.score))

        result = additional_docs + non_thumbnail_docs

        if not result:
            # return output from raw retrieved thumbnails
            result = self._filter_docs(raw_thumbnail_docs, top_k=thumbnail_count)

        return result


class TextVectorQA(BaseComponent):
    retrieving_pipeline: BaseRetrieval
    qa_pipeline: BaseComponent

    def run(self, question, **kwargs):
        retrieved_documents = self.retrieving_pipeline(question, **kwargs)
        return self.qa_pipeline(question, retrieved_documents, **kwargs)
