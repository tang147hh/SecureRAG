import { ChangeEvent, FormEvent, KeyboardEvent, useLayoutEffect, useRef, useState } from "react";
import { AtSign, Paperclip, SendHorizontal, Sparkles } from "lucide-react";
import { IconButton } from "./IconButton";

interface ChatComposerProps {
  disabled: boolean;
  selectedFileCount: number;
  onSend: (content: string) => void;
  onUploadFiles: (files: File[]) => void;
}

export function ChatComposer({
  disabled,
  selectedFileCount,
  onSend,
  onUploadFiles,
}: ChatComposerProps) {
  const [value, setValue] = useState("");
  const textAreaRef = useRef<HTMLTextAreaElement | null>(null);
  const fileInputRef = useRef<HTMLInputElement | null>(null);

  useLayoutEffect(() => {
    const textArea = textAreaRef.current;
    if (!textArea) return;
    textArea.style.height = "auto";
    textArea.style.height = `${Math.min(textArea.scrollHeight, 180)}px`;
  }, [value]);

  const submit = () => {
    const content = value.trim();
    if (!content || disabled) return;
    onSend(content);
    setValue("");
  };

  const handleSubmit = (event: FormEvent) => {
    event.preventDefault();
    submit();
  };

  const handleKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      submit();
    }
  };

  const handleFileChange = (event: ChangeEvent<HTMLInputElement>) => {
    onUploadFiles(Array.from(event.target.files ?? []));
    event.target.value = "";
  };

  return (
    <form className="chat-composer" onSubmit={handleSubmit}>
      <div className="composer-hints">
        <span>
          <AtSign size={14} />
          @WebSearch
        </span>
        <span>{selectedFileCount ? `限定 ${selectedFileCount} 个文件` : "检索全部已索引文件"}</span>
        <span>文件页可拖拽分类并选择聊天范围</span>
      </div>
      <div className="composer-box">
        <input
          ref={fileInputRef}
          className="visually-hidden"
          type="file"
          multiple
          onChange={handleFileChange}
        />
        <IconButton label="添加附件" onClick={() => fileInputRef.current?.click()}>
          <Paperclip size={18} />
        </IconButton>
        <textarea
          ref={textAreaRef}
          value={value}
          onChange={(event) => setValue(event.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="询问企业知识库，使用 @filename 锁定文档，Shift+Enter 换行"
          rows={1}
          disabled={disabled}
        />
        <IconButton label="优化 Prompt">
          <Sparkles size={18} />
        </IconButton>
        <button className="send-button" type="submit" disabled={disabled || !value.trim()}>
          <SendHorizontal size={18} />
          发送
        </button>
      </div>
    </form>
  );
}
