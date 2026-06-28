import type { ReactNode } from "react";
import { HeaderNav } from "./HeaderNav";

interface AppShellProps {
  sidebar: ReactNode;
  workspace: ReactNode;
  referencePanel: ReactNode;
  activeView: "chat" | "files" | "eval" | "settings";
  sidebarOpen: boolean;
  referenceOpen: boolean;
  onSelectView: (view: "chat" | "files" | "eval" | "settings") => void;
  onOpenSidebar: () => void;
  onOpenReferences: () => void;
  onCloseDrawers: () => void;
}

export function AppShell({
  sidebar,
  workspace,
  referencePanel,
  activeView,
  sidebarOpen,
  referenceOpen,
  onSelectView,
  onOpenSidebar,
  onOpenReferences,
  onCloseDrawers,
}: AppShellProps) {
  const hasOverlay = sidebarOpen || referenceOpen;

  return (
    <div className="app-shell">
      <HeaderNav
        activeView={activeView}
        onSelectView={onSelectView}
        onOpenSidebar={onOpenSidebar}
        onOpenReferences={onOpenReferences}
      />
      <div className={`app-body ${activeView !== "chat" ? "app-body--full" : ""}`}>
        {sidebar}
        {workspace}
        {referencePanel}
      </div>
      <button
        className={`drawer-overlay ${hasOverlay ? "is-visible" : ""}`}
        type="button"
        aria-label="关闭抽屉"
        onClick={onCloseDrawers}
      />
    </div>
  );
}
