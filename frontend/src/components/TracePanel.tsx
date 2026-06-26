import { Activity, CheckCircle2, Clock, PenLine, ShieldCheck } from "lucide-react";
import type { ReactNode } from "react";
import type { RagTraceChunk, RagTraceDetail } from "../api/types";

interface TracePanelProps {
  trace?: RagTraceDetail | null;
  loading?: boolean;
  error?: string;
}

export function TracePanel({ trace, loading = false, error }: TracePanelProps) {
  if (loading) {
    return <div className="trace-empty">Trace 加载中...</div>;
  }
  if (error) {
    return <div className="trace-empty">{error}</div>;
  }
  if (!trace) {
    return <div className="trace-empty">该回答没有 Trace 数据。</div>;
  }

  const data = trace.data ?? {};
  const acl = data.acl ?? {};
  const params = data.retrieval_params ?? {};
  const enhancement = data.retrieval_enhancement ?? {};
  const durations = data.durations_ms ?? {};
  const tokens = data.tokens ?? {};
  const verification = data.answer_verification;
  const fusionQueries = data.rag_fusion?.queries?.length
    ? data.rag_fusion.queries
    : ((enhancement.fusion_queries as string[] | undefined) ?? []);
  const fusionEnabled =
    data.rag_fusion?.enabled === true ||
    enhancement.strategy === "fusion" ||
    params.enhancement === "fusion";
  const traceErrors = (data.errors ?? []) as Array<Record<string, unknown>>;
  const fusionErrors = traceErrors.filter((item) =>
    String(item.stage ?? "").includes("fusion"),
  );

  return (
    <div className="trace-panel">
      {fusionQueries.length ? (
        <TraceSection icon={<PenLine size={14} />} title="RAG-Fusion 问题拆分">
          <ol className="trace-query-list trace-query-list-highlight">
            {fusionQueries.map((query, index) => (
              <li key={`${query}-${index}`}>
                <span>{fusionQueryLabel(index)}</span>
                <p>{query}</p>
              </li>
            ))}
          </ol>
        </TraceSection>
      ) : fusionEnabled ? (
        <TraceSection icon={<PenLine size={14} />} title="RAG-Fusion 问题拆分">
          <p className="trace-warning">
            未生成查询视角。请查看下方错误信息，通常是查询生成模型调用失败。
          </p>
        </TraceSection>
      ) : null}

      {fusionErrors.length ? (
        <TraceSection icon={<PenLine size={14} />} title="RAG-Fusion 错误">
          <div className="trace-errors">
            {fusionErrors.map((item, index) => (
              <article className="trace-error" key={`${item.stage}-${index}`}>
                <strong>{String(item.stage ?? "fusion_error")}</strong>
                <p>{String(item.message ?? item.type ?? "未知错误")}</p>
              </article>
            ))}
          </div>
        </TraceSection>
      ) : null}

      <TraceSection icon={<Activity size={14} />} title="检索参数">
        <KeyValueGrid value={params} />
      </TraceSection>

      <TraceSection icon={<PenLine size={14} />} title="检索增强">
        <KeyValueGrid
          value={{
            strategy: enhancement.strategy ?? params.enhancement ?? "none",
            original_question: enhancement.original_question ?? data.original_question ?? data.question,
            rewritten_question:
              enhancement.rewritten_question ??
              data.query_rewrite?.rewritten_question,
            hyde_document: enhancement.hyde_document ?? data.hyde?.document,
            fusion_query_count: fusionQueries.length,
            retrieval_query: enhancement.retrieval_query ?? data.retrieval_query,
          }}
        />
      </TraceSection>

      <TraceSection icon={<ShieldCheck size={14} />} title="权限过滤">
        <KeyValueGrid
          value={{
            pre_filter_sources: acl.pre_filter_source_count,
            post_filter_sources: acl.post_filter_source_count,
            pre_filter_chunks: acl.pre_filter_chunk_count,
            post_filter_chunks: acl.post_filter_chunk_count,
            filtered_sources: acl.filtered_source_count,
            reason_summary: JSON.stringify(acl.filtered_reason_summary ?? {}),
          }}
        />
        <TraceList
          title="过滤原因"
          items={(acl.filtered_reasons as Array<Record<string, unknown>> | undefined)?.map(
            (item) => ({
              chunk_id: String(item.reason ?? ""),
              source_id: String(item.reason ?? ""),
              excerpt: `count ${String(item.count ?? 0)}`,
            }),
          )}
        />
      </TraceSection>

      <TraceSection icon={<Clock size={14} />} title="耗时与 Tokens">
        <KeyValueGrid value={{ ...durations, ...tokens }} />
      </TraceSection>

      <TraceSection icon={<CheckCircle2 size={14} />} title="答案证据校验">
        {verification ? (
          <>
          <KeyValueGrid
            value={{
              evidence_coverage: formatPercent(verification.evidence_coverage),
              supported: verification.supported_count,
              unsupported: verification.unsupported_count,
              insufficient: verification.insufficient_count,
              final_action: verification.final_action,
              retry_triggered: verification.retry?.triggered,
              retry_query: verification.retry?.query,
              gate_reason: verification.gate?.reason,
            }}
          />
          <SentenceSupportList checks={verification.checks} />
          <TraceList
            title="二次检索补充 Context"
            items={verification.retry?.added_context_chunks}
          />
          </>
        ) : (
          <p className="trace-warning">
            当前 Trace 没有 answer_verification 数据。请重启后端服务后重新提问，
            或选择一条新生成的回答再展开 Trace。
          </p>
        )}
      </TraceSection>

      <TraceList title="Vector 候选" items={data.vector_candidate_chunks} />
      <TraceList title="Text 候选" items={data.text_candidate_chunks} />
      <FusionCandidateList items={data.fusion_query_candidates} />
      <TraceList
        title="Hybrid 融合"
        items={data.fused_candidate_chunks ?? data.candidate_chunks_before_rerank}
      />
      <TraceList
        title="Rerank 后"
        items={data.reranked_candidate_chunks ?? data.candidate_chunks_after_rerank}
      />
      <TraceList title="最终 Context" items={data.context_chunks} />
      <TraceList title="最终引用" items={data.citation_chunks} />
    </div>
  );
}

