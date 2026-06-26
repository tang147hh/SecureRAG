from __future__ import annotations

import math
from contextlib import suppress
from collections.abc import Mapping
from typing import Any
from urllib.request import urlopen


RAGAS_METRIC_NAMES = (
    "faithfulness",
    "answer_relevancy",
    "context_precision",
    "context_recall",
)

_RESULT_ALIASES = {
    "answer_relevancy": ("response_relevancy", "answer_relevance"),
    "context_precision": (
        "llm_context_precision_with_reference",
        "llm_context_precision_without_reference",
    ),
    "context_recall": ("llm_context_recall",),
}


def extract_ragas_contexts(trace_data: dict[str, Any] | None) -> list[str]:
    if not isinstance(trace_data, dict):
        return []

    chunks = trace_data.get("context_chunks") or trace_data.get(
        "candidate_chunks_after_rerank"
    )
    if not isinstance(chunks, list):
        return []

    contexts: list[str] = []
    seen: set[str] = set()
    for chunk in chunks:
        text = _chunk_text(chunk)
        if text and text not in seen:
            contexts.append(text)
            seen.add(text)
    return contexts


def calculate_ragas_metrics(
    question: str,
    answer: str,
    contexts: list[str],
    ground_truth: str | None = None,
) -> dict[str, Any]:
    output: dict[str, Any] = {
        "ragas_enabled": False,
        "faithfulness": None,
        "answer_relevancy": None,
        "context_precision": None,
        "context_recall": None,
    }
    question = (question or "").strip()
    answer = (answer or "").strip()
    ground_truth = (ground_truth or "").strip() or None
    contexts = [str(context).strip() for context in contexts or [] if str(context).strip()]

    metric_names = _selected_metric_names(
        question=question,
        answer=answer,
        contexts=contexts,
        ground_truth=ground_truth,
    )
    if not metric_names:
        output["ragas_enabled"] = True
        return output

    try:
        from datasets import Dataset
        from ragas import RunConfig, evaluate
    except Exception as exc:
        output["ragas_error"] = f"RAGAS dependency unavailable: {exc}"
        return output

    try:
        llm, embeddings = _build_ragas_runtime()
        metrics = _load_metrics(metric_names)
        errors: list[str] = []
        for metric in metrics:
            metric_name = str(getattr(metric, "name", "") or "")
            if metric_name == "answer_relevancy" and embeddings is None:
                errors.append("answer_relevancy skipped: embeddings unavailable")
                continue
            scores = _evaluate_with_compatible_schema(
                evaluate=evaluate,
                dataset_cls=Dataset,
                metrics=[metric],
                question=question,
                answer=answer,
                contexts=contexts,
                ground_truth=ground_truth,
                llm=llm,
                embeddings=embeddings,
                run_config=RunConfig(timeout=90, max_retries=1, max_workers=2),
            )
            output[metric_name] = _normalize_score(_score_value(scores, metric_name))
        output["ragas_enabled"] = any(output[name] is not None for name in RAGAS_METRIC_NAMES)
        if errors:
            output["ragas_error"] = "; ".join(errors)
        elif not output["ragas_enabled"]:
            output["ragas_error"] = "RAGAS evaluation returned no scores"
        else:
            output.pop("ragas_error", None)
    except Exception as exc:
        output["ragas_error"] = f"RAGAS evaluation failed: {exc}"
    return output


def _selected_metric_names(
    *,
    question: str,
    answer: str,
    contexts: list[str],
    ground_truth: str | None,
) -> list[str]:
    metric_names: list[str] = []
    if question and answer and contexts:
        metric_names.append("faithfulness")
    if question and answer:
        metric_names.append("answer_relevancy")
    if question and contexts and ground_truth:
        metric_names.extend(["context_precision", "context_recall"])
    return metric_names


def _chunk_text(chunk: Any) -> str:
    if isinstance(chunk, str):
        return chunk.strip()
    if not isinstance(chunk, Mapping):
        return ""

    for key in ("text", "content", "page_content", "excerpt"):
        value = chunk.get(key)
        if value:
            return str(value).strip()

    metadata = chunk.get("metadata")
    if isinstance(metadata, Mapping):
        for key in ("text", "content", "page_content", "window", "excerpt"):
            value = metadata.get(key)
            if value:
                return str(value).strip()
    return ""


def _load_metrics(metric_names: list[str]) -> list[Any]:
    try:
        from ragas.metrics.collections import (  # type: ignore[import-not-found]
            AnswerRelevancy,
            Faithfulness,
            LLMContextPrecisionWithReference,
            LLMContextRecall,
        )

        metric_classes = {
            "faithfulness": Faithfulness,
            "answer_relevancy": AnswerRelevancy,
            "context_precision": LLMContextPrecisionWithReference,
            "context_recall": LLMContextRecall,
        }
        return [_prepare_metric(metric_classes[name]()) for name in metric_names]
    except Exception:
        pass

    try:
        from ragas.metrics import (
            AnswerRelevancy,
            ContextPrecision,
            ContextRecall,
            Faithfulness,
        )

        metric_classes = {
            "faithfulness": Faithfulness,
            "answer_relevancy": AnswerRelevancy,
            "context_precision": ContextPrecision,
            "context_recall": ContextRecall,
        }
        return [_prepare_metric(metric_classes[name]()) for name in metric_names]
    except Exception:
        from ragas.metrics import (
            answer_relevancy,
            context_precision,
            context_recall,
            faithfulness,
        )

        legacy_metrics = {
            "faithfulness": faithfulness,
            "answer_relevancy": answer_relevancy,
            "context_precision": context_precision,
            "context_recall": context_recall,
        }
        return [_prepare_metric(legacy_metrics[name]) for name in metric_names]


