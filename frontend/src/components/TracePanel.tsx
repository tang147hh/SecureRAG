import { Activity, Clock, ShieldCheck } from "lucide-react";
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
  const durations = data.durations_ms ?? {};
  const tokens = data.tokens ?? {};

  return (
    <div className="trace-panel">
      <TraceSection icon={<Activity size={14} />} title="检索参数">
        <KeyValueGrid value={params} />
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
          title="被过滤 source"
          items={(acl.filtered_reasons as Array<Record<string, unknown>> | undefined)?.map(
            (item) => ({
              chunk_id: String(item.source_id ?? ""),
              source_id: String(item.source_id ?? ""),
              excerpt: String(item.reason ?? ""),
            }),
          )}
        />
      </TraceSection>

      <TraceSection icon={<Clock size={14} />} title="耗时与 Tokens">
        <KeyValueGrid value={{ ...durations, ...tokens }} />
      </TraceSection>

      <TraceList title="Rerank 前候选" items={data.candidate_chunks_before_rerank} />
      <TraceList title="Rerank 后候选" items={data.candidate_chunks_after_rerank} />
      <TraceList title="最终 Context" items={data.context_chunks} />
      <TraceList title="最终引用" items={data.citation_chunks} />
    </div>
  );
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
}: {
  title: string;
  items?: RagTraceChunk[];
}) {
  if (!items?.length) {
    return (
      <section className="trace-section">
        <h4>{title}</h4>
        <p className="trace-muted">无数据</p>
      </section>
    );
  }
  return (
    <section className="trace-section">
      <h4>{title}</h4>
      <div className="trace-chunks">
        {items.slice(0, 12).map((chunk, index) => (
          <article className="trace-chunk" key={`${chunk.chunk_id}-${index}`}>
            <strong>
              {index + 1}. {chunk.source_name || chunk.source_id || "source"}
            </strong>
            <small>
              {chunk.chunk_id}
              {chunk.page_label ? ` · page ${chunk.page_label}` : ""}
              {typeof chunk.score === "number" ? ` · score ${chunk.score.toFixed(3)}` : ""}
            </small>
            {chunk.excerpt ? <p>{chunk.excerpt}</p> : null}
          </article>
        ))}
      </div>
    </section>
  );
}

function formatValue(value: unknown) {
  if (typeof value === "boolean") return value ? "true" : "false";
  if (value === null) return "null";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}
