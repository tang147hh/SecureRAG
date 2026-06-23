import { Check, Copy, FileSearch, FileText } from "lucide-react";
import { useState } from "react";
import { apiClient } from "../api/client";
import type { ChatMessage, RagTraceDetail } from "../api/types";
import { IconButton } from "./IconButton";
import { TracePanel } from "./TracePanel";

interface MessageBubbleProps {
  message: ChatMessage;
}

export function MessageBubble({ message }: MessageBubbleProps) {
  const [trace, setTrace] = useState<RagTraceDetail | null>();
  const [traceLoading, setTraceLoading] = useState(false);
  const [traceError, setTraceError] = useState<string>();
  const isAssistant = message.role === "assistant";
  const isSystem = message.role === "system";
  const isLoading = message.status === "loading" || message.status === "streaming";
  const copyMessage = () => {
    void navigator.clipboard.writeText(message.content);
  };
  const loadTrace = () => {
    if (!isAssistant || traceLoading || trace !== undefined) return;
    setTraceLoading(true);
    setTraceError(undefined);
    apiClient
      .getMessageTrace(message.id)
      .then(setTrace)
      .catch((error: unknown) => {
        setTraceError(error instanceof Error ? error.message : "Trace 加载失败。");
      })
      .finally(() => setTraceLoading(false));
  };

  return (
    <article className={`message-row message-row--${message.role}`}>
      <div className="message-avatar">{isAssistant ? "AI" : isSystem ? "!" : "U"}</div>
      <div className="message-shell">
        <div className="message-meta">
          <span>{isAssistant ? "SecureRAG Assistant" : isSystem ? "System" : "You"}</span>
          <small>
            {new Intl.DateTimeFormat("zh-CN", {
              hour: "2-digit",
              minute: "2-digit",
            }).format(new Date(message.createdAt))}
          </small>
          {isLoading ? <em>streaming</em> : null}
        </div>
        <div className="message-bubble">
          {message.content ? (
            <p>{message.content}</p>
          ) : (
            <div className="typing-indicator">
              <span />
              <span />
              <span />
            </div>
          )}
        </div>
        {isAssistant && message.citations?.length ? (
          <details className="message-citations">
            <summary>
              <span>
                <FileText size={15} />
                引用依据
              </span>
              <small>{message.citations.length} 个证据块</small>
            </summary>
            <div className="message-citations__list">
              {message.citations.map((citation, index) => (
                <article className="message-citation" key={citation.id}>
                  <div>
                    <strong>[{index + 1}] {citation.title}</strong>
                    <small>
                      {citation.page ? `Page ${citation.page}` : "Snippet"}
                      {citation.score > 0
                        ? ` · ${Math.round(citation.score * 100)}% match`
                        : ""}
                    </small>
                  </div>
                  <p>{citation.excerpt}</p>
                </article>
              ))}
            </div>
          </details>
        ) : null}
        {isAssistant ? (
          <details className="message-trace" onToggle={(event) => {
            if (event.currentTarget.open) loadTrace();
          }}>
            <summary>
              <span>
                <FileSearch size={15} />
                Trace
              </span>
              <small>{trace?.status ?? "RAG 链路"}</small>
            </summary>
            <TracePanel trace={trace} loading={traceLoading} error={traceError} />
          </details>
        ) : null}
        <div className="message-actions">
          <IconButton label="复制消息" onClick={copyMessage}>
            <Copy size={14} />
          </IconButton>
          {!isAssistant && !isSystem ? (
            <IconButton label="已发送">
              <Check size={14} />
            </IconButton>
          ) : null}
        </div>
      </div>
    </article>
  );
}
