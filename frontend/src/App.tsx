import { useEffect, useMemo, useRef, useState } from "react";
import { apiClient } from "./api/client";
import type {
  ChatMessage,
  ChatSettings,
  Conversation,
  FileDetail,
  FileDirectory,
  FileItem,
  ReferenceDocument,
  SourcePermissionItem,
  UploadIndexingOptions,
} from "./api/types";
import { AppShell } from "./components/AppShell";
import { ChatWorkspace } from "./components/ChatWorkspace";
import { EvaluationWorkspace } from "./components/EvaluationWorkspace";
import { FileWorkspace } from "./components/FileWorkspace";
import { ModelSettingsWorkspace } from "./components/ModelSettingsWorkspace";
import { ReferencePanel } from "./components/ReferencePanel";
import { Sidebar } from "./components/Sidebar";

const createUserMessage = (conversationId: string, content: string): ChatMessage => ({
  id: `local-${crypto.randomUUID()}`,
  conversationId,
  role: "user",
  content,
  createdAt: new Date().toISOString(),
  status: "sent",
});

const createAssistantPlaceholder = (conversationId: string): ChatMessage => ({
  id: `stream-${crypto.randomUUID()}`,
  conversationId,
  role: "assistant",
  content: "",
  createdAt: new Date().toISOString(),
  status: "loading",
});

const lastAssistantMessage = (messages: ChatMessage[]) =>
  [...messages].reverse().find((message) => message.role === "assistant");

