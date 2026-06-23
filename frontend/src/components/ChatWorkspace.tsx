import { Files, PanelRightOpen } from "lucide-react";
import { useEffect, useRef } from "react";
import type { ChatMessage, Conversation } from "../api/types";
import { ChatComposer } from "./ChatComposer";
import { ConversationSwitcher } from "./ConversationSwitcher";
import { IconButton } from "./IconButton";
import { MessageList } from "./MessageList";

interface ChatWorkspaceProps {
  conversation?: Conversation;
  conversations: Conversation[];
  messages: ChatMessage[];
  isSending: boolean;
  selectedFileCount: number;
  onSend: (content: string) => void;
  onUploadFiles: (files: File[]) => void;
  onOpenReferences: () => void;
  onOpenFiles: () => void;
  onSelectConversation: (conversationId: string) => void;
  onCreateConversation: () => void;
  onDeleteConversation: (conversationId: string) => void;
  onRenameConversation: (conversationId: string) => void;
  apiMode: "connecting" | "backend" | "error";
}

export function ChatWorkspace({
  conversation,
  conversations,
  messages,
  isSending,
  selectedFileCount,
  onSend,
  onUploadFiles,
  onOpenReferences,
  onOpenFiles,
  onSelectConversation,
  onCreateConversation,
  onDeleteConversation,
  onRenameConversation,
  apiMode,
}: ChatWorkspaceProps) {
  const scrollRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    const scrollEl = scrollRef.current;
    if (!scrollEl) return;
    scrollEl.scrollTo({
      top: scrollEl.scrollHeight,
      behavior: isSending ? "smooth" : "auto",
    });
  }, [messages, isSending]);

  return (
    <main className="chat-workspace">
      <div className="chat-toolbar">
        <ConversationSwitcher
          conversations={conversations}
          activeConversationId={conversation?.id}
          onSelect={onSelectConversation}
          onCreate={onCreateConversation}
          onDelete={onDeleteConversation}
          onRename={onRenameConversation}
        />
        <div className="chat-toolbar__meta">
          <span className={`api-mode api-mode--${apiMode}`}>
            {apiMode === "backend" ? "Backend linked" : apiMode === "error" ? "Backend error" : "Connecting"}
          </span>
          <button className="chat-file-scope" type="button" onClick={onOpenFiles}>
            <Files size={15} />
            {selectedFileCount ? `${selectedFileCount} files` : "All files"}
          </button>
          <span>{messages.length} messages</span>
          <IconButton className="tablet-only" label="打开知识引用" onClick={onOpenReferences}>
            <PanelRightOpen size={18} />
          </IconButton>
        </div>
      </div>
      <div className="chat-scroll" ref={scrollRef}>
        <MessageList messages={messages} />
      </div>
      <ChatComposer
        disabled={isSending || !conversation}
        selectedFileCount={selectedFileCount}
        onSend={onSend}
        onUploadFiles={onUploadFiles}
      />
    </main>
  );
}
