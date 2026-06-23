import { Edit3, MessageSquare, Plus, Trash2 } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import type { Conversation } from "../api/types";
import { IconButton } from "./IconButton";

interface ConversationSwitcherProps {
  conversations: Conversation[];
  activeConversationId?: string;
  onSelect: (conversationId: string) => void;
  onCreate: () => void;
  onDelete: (conversationId: string) => void;
  onRename: (conversationId: string) => void;
}

const relativeTime = (isoDate: string) =>
  new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(isoDate));

export function ConversationSwitcher({
  conversations,
  activeConversationId,
  onSelect,
  onCreate,
  onDelete,
  onRename,
}: ConversationSwitcherProps) {
  const [open, setOpen] = useState(false);
  const switcherRef = useRef<HTMLDivElement | null>(null);
  const activeConversation = conversations.find(
    (conversation) => conversation.id === activeConversationId,
  );

  useEffect(() => {
    if (!open) return;

    const handlePointerDown = (event: PointerEvent) => {
      if (!switcherRef.current?.contains(event.target as Node)) {
        setOpen(false);
      }
    };

    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };

    document.addEventListener("pointerdown", handlePointerDown);
    document.addEventListener("keydown", handleKeyDown);
    return () => {
      document.removeEventListener("pointerdown", handlePointerDown);
      document.removeEventListener("keydown", handleKeyDown);
    };
  }, [open]);

  const handleCreate = () => {
    setOpen(false);
    onCreate();
  };

  return (
    <div className="conversation-switcher" ref={switcherRef}>
      <button
        className="conversation-trigger"
        type="button"
        aria-expanded={open}
        aria-haspopup="dialog"
        onClick={() => setOpen((current) => !current)}
      >
        <MessageSquare size={18} />
        <span>
          <small>当前会话</small>
          <strong>{activeConversation?.title ?? "未选择会话"}</strong>
        </span>
      </button>

      {open ? (
        <div className="conversation-popover" role="dialog" aria-label="切换会话">
          <div className="conversation-popover__head">
            <div>
              <strong>会话列表</strong>
              <small>{conversations.length} 个会话</small>
            </div>
            <IconButton label="新建会话" variant="accent" onClick={handleCreate}>
              <Plus size={16} />
            </IconButton>
          </div>

          <div className="conversation-menu">
            {conversations.map((conversation) => (
              <article
                key={conversation.id}
                className={`conversation-menu__item ${
                  conversation.id === activeConversationId ? "is-active" : ""
                }`}
              >
                <button
                  type="button"
                  onClick={() => {
                    onSelect(conversation.id);
                    setOpen(false);
                  }}
                >
                  <span>{conversation.title}</span>
                  <small>
                    {relativeTime(conversation.updatedAt)} · {conversation.messageCount}
                  </small>
                </button>
                <div className="conversation-menu__actions">
                  <IconButton label="重命名会话" onClick={() => onRename(conversation.id)}>
                    <Edit3 size={14} />
                  </IconButton>
                  <IconButton
                    label="删除会话"
                    variant="danger"
                    onClick={() => onDelete(conversation.id)}
                  >
                    <Trash2 size={14} />
                  </IconButton>
                </div>
              </article>
            ))}
          </div>
        </div>
      ) : null}
    </div>
  );
}
