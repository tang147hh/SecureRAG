import { Activity, HelpCircle, Menu, Moon, PanelRightOpen, ShieldCheck } from "lucide-react";
import { IconButton } from "./IconButton";

interface HeaderNavProps {
  activeView: "chat" | "files" | "eval" | "settings";
  onSelectView: (view: "chat" | "files" | "eval" | "settings") => void;
  onOpenSidebar: () => void;
  onOpenReferences: () => void;
}

const navItems = ["聊天", "文件", "评测", "资源", "设置", "帮助"];

export function HeaderNav({
  activeView,
  onSelectView,
  onOpenSidebar,
  onOpenReferences,
}: HeaderNavProps) {
  return (
    <header className="header-nav">
      <div className="header-nav__left">
        <IconButton className="mobile-only" label="打开侧边栏" onClick={onOpenSidebar}>
          <Menu size={18} />
        </IconButton>
        <div className="brand-mark">
          <ShieldCheck size={22} />
        </div>
        <div className="brand-copy">
          <strong>SecureRAG</strong>
          <span>Secure RAG Console</span>
        </div>
      </div>

      <nav className="top-menu" aria-label="Primary navigation">
        {navItems.map((item) => (
          <button
            key={item}
            className={
              (item === "聊天" && activeView === "chat") ||
              (item === "文件" && activeView === "files") ||
              (item === "评测" && activeView === "eval") ||
              (item === "设置" && activeView === "settings")
                ? "is-active"
                : ""
            }
            type="button"
            onClick={() => {
              if (item === "聊天") onSelectView("chat");
              if (item === "文件") onSelectView("files");
              if (item === "评测") onSelectView("eval");
              if (item === "设置") onSelectView("settings");
            }}
          >
            {item === "帮助" ? <HelpCircle size={15} /> : null}
            {item}
          </button>
        ))}
      </nav>

      <div className="header-nav__right">
        <span className="system-status">
          <Activity size={15} />
          在线
        </span>
        <IconButton label="切换主题">
          <Moon size={17} />
        </IconButton>
        <span className="version-badge">v0.1.0</span>
        <IconButton className="tablet-only" label="打开引用面板" onClick={onOpenReferences}>
          <PanelRightOpen size={18} />
        </IconButton>
      </div>
    </header>
  );
}