function SentenceSupportList({
  checks,
}: {
  checks?: NonNullable<RagTraceDetail["data"]["answer_verification"]>["checks"];
}) {
  if (!checks?.length) {
    return <p className="trace-muted">无逐句校验数据</p>;
  }
  return (
    <div className="trace-sentence-checks">
      {checks.map((check, index) => (
        <article className={`trace-sentence trace-sentence-${check.status}`} key={index}>
          <strong>
            S{check.index ?? index + 1} · {check.status ?? "unknown"}
            {typeof check.score === "number" ? ` · ${check.score.toFixed(2)}` : ""}
          </strong>
          <p>{check.sentence}</p>
          {check.reason ? <small>{check.reason}</small> : null}
          {check.evidence?.length ? (
            <ul>
              {check.evidence.slice(0, 2).map((item, evidenceIndex) => (
                <li key={`${item.chunk_id}-${evidenceIndex}`}>
                  <span>{item.source_name || item.source_id || item.chunk_id}</span>
                  {item.page_label ? <span> · page {item.page_label}</span> : null}
                  {item.overlap_terms?.length ? (
                    <span> · {item.overlap_terms.slice(0, 6).join(", ")}</span>
                  ) : null}
                </li>
              ))}
            </ul>
          ) : null}
        </article>
      ))}
    </div>
  );
}

function FusionCandidateList({
  items,
}: {
  items?: RagTraceDetail["data"]["fusion_query_candidates"];
}) {
  if (!items?.length) return null;
  return (
    <section className="trace-section">
      <h4>每个查询的候选</h4>
      <div className="trace-query-candidates">
        {items.map((item, index) => (
          <article className="trace-query-candidate" key={`${item.query}-${index}`}>
            <strong>
              Q{item.query_index ?? index + 1}. {item.query}
            </strong>
            <TraceList
              title="Vector"
              items={item.vector_candidate_chunks?.slice(0, 5)}
              compact
            />
            <TraceList
              title="Text"
              items={item.text_candidate_chunks?.slice(0, 5)}
              compact
            />
            <TraceList
              title="该查询融合"
              items={item.fused_candidate_chunks?.slice(0, 5)}
              compact
            />
          </article>
        ))}
      </div>
    </section>
  );
}