function App() {
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [activeConversationId, setActiveConversationId] = useState<string>();
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [references, setReferences] = useState<ReferenceDocument[]>([]);
  const [activeDocumentId, setActiveDocumentId] = useState<string>();
  const [settings, setSettings] = useState<ChatSettings>();
  const [settingsDraft, setSettingsDraft] = useState<ChatSettings>();
  const [activeView, setActiveView] = useState<
    "chat" | "files" | "eval" | "settings"
  >("chat");
  const [fileDirectories, setFileDirectories] = useState<FileDirectory[]>([]);
  const [fileItems, setFileItems] = useState<FileItem[]>([]);
  const [activeFileDetail, setActiveFileDetail] = useState<FileDetail>();
  const [activeFileDetailError, setActiveFileDetailError] = useState<string>();
  const [selectedFileIds, setSelectedFileIds] = useState<string[]>([]);
  const [isSending, setIsSending] = useState(false);
  const [isUploading, setIsUploading] = useState(false);
  const [apiMode, setApiMode] = useState<"connecting" | "backend" | "error">("connecting");
  const [bootError, setBootError] = useState<string>();
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [referenceOpen, setReferenceOpen] = useState(false);
  const settingsSaveTimer = useRef<number | undefined>(undefined);

  const activeConversation = useMemo(
    () => conversations.find((conversation) => conversation.id === activeConversationId),
    [activeConversationId, conversations],
  );

  useEffect(() => {
    let mounted = true;
    const boot = async () => {
      const [conversationList, chatSettings, defaultReferences] = await Promise.all([
        apiClient.listConversations(),
        apiClient.getChatSettings(),
        apiClient.listDefaultReferences(),
        apiClient.health(),
      ]);
      const nextConversations =
        conversationList.length > 0 ? conversationList : [await apiClient.createConversation()];

      if (!mounted) return;
      setConversations(nextConversations);
      setActiveConversationId(nextConversations[0]?.id);
      setSettings(chatSettings);
      setSettingsDraft(chatSettings);
      setReferences(defaultReferences);
      setActiveDocumentId(defaultReferences[0]?.id);
      apiClient
        .listFileWorkspace()
        .then((workspace) => {
          if (!mounted) return;
          setFileDirectories(workspace.directories);
          setFileItems(workspace.files);
        })
        .catch(() => undefined);
      setApiMode("backend");
    };

    boot().catch((error: unknown) => {
      if (!mounted) return;
      setBootError(error instanceof Error ? error.message : "后端接口不可用。");
      setApiMode("error");
    });
    return () => {
      mounted = false;
    };
  }, []);

  useEffect(() => {
    if (!activeConversationId) return;
    let mounted = true;
    apiClient
      .listMessages(activeConversationId)
      .then(async (nextMessages) => {
        if (!mounted) return;
        setMessages(nextMessages);
        const assistantMessage = lastAssistantMessage(nextMessages);
        if (!assistantMessage) {
          const defaultReferences = await apiClient.listDefaultReferences();
          if (!mounted) return;
          setReferences(defaultReferences);
          setActiveDocumentId(defaultReferences[0]?.id);
          return;
        }
        const messageReferences = await apiClient.getMessageReferences(assistantMessage.id);
        if (!mounted) return;
        if (messageReferences.length) {
          setReferences(messageReferences);
          setActiveDocumentId(messageReferences[0]?.id);
        } else {
          const defaultReferences = await apiClient.listDefaultReferences();
          if (!mounted) return;
          setReferences(defaultReferences);
          setActiveDocumentId(defaultReferences[0]?.id);
        }
      })
      .catch((error: unknown) => {
        if (!mounted) return;
        setMessages([
          {
            id: `error-${crypto.randomUUID()}`,
            conversationId: activeConversationId,
            role: "system",
            content: error instanceof Error ? error.message : "加载会话消息失败。",
            createdAt: new Date().toISOString(),
            status: "error",
          },
        ]);
      });
    return () => {
      mounted = false;
    };
  }, [activeConversationId]);

  const handleOpenMessageReferences = async (messageId: string) => {
    try {
      const messageReferences = await apiClient.getMessageReferences(messageId);
      if (!messageReferences.length) {
        window.alert("该回答没有可恢复的知识引用。");
        return;
      }
      setReferences(messageReferences);
      setActiveDocumentId(messageReferences[0]?.id);
      setReferenceOpen(true);
    } catch (error) {
      window.alert(error instanceof Error ? error.message : "知识引用加载失败。");
    }
  };

  const refreshConversations = async () => {
    const nextConversations = await apiClient.listConversations();
    setConversations(nextConversations);
    return nextConversations;
  };

  const handleCreateConversation = async () => {
    const conversation = await apiClient.createConversation();
    const nextConversations = await refreshConversations();
    setConversations((current) =>
      current.some((item) => item.id === conversation.id)
        ? current
        : [conversation, ...nextConversations],
    );
    setActiveConversationId(conversation.id);
    setMessages([]);
  };

  const handleDeleteConversation = async (conversationId: string) => {
    await apiClient.deleteConversation(conversationId);
    const nextConversations = await apiClient.listConversations();
    const ensuredConversations =
      nextConversations.length > 0 ? nextConversations : [await apiClient.createConversation()];
    setConversations(ensuredConversations);
    if (conversationId === activeConversationId) {
      setActiveConversationId(ensuredConversations[0]?.id);
      setMessages([]);
    }
  };

  const handleRenameConversation = async (conversationId: string) => {
    const current = conversations.find((conversation) => conversation.id === conversationId);
    const baseTitle = current?.title.replace(/ · 已编辑$/, "") ?? "企业问答会话";
    const nextTitle = window.prompt("输入新的会话名称", baseTitle);
    if (!nextTitle) return;
    await apiClient.renameConversation(conversationId, nextTitle);
    await refreshConversations();
  };

  const handleSettingsChange = async (nextSettings: ChatSettings) => {
    setSettings(nextSettings);
    window.clearTimeout(settingsSaveTimer.current);
    settingsSaveTimer.current = window.setTimeout(() => {
      void apiClient.saveChatSettings({
        ...nextSettings,
        modelConfig: { name: "", baseUrl: "", model: "", apiKey: "", isDefault: false },
        embeddingConfig: {
          name: "",
          baseUrl: "",
          model: "",
          apiKey: "",
          isDefault: false,
        },
      });
    }, 240);
  };

  const handleSettingsDraftChange = (nextSettings: ChatSettings) => {
    setSettingsDraft(nextSettings);
  };

  const handleSaveSettings = async () => {
    if (!settingsDraft) return;
    try {
      const saved = await apiClient.saveChatSettings(settingsDraft);
      setSettings(saved);
      setSettingsDraft(saved);
      window.alert(saved.settingError || "设置已保存。");
    } catch (error) {
      window.alert(error instanceof Error ? error.message : "设置保存失败。");
    }
  };

  const handleUploadFiles = async (
    files: File[],
    directoryId?: string | null,
    options?: UploadIndexingOptions,
  ) => {
    if (!files.length) return;
    setIsUploading(true);
    try {
      const uploadedReferences = await apiClient.uploadFiles(files, directoryId, {
        ...options,
        embeddingModel: settings?.embeddingModel ?? "",
      });
      setReferences((current) => [...uploadedReferences, ...current]);
      setActiveDocumentId(uploadedReferences[0]?.id);
      const workspace = await apiClient.listFileWorkspace();
      setFileDirectories(workspace.directories);
      setFileItems(workspace.files);
      setReferenceOpen(true);
    } catch (error) {
      window.alert(error instanceof Error ? error.message : "文件上传失败。");
    } finally {
      setIsUploading(false);
    }
  };

  const handleReembedFile = async (fileId: string, options?: UploadIndexingOptions) => {
    setIsUploading(true);
    try {
      const detail = await apiClient.reembedFile(fileId, {
        ...options,
        embeddingModel: settings?.embeddingModel ?? "",
      });
      setActiveFileDetail(detail);
      const workspace = await apiClient.listFileWorkspace();
      setFileDirectories(workspace.directories);
      setFileItems(workspace.files);
      const defaultReferences = await apiClient.listDefaultReferences();
      setReferences(defaultReferences);
    } catch (error) {
      window.alert(error instanceof Error ? error.message : "重新 embedding 失败。");
    } finally {
      setIsUploading(false);
    }
  };

  const handleDeleteFile = async (fileId: string) => {
    try {
      await apiClient.deleteFile(fileId);
      const workspace = await apiClient.listFileWorkspace();
      setFileDirectories(workspace.directories);
      setFileItems(workspace.files);
      setSelectedFileIds((current) => current.filter((id) => id !== fileId));
      setActiveFileDetail((current) => (current?.file.id === fileId ? undefined : current));
      const defaultReferences = await apiClient.listDefaultReferences();
      setReferences(defaultReferences);
      setActiveDocumentId((current) =>
        current === fileId ? defaultReferences[0]?.id : current,
      );
    } catch (error) {
      window.alert(error instanceof Error ? error.message : "文件删除失败。");
    }
  };

  const handleCreateDirectory = async (name: string) => {
    try {
      await apiClient.createDirectory(name);
      const workspace = await apiClient.listFileWorkspace();
      setFileDirectories(workspace.directories);
      setFileItems(workspace.files);
    } catch (error) {
      window.alert(error instanceof Error ? error.message : "目录创建失败。");
    }
  };

  const handleDeleteDirectory = async (directoryId: string) => {
    if (!window.confirm("删除目录不会删除文件，确认继续？")) return;
    try {
      await apiClient.deleteDirectory(directoryId);
      const workspace = await apiClient.listFileWorkspace();
      setFileDirectories(workspace.directories);
      setFileItems(workspace.files);
    } catch (error) {
      window.alert(error instanceof Error ? error.message : "目录删除失败。");
    }
  };

  const handleMoveFiles = async (fileIds: string[], directoryId?: string | null) => {
    if (!fileIds.length) return;
    try {
      const workspace = await apiClient.moveFiles(fileIds, directoryId);
      setFileDirectories(workspace.directories);
      setFileItems(workspace.files);
    } catch (error) {
      window.alert(error instanceof Error ? error.message : "移动文件失败。");
    }
  };

  const handleSelectFileDetail = async (fileId: string, typeFilter = "all") => {
    setActiveFileDetailError(undefined);
    try {
      const detail = await apiClient.getFileDetail(fileId, typeFilter);
      setActiveFileDetail(detail);
    } catch (error) {
      setActiveFileDetail(undefined);
      setActiveFileDetailError(error instanceof Error ? error.message : "文件详情加载失败。");
    }
  };

  const handleUpdateFilePermissions = async (
    fileId: string,
    permissions: SourcePermissionItem[],
  ) => {
    try {
      const detail = await apiClient.updateFilePermissions(fileId, permissions);
      setActiveFileDetail(detail);
      const workspace = await apiClient.listFileWorkspace();
      setFileDirectories(workspace.directories);
      setFileItems(workspace.files);
      const defaultReferences = await apiClient.listDefaultReferences();
      setReferences(defaultReferences);
    } catch (error) {
      window.alert(error instanceof Error ? error.message : "权限更新失败。");
    }
  };

  const handleToggleFileSelection = (fileId: string) => {
    setSelectedFileIds((current) =>
      current.includes(fileId)
        ? current.filter((currentId) => currentId !== fileId)
        : [...current, fileId],
    );
  };

  const handleSend = async (content: string) => {
    if (!activeConversationId || !settings || isSending) return;
    setIsSending(true);
    const userMessage = createUserMessage(activeConversationId, content);
    const assistantPlaceholder = createAssistantPlaceholder(activeConversationId);
    setMessages((current) => [...current, userMessage, assistantPlaceholder]);

    try {
      let streamedContent = "";
      const stream = apiClient.streamMessage({
        conversationId: activeConversationId,
        content,
        settings,
        selectedFileIds,
      });

      // Manual iteration keeps the async generator return value available.
      // That gives the UI both token updates and the final citation payload.
      while (true) {
        const next = await stream.next();
        if (next.done) {
          setMessages((current) =>
            current.map((message) =>
              message.id === assistantPlaceholder.id
                ? { ...next.value.message, content: streamedContent || next.value.message.content }
                : message,
            ),
          );
          setReferences(next.value.references);
          setActiveDocumentId(next.value.references[0]?.id);
          break;
        }

        streamedContent += next.value;
        setMessages((current) =>
          current.map((message) =>
            message.id === assistantPlaceholder.id
              ? { ...message, content: streamedContent, status: "streaming" }
              : message,
          ),
        );
      }
      await refreshConversations();
    } catch (error) {
      setMessages((current) =>
        current.map((message) =>
          message.id === assistantPlaceholder.id
            ? {
                ...message,
                content: error instanceof Error ? error.message : "消息发送失败。",
                status: "error",
              }
            : message,
        ),
      );
    } finally {
      setIsSending(false);
    }
  };

  if (!settings) {
    return (
      <div className="boot-screen">
        {bootError ? `后端接口连接失败：${bootError}` : "Loading SecureRAG workspace..."}
      </div>
    );
  }

  return (
    <AppShell
      activeView={activeView}
      sidebarOpen={sidebarOpen}
      referenceOpen={referenceOpen}
      onSelectView={setActiveView}
      onOpenSidebar={() => setSidebarOpen(true)}
      onOpenReferences={() => setReferenceOpen(true)}
      onCloseDrawers={() => {
        setSidebarOpen(false);
        setReferenceOpen(false);
      }}
      sidebar={
        <Sidebar
          settings={settings}
          isOpen={sidebarOpen}
          onClose={() => setSidebarOpen(false)}
          onSettingsChange={handleSettingsChange}
          onUploadFiles={handleUploadFiles}
          isUploading={isUploading}
        />
      }
      workspace={
        activeView === "chat" ? (
          <ChatWorkspace
            conversation={activeConversation}
            conversations={conversations}
            messages={messages}
            isSending={isSending}
            selectedFileCount={selectedFileIds.length}
            onSend={handleSend}
            onUploadFiles={handleUploadFiles}
            onOpenReferences={() => setReferenceOpen(true)}
            onOpenMessageReferences={handleOpenMessageReferences}
            onSelectConversation={setActiveConversationId}
            onCreateConversation={handleCreateConversation}
            onDeleteConversation={handleDeleteConversation}
            onRenameConversation={handleRenameConversation}
            onOpenFiles={() => setActiveView("files")}
            apiMode={apiMode}
          />
        ) : activeView === "files" ? (
          <FileWorkspace
            directories={fileDirectories}
            files={fileItems}
            activeFileDetail={activeFileDetail}
            activeFileDetailError={activeFileDetailError}
            selectedFileIds={selectedFileIds}
            isUploading={isUploading}
            onUploadFiles={handleUploadFiles}
            onReembedFile={handleReembedFile}
            onCreateDirectory={handleCreateDirectory}
            onDeleteFile={handleDeleteFile}
            onDeleteDirectory={handleDeleteDirectory}
            onMoveFiles={handleMoveFiles}
            onSelectFileDetail={handleSelectFileDetail}
            onUpdateFilePermissions={handleUpdateFilePermissions}
            onToggleFileSelection={handleToggleFileSelection}
            onSelectFilesForChat={setSelectedFileIds}
            onOpenChat={() => setActiveView("chat")}
          />
        ) : activeView === "eval" ? (
          <EvaluationWorkspace files={fileItems} />
        ) : (
          <ModelSettingsWorkspace
            settings={settingsDraft ?? settings}
            onChange={handleSettingsDraftChange}
            onSave={handleSaveSettings}
          />
        )
      }
      referencePanel={
        <ReferencePanel
          references={references}
          activeDocumentId={activeDocumentId}
          isOpen={referenceOpen}
          onClose={() => setReferenceOpen(false)}
          onSelectDocument={setActiveDocumentId}
        />
      }
    />
  );
}

export default App;
