import { X } from "lucide-react";
import type { ChatSettings } from "../api/types";
import { ChatSettingsPanel } from "./ChatSettingsPanel";
import { IconButton } from "./IconButton";

interface SidebarProps {
  settings: ChatSettings;
  isOpen: boolean;
  onClose: () => void;
  onSettingsChange: (settings: ChatSettings) => void;
  onUploadFiles: (files: File[]) => void;
  isUploading: boolean;
}

export function Sidebar({
  settings,
  isOpen,
  onClose,
  onSettingsChange,
  onUploadFiles,
  isUploading,
}: SidebarProps) {
  return (
    <aside
      className={`sidebar app-drawer app-drawer--left ${isOpen ? "is-open" : ""}`}
      style={
        isOpen
          ? {
              left: "auto",
              right: "calc(100vw - min(22rem, calc(100vw - 1.2rem)) - 0.6rem)",
            }
          : undefined
      }
    >
      <div className="drawer-heading">
        <div>
          <strong>RAG 设置</strong>
          <small>检索、模型与文件索引</small>
        </div>
        <IconButton className="mobile-only" label="关闭侧边栏" onClick={onClose}>
          <X size={17} />
        </IconButton>
      </div>
      <ChatSettingsPanel
        settings={settings}
        onChange={onSettingsChange}
        onUploadFiles={onUploadFiles}
        isUploading={isUploading}
      />
    </aside>
  );
}