function fusionQueryLabel(index: number) {
  const labels = ["原问题", "时间边界", "身份边界", "费用标准", "审批流程"];
  return `Q${index + 1} · ${labels[index] ?? "补充视角"}`;
}

function TraceSection({
  icon,
  title,
  children,
}: {
  icon: ReactNode;
  title: string;
  children: ReactNode;
}) {
  return (
    <section className="trace-section">
      <h4>
        {icon}
        {title}
      </h4>
      {children}
    </section>
  );
}

function KeyValueGrid({ value }: { value: Record<string, unknown> }) {
  const entries = Object.entries(value).filter(([, item]) => item !== undefined);
  if (!entries.length) {
    return <p className="trace-muted">无数据</p>;
  }
  return (
    <dl className="trace-kv">
      {entries.map(([key, item]) => (
        <div key={key}>
          <dt>{key}</dt>
          <dd>{formatValue(item)}</dd>
        </div>
      ))}
    </dl>
  );
}

function TraceList({
  title,
  items,
  compact = false,
}: {
  title: string;
  items?: RagTraceChunk[];
  compact?: boolean;
}) {
  if (!items?.length) {
    return (
      <section className={compact ? "trace-subsection" : "trace-section"}>
        <h4>{title}</h4>
        <p className="trace-muted">无数据</p>
      </section>
    );
  }
  return (
    <section className={compact ? "trace-subsection" : "trace-section"}>
      <h4>{title}</h4>
      <div className="trace-chunks">
        {items.slice(0, compact ? 5 : 12).map((chunk, index) => (
          <article className="trace-chunk" key={`${chunk.chunk_id}-${index}`}>
            <strong>
              {index + 1}. {chunk.source_name || chunk.source_id || "source"}
            </strong>
            <small>
              {chunk.chunk_id}
              {chunk.page_label ? ` · page ${chunk.page_label}` : ""}
              {typeof chunk.score === "number" ? ` · score ${chunk.score.toFixed(3)}` : ""}
              {channelLabel(chunk)}
              {rankLabel(chunk)}
            </small>
            {chunk.excerpt ? <p>{chunk.excerpt}</p> : null}
          </article>
        ))}
      </div>
    </section>
  );
}

function rankLabel(chunk: RagTraceChunk) {
  const parts: string[] = [];
  if (typeof chunk.vector_rank === "number") {
    parts.push(`vector #${chunk.vector_rank}`);
  }
  if (typeof chunk.text_rank === "number") {
    parts.push(`text #${chunk.text_rank}`);
  }
  if (typeof chunk.rrf_score === "number") {
    parts.push(`rrf ${chunk.rrf_score.toFixed(4)}`);
  }
  if (typeof chunk.final_rank === "number") {
    parts.push(`final #${chunk.final_rank}`);
  }
  if (chunk.fusion_query_hits?.length) {
    parts.push(`queries ${chunk.fusion_query_hits.map((item) => `Q${item}`).join(",")}`);
  } else if (typeof chunk.fusion_query_index === "number") {
    parts.push(`Q${chunk.fusion_query_index}`);
  }
  if (typeof chunk.rank_after_rerank === "number") {
    parts.push(`rerank #${chunk.rank_after_rerank}`);
  }
  if (parts.length) {
    return ` · ${parts.join(" · ")}`;
  }
  if (typeof chunk.rank_after_rerank === "number") {
    return ` · rerank #${chunk.rank_after_rerank}`;
  }
  if (typeof chunk.rank_after_fusion === "number") {
    return ` · fused #${chunk.rank_after_fusion}`;
  }
  if (typeof chunk.rank_before_fusion === "number") {
    return ` · raw #${chunk.rank_before_fusion}`;
  }
  return "";
}

function channelLabel(chunk: RagTraceChunk) {
  const channels = chunk.retrieval_channels?.length
    ? chunk.retrieval_channels.join("+")
    : chunk.retrieval_channel;
  return channels ? ` · ${channels}` : "";
}

function formatValue(value: unknown) {
  if (typeof value === "boolean") return value ? "true" : "false";
  if (value === null) return "null";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

function formatPercent(value: unknown) {
  if (typeof value !== "number") return value;
  return `${Math.round(value * 100)}%`;
}
