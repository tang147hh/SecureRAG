import type { ChatMessage } from "../api/types";
import { EmptyState } from "./EmptyState";
import { MessageBubble } from "./MessageBubble";

interface MessageListProps {
  messages: ChatMessage[];
  onOpenMessageReferences?: (messageId: string) => void;
}

export function MessageList({ messages, onOpenMessageReferences }: MessageListProps) {
  if (messages.length === 0) {
    return <EmptyState />;
  }

  return (
    <div className="message-list" aria-live="polite">
      {messages.map((message) => (
        <MessageBubble
          key={message.id}
          message={message}
          onOpenReferences={onOpenMessageReferences}
        />
      ))}
    </div>
  );
}