def _prepare_metric(metric: Any) -> Any:
    if getattr(metric, "name", "") == "answer_relevancy" and hasattr(
        metric, "strictness"
    ):
        metric.strictness = 1
    return metric


def _evaluate_with_compatible_schema(
    *,
    evaluate: Any,
    dataset_cls: Any,
    metrics: list[Any],
    question: str,
    answer: str,
    contexts: list[str],
    ground_truth: str | None,
    llm: Any = None,
    embeddings: Any = None,
    run_config: Any = None,
) -> dict[str, Any]:
    last_error: Exception | None = None
    for data in _ragas_dataset_candidates(question, answer, contexts, ground_truth):
        try:
            result = evaluate(
                dataset_cls.from_dict(data),
                metrics=metrics,
                llm=llm,
                embeddings=embeddings,
                run_config=run_config,
                raise_exceptions=False,
                show_progress=False,
            )
            return _result_scores(result)
        except TypeError:
            try:
                result = evaluate(
                    dataset_cls.from_dict(data),
                    metrics=metrics,
                    llm=llm,
                    embeddings=embeddings,
                    run_config=run_config,
                )
                return _result_scores(result)
            except Exception as exc:
                last_error = exc
        except Exception as exc:
            last_error = exc
    if last_error is not None:
        raise last_error
    return {}


def _ragas_dataset_candidates(
    question: str,
    answer: str,
    contexts: list[str],
    ground_truth: str | None,
) -> list[dict[str, list[Any]]]:
    modern = {
        "user_input": [question],
        "response": [answer],
        "retrieved_contexts": [contexts],
    }
    legacy = {
        "question": [question],
        "answer": [answer],
        "contexts": [contexts],
    }
    if ground_truth:
        modern["reference"] = [ground_truth]
        legacy["ground_truth"] = [ground_truth]
    return [modern, legacy]


def _result_scores(result: Any) -> dict[str, Any]:
    if isinstance(result, Mapping):
        return dict(result)

    scores = getattr(result, "scores", None)
    if isinstance(scores, list) and scores:
        first = scores[0]
        if isinstance(first, Mapping):
            return dict(first)
    if isinstance(scores, Mapping):
        return dict(scores)

    if hasattr(result, "to_pandas"):
        frame = result.to_pandas()
        if len(frame.index):
            return dict(frame.iloc[0].to_dict())

    try:
        return dict(result)
    except Exception:
        return {}


def _normalize_score(value: Any) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(number) else number


def _score_value(scores: dict[str, Any], name: str) -> Any:
    if name in scores:
        return scores.get(name)
    for alias in _RESULT_ALIASES.get(name, ()):
        if alias in scores:
            return scores.get(alias)
    return None


def _build_ragas_runtime() -> tuple[Any, Any]:
    return _build_ragas_llm(), _build_ragas_embeddings()


def _build_ragas_llm() -> Any:
    try:
        from decouple import config
        from langchain_openai import ChatOpenAI
        from ragas.llms import LangchainLLMWrapper
    except Exception as exc:
        raise RuntimeError(f"RAGAS LLM dependencies unavailable: {exc}") from exc

    api_key = config("DEEPSEEK_API_KEY", default="").strip()
    if not api_key:
        raise RuntimeError("DEEPSEEK_API_KEY is not configured for RAGAS")

    llm = ChatOpenAI(
        api_key=api_key,
        base_url=config("DEEPSEEK_API_BASE", default="https://api.deepseek.com"),
        model=config("DEEPSEEK_CHAT_MODEL", default="deepseek-chat"),
        temperature=0,
        timeout=60,
        max_retries=1,
    )
    return LangchainLLMWrapper(llm)


def _build_ragas_embeddings() -> Any | None:
    try:
        from decouple import config
        from langchain_openai import OpenAIEmbeddings
        from ragas.embeddings import LangchainEmbeddingsWrapper
    except Exception:
        return None

    base_url = config("KH_OLLAMA_URL", default="http://localhost:11434/v1/").rstrip("/")
    if _ollama_is_available(base_url):
        embeddings = OpenAIEmbeddings(
            api_key=config("LOCAL_EMBEDDINGS_API_KEY", default="ollama"),
            base_url=base_url,
            model=config("LOCAL_MODEL_EMBEDDINGS", default="nomic-embed-text"),
            timeout=30,
            max_retries=1,
        )
        return LangchainEmbeddingsWrapper(embeddings)

    with suppress(Exception):
        from langchain_community.embeddings import HuggingFaceEmbeddings

        local_model = config(
            "KH_RAGAS_LOCAL_EMBEDDING_MODEL",
            default="sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
        )
        embeddings = HuggingFaceEmbeddings(
            model_name=local_model,
            cache_folder=config("KH_RAGAS_LOCAL_EMBEDDING_CACHE", default=None),
            encode_kwargs={"normalize_embeddings": True},
        )
        return LangchainEmbeddingsWrapper(embeddings)
    return None


def _ollama_is_available(base_url: str) -> bool:
    root_url = base_url.removesuffix("/v1")
    with suppress(Exception):
        with urlopen(f"{root_url}/api/tags", timeout=2) as response:
            return 200 <= response.status < 500
    return False
