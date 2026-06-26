import { Edit3, MessageSquare, Plus, Trash2 } from "lucide-react";
import type { Conversation } from "../api/types";
import { IconButton } from "./IconButton";

interface ConversationListProps {
  conversations: Conversation[];
  activeConversationId?: string;
  onSelect: (conversationId: string) => void;
  onCreate: () => void;
  onDelete: (conversationId: string) => void;
  onRename: (conversationId: string) => void;
}

const relativeTime = (isoDate: string) =>
  new Intl.DateTimeFormat("zh-CN", {
    timeZone: "Asia/Shanghai",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(isoDate));

export function ConversationList({
  conversations,
  activeConversationId,
  onSelect,
  onCreate,
  onDelete,
  onRename,
}: ConversationListProps) {
  return (
    <section className="sidebar-section">
      <div className="section-heading">
        <div>
          <span>会话</span>
          <small>{conversations.length} 个工作流</small>
        </div>
        <IconButton label="新建会话" variant="accent" onClick={onCreate}>
          <Plus size={16} />
        </IconButton>
      </div>

      <div className="conversation-list">
        {conversations.map((conversation) => (
          <article
            key={conversation.id}
            className={`conversation-card ${
              conversation.id === activeConversationId ? "is-active" : ""
            }`}
          >
            <button type="button" onClick={() => onSelect(conversation.id)}>
              <MessageSquare size={16} />
              <strong>{conversation.title}</strong>
              <small>
                {relativeTime(conversation.updatedAt)} · {conversation.messageCount}
              </small>
            </button>
            <div className="conversation-card__actions">
              <IconButton label="重命名会话" onClick={() => onRename(conversation.id)}>
                <Edit3 size={14} />
              </IconButton>
              <IconButton label="删除会话" variant="danger" onClick={() => onDelete(conversation.id)}>
                <Trash2 size={14} />
              </IconButton>
            </div>
          </article>
        ))}
      </div>
    </section>
  );
}
