import { Check, Copy, FileSearch, FileText } from "lucide-react";
import type { ReactNode } from "react";
import { useState } from "react";
import { apiClient } from "../api/client";
import type { ChatMessage, RagTraceDetail } from "../api/types";
import { IconButton } from "./IconButton";
import { TracePanel } from "./TracePanel";

interface MessageBubbleProps {
  message: ChatMessage;
  onOpenReferences?: (messageId: string) => void;
}

export function MessageBubble({ message, onOpenReferences }: MessageBubbleProps) {
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
              timeZone: "Asia/Shanghai",
              hour: "2-digit",
              minute: "2-digit",
            }).format(new Date(message.createdAt))}
          </small>
          {isLoading ? <em>streaming</em> : null}
        </div>
        <button
          className={`message-bubble ${isAssistant ? "message-bubble--clickable" : ""}`}
          type="button"
          onClick={() => {
            if (isAssistant && !isLoading) onOpenReferences?.(message.id);
          }}
          disabled={!isAssistant || isLoading}
          aria-label={isAssistant ? "显示这条回答的知识引用" : undefined}
        >
          {message.content ? (
            <MarkdownText text={message.content} />
          ) : (
            <div className="typing-indicator">
              <span />
              <span />
              <span />
            </div>
          )}
        </button>
        {isAssistant && message.citations?.length ? (
          <details className="message-citations">
            <summary>
              <button
                className="message-citations__open"
                type="button"
                onClick={(event) => {
                  event.preventDefault();
                  onOpenReferences?.(message.id);
                }}
              >
                <FileText size={15} />
                引用依据
              </button>
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

function MarkdownText({ text }: { text: string }) {
  const paragraphs = text.split(/\n{2,}/);
  return (
    <div className="message-markdown">
      {paragraphs.map((paragraph, paragraphIndex) => (
        <p key={`${paragraph}-${paragraphIndex}`}>
          {renderInlineMarkdown(paragraph)}
        </p>
      ))}
    </div>
  );
}

function renderInlineMarkdown(text: string) {
  const parts: ReactNode[] = [];
  const pattern = /(\*\*([^*\n]+)\*\*)|(\n)/g;
  let lastIndex = 0;
  let match: RegExpExecArray | null;

  while ((match = pattern.exec(text)) !== null) {
    if (match.index > lastIndex) {
      parts.push(text.slice(lastIndex, match.index));
    }
    if (match[3]) {
      parts.push(<br key={`br-${match.index}`} />);
    } else {
      parts.push(<strong key={`strong-${match.index}`}>{match[2]}</strong>);
    }
    lastIndex = pattern.lastIndex;
  }

  if (lastIndex < text.length) {
    parts.push(text.slice(lastIndex));
  }
  return parts;
}
