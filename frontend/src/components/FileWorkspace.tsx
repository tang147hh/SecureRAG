import { ChangeEvent, DragEvent, useEffect, useMemo, useRef, useState } from "react";
import {
  CheckSquare,
  FileText,
  Folder,
  FolderPlus,
  Inbox,
  Loader2,
  MessageSquareText,
  PieChart,
  RefreshCw,
  Search,
  Shield,
  Square,
  Trash2,
  UploadCloud,
  Users,
} from "lucide-react";
import type {
  FileDetail,
  FileDirectory,
  FileItem,
  SourcePermissionItem,
  UploadIndexingOptions,
} from "../api/types";
import { IconButton } from "./IconButton";

interface FileWorkspaceProps {
  directories: FileDirectory[];
  files: FileItem[];
  activeFileDetail?: FileDetail;
  activeFileDetailError?: string;
  selectedFileIds: string[];
  isUploading: boolean;
  onUploadFiles: (
    files: File[],
    directoryId?: string | null,
    options?: UploadIndexingOptions,
  ) => void;
  onReembedFile: (fileId: string, options?: UploadIndexingOptions) => void;
  onCreateDirectory: (name: string) => void;
  onDeleteFile: (fileId: string) => void;
  onDeleteDirectory: (directoryId: string) => void;
  onMoveFiles: (fileIds: string[], directoryId?: string | null) => void;
  onSelectFileDetail: (fileId: string, typeFilter?: string) => void;
  onUpdateFilePermissions: (fileId: string, permissions: SourcePermissionItem[]) => void;
  onToggleFileSelection: (fileId: string) => void;
  onSelectFilesForChat: (fileIds: string[]) => void;
  onOpenChat: () => void;
}

const ROOT_DIRECTORY_ID = "__root__";

const formatSize = (size: number) => {
  if (!size) return "未知大小";
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
  return `${(size / 1024 / 1024).toFixed(1)} MB`;
};

export function FileWorkspace({
  directories,
  files,
  activeFileDetail,
  activeFileDetailError,
  selectedFileIds,
  isUploading,
  onUploadFiles,
  onReembedFile,
  onCreateDirectory,
  onDeleteFile,
  onDeleteDirectory,
  onMoveFiles,
  onSelectFileDetail,
  onUpdateFilePermissions,
  onToggleFileSelection,
  onSelectFilesForChat,
  onOpenChat,
}: FileWorkspaceProps) {
  const [activeDirectoryId, setActiveDirectoryId] = useState<string>(ROOT_DIRECTORY_ID);
  const [activeChunkType, setActiveChunkType] = useState("all");
  const [query, setQuery] = useState("");
  const [chunkSize, setChunkSize] = useState(1024);
  const [chunkOverlap, setChunkOverlap] = useState(256);
  const [dragOverDirectoryId, setDragOverDirectoryId] = useState<string>();
  const fileInputRef = useRef<HTMLInputElement | null>(null);

  const filesByDirectory = useMemo(() => {
    const grouped = new Map<string, FileItem[]>();
    grouped.set(ROOT_DIRECTORY_ID, []);
    directories.forEach((directory) => grouped.set(directory.id, []));
    files.forEach((file) => {
      grouped.get(file.directoryId || ROOT_DIRECTORY_ID)?.push(file);
    });
    return grouped;
  }, [directories, files]);

  const activeFiles = useMemo(() => {
    const base =
      activeDirectoryId === "all"
        ? files
        : filesByDirectory.get(activeDirectoryId) ?? [];
    const normalized = query.trim().toLowerCase();
    if (!normalized) return base;
    return base.filter((file) =>
      [file.name, file.summary, file.source].some((value) =>
        value.toLowerCase().includes(normalized),
      ),
    );
  }, [activeDirectoryId, files, filesByDirectory, query]);

  const selectedSet = useMemo(() => new Set(selectedFileIds), [selectedFileIds]);
  const selectedVisibleCount = activeFiles.filter((file) => selectedSet.has(file.id)).length;
  const uploadOptions = useMemo<UploadIndexingOptions>(
    () => ({ chunkSize, chunkOverlap }),
    [chunkOverlap, chunkSize],
  );

  useEffect(() => {
    if (chunkOverlap >= chunkSize) {
      setChunkOverlap(Math.max(0, chunkSize - 1));
    }
  }, [chunkOverlap, chunkSize]);

  const handleFileChange = (event: ChangeEvent<HTMLInputElement>) => {
    onUploadFiles(Array.from(event.target.files ?? []), null, uploadOptions);
    event.target.value = "";
  };

  const handleCreateDirectory = () => {
    const name = window.prompt("输入新目录名称");
    if (!name?.trim()) return;
    onCreateDirectory(name.trim());
  };

  const dragFileIds = (event: DragEvent) => {
    const raw = event.dataTransfer.getData("application/securerag-file-ids");
    if (!raw) return [];
    try {
      const parsed = JSON.parse(raw);
      return Array.isArray(parsed) ? parsed.map(String) : [];
    } catch {
      return [];
    }
  };

  const handleDragStart = (event: DragEvent<HTMLElement>, fileId: string) => {
    const fileIds = selectedSet.has(fileId) ? selectedFileIds : [fileId];
    event.dataTransfer.effectAllowed = "move";
    event.dataTransfer.setData("application/securerag-file-ids", JSON.stringify(fileIds));
  };

  const handleDropOnDirectory = (event: DragEvent, directoryId: string) => {
    event.preventDefault();
    const targetDirectoryId = directoryId === ROOT_DIRECTORY_ID ? null : directoryId;
    const droppedFiles = Array.from(event.dataTransfer.files ?? []);
    if (droppedFiles.length) {
      onUploadFiles(droppedFiles, targetDirectoryId, uploadOptions);
      setActiveDirectoryId(directoryId);
      setDragOverDirectoryId(undefined);
      return;
    }

    const fileIds = dragFileIds(event);
    if (fileIds.length) {
      onMoveFiles(fileIds, targetDirectoryId);
    }
    setDragOverDirectoryId(undefined);
  };

  const handleUploadDrop = (event: DragEvent) => {
    event.preventDefault();
    setDragOverDirectoryId(undefined);
    const droppedFiles = Array.from(event.dataTransfer.files ?? []);
    const targetDirectoryId =
      activeDirectoryId !== "all" && activeDirectoryId !== ROOT_DIRECTORY_ID
        ? activeDirectoryId
        : null;
    if (droppedFiles.length) onUploadFiles(droppedFiles, targetDirectoryId, uploadOptions);
  };

  const selectVisibleForChat = () => {
    onSelectFilesForChat(activeFiles.map((file) => file.id));
  };

  const handleOpenDetail = (fileId: string) => {
    setActiveChunkType("all");
    onSelectFileDetail(fileId, "all");
  };

  const handleChunkTypeChange = (type: string) => {
    if (!activeFileDetail?.file.id) return;
    setActiveChunkType(type);
    onSelectFileDetail(activeFileDetail.file.id, type);
  };

  const handleEditPermissions = () => {
    if (!activeFileDetail) return;
    const publicRead = activeFileDetail.permissions.some(
      (permission) =>
        permission.principalType === "public" && permission.permission !== "none",
    );
    const readableUsers = activeFileDetail.permissions
      .filter((permission) => permission.principalType === "user" && permission.permission === "read")
      .map((permission) => permission.principalId)
      .join(", ");
    const nextPublic = window.confirm(
      publicRead ? "当前文件公开可读。是否保持公开可读？" : "是否允许所有用户读取该文件？",
    );
    const nextUsers = window.prompt("允许读取的用户 ID，用逗号分隔", readableUsers);
    if (nextUsers === null) return;
    const userEntries = nextUsers
      .split(",")
      .map((value) => value.trim())
      .filter(Boolean)
      .map<SourcePermissionItem>((principalId) => ({
        principalType: "user",
        principalId,
        permission: "read",
      }));
    const entries = nextPublic
      ? [
          ...userEntries,
          { principalType: "public", principalId: "*", permission: "read" },
        ]
      : userEntries;
    onUpdateFilePermissions(activeFileDetail.file.id, entries);
  };

  const handleReembed = () => {
    if (!activeFileDetail) return;
    const message = [
      `确认要重新 embedding「${activeFileDetail.file.name}」吗？`,
      "",
      "系统会先删除该文件当前的 chunks 和向量索引，再按当前 chunk_size / chunk_overlap 重建。",
      "如果重建失败，页面会显示错误信息；请根据提示检查后端索引日志或重新上传文件。",
    ].join("\n");
    if (!window.confirm(message)) {
      return;
    }
    onReembedFile(activeFileDetail.file.id, uploadOptions);
  };

  const handleDeleteFile = () => {
    if (!activeFileDetail) return;
    const message = [
      `确认要删除「${activeFileDetail.file.name}」吗？`,
      "",
      "此操作会删除：",
      "1. 文件记录和原始文件",
      "2. 该文件的所有 chunks",
      "3. 该文件的向量索引",
      "4. 目录中的文件引用",
      "",
      "删除后无法在当前界面恢复，需要重新上传并 embedding。确认继续？",
    ].join("\n");
    if (!window.confirm(message)) return;
    onDeleteFile(activeFileDetail.file.id);
  };

  return (
    <main className="file-workspace">
      <input
        ref={fileInputRef}
        className="visually-hidden"
        type="file"
        multiple
        onChange={handleFileChange}
      />

      <div className="file-sidebar">
        <div className="drawer-heading">
          <div>
            <strong>文件目录</strong>
            <small>{files.length} 个已索引文件</small>
          </div>
          <IconButton label="新建目录" onClick={handleCreateDirectory}>
            <FolderPlus size={17} />
          </IconButton>
        </div>

        <button
          className={`directory-row ${activeDirectoryId === "all" ? "is-active" : ""}`}
          type="button"
          onClick={() => setActiveDirectoryId("all")}
        >
          <Inbox size={17} />
          <span>全部文件</span>
          <small>{files.length}</small>
        </button>
        <DirectoryDropRow
          id={ROOT_DIRECTORY_ID}
          label="未分类"
          count={filesByDirectory.get(ROOT_DIRECTORY_ID)?.length ?? 0}
          active={activeDirectoryId === ROOT_DIRECTORY_ID}
          dragOver={dragOverDirectoryId === ROOT_DIRECTORY_ID}
          onClick={() => setActiveDirectoryId(ROOT_DIRECTORY_ID)}
          onDragOver={(event) => {
            event.preventDefault();
            setDragOverDirectoryId(ROOT_DIRECTORY_ID);
          }}
          onDragLeave={() => setDragOverDirectoryId(undefined)}
          onDrop={(event) => handleDropOnDirectory(event, ROOT_DIRECTORY_ID)}
        />

        <div className="directory-list">
          {directories.map((directory) => (
            <div className="directory-row-wrap" key={directory.id}>
              <DirectoryDropRow
                id={directory.id}
                label={directory.name}
                count={filesByDirectory.get(directory.id)?.length ?? 0}
                active={activeDirectoryId === directory.id}
                dragOver={dragOverDirectoryId === directory.id}
                onClick={() => setActiveDirectoryId(directory.id)}
                onDragOver={(event) => {
                  event.preventDefault();
                  setDragOverDirectoryId(directory.id);
                }}
                onDragLeave={() => setDragOverDirectoryId(undefined)}
                onDrop={(event) => handleDropOnDirectory(event, directory.id)}
              />
              <IconButton
                label="删除目录"
                variant="danger"
                onClick={() => onDeleteDirectory(directory.id)}
              >
                <Trash2 size={15} />
              </IconButton>
            </div>
          ))}
        </div>
      </div>

      <section className="file-main">
        <div className="file-toolbar">
          <div>
            <strong>文件</strong>
            <small>
              {selectedFileIds.length ? `已选择 ${selectedFileIds.length} 个文件用于聊天` : "默认检索全部文件"}
            </small>
          </div>
          <div className="file-toolbar__actions">
            <button className="secondary-button" type="button" onClick={selectVisibleForChat}>
              <CheckSquare size={16} />
              当前视图用于聊天
            </button>
            <button className="secondary-button" type="button" onClick={onOpenChat}>
              <MessageSquareText size={16} />
              去聊天
            </button>
            <button
              className="primary-button"
              type="button"
              disabled={isUploading}
              onClick={() => fileInputRef.current?.click()}
            >
              {isUploading ? <Loader2 className="spin" size={16} /> : <UploadCloud size={16} />}
              上传并 Embedding
            </button>
          </div>
        </div>

        <div className="embedding-options">
          <label>
            <span>chunk_size</span>
            <input
              type="number"
              min={128}
              max={50000}
              step={64}
              value={chunkSize}
              onChange={(event) => setChunkSize(Number(event.target.value) || 1024)}
            />
          </label>
          <label>
            <span>chunk_overlap</span>
            <input
              type="number"
              min={0}
              max={Math.max(0, chunkSize - 1)}
              step={16}
              value={chunkOverlap}
              onChange={(event) =>
                setChunkOverlap(Math.min(Number(event.target.value) || 0, chunkSize - 1))
              }
            />
          </label>
        </div>

        <div
          className={`file-drop-target ${isUploading ? "is-uploading" : ""}`}
          onDragOver={(event) => event.preventDefault()}
          onDrop={handleUploadDrop}
        >
          <UploadCloud size={18} />
          <span>{isUploading ? "正在写入向量索引..." : "拖入文件即可上传、解析并 embedding"}</span>
        </div>

        <label className="file-search">
          <Search size={16} />
          <input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="搜索文件名、来源或索引摘要"
          />
        </label>

        <div className="file-list-head">
          <span>名称</span>
          <span>目录</span>
          <span>权限</span>
          <span>大小</span>
          <span>更新时间</span>
        </div>
        <div className="file-list" aria-label="文件列表">
          {activeFiles.length ? (
            activeFiles.map((file) => (
              <article
                className={`file-row ${selectedSet.has(file.id) ? "is-selected" : ""}`}
                draggable
                key={file.id}
                onDragStart={(event) => handleDragStart(event, file.id)}
                onClick={() => handleOpenDetail(file.id)}
              >
                <button
                  className="file-select-button"
                  type="button"
                  onClick={(event) => {
                    event.stopPropagation();
                    onToggleFileSelection(file.id);
                  }}
                  aria-label={selectedSet.has(file.id) ? "取消选择文件" : "选择文件"}
                >
                  {selectedSet.has(file.id) ? <CheckSquare size={18} /> : <Square size={18} />}
                </button>
                <div className="file-name-cell">
                  <FileText size={18} />
                  <span>
                    <strong>{file.name}</strong>
                    <small>{file.summary}</small>
                  </span>
                </div>
                <span>{directoryName(file.directoryId, directories)}</span>
                <span>
                  <PermissionBadge permission={file.permission} />
                </span>
                <span>{formatSize(file.size)}</span>
                <span>{new Date(file.updatedAt).toLocaleString()}</span>
              </article>
            ))
          ) : (
            <div className="file-empty">
              <FileText size={28} />
              <strong>暂无文件</strong>
              <span>上传文件后会自动进入 embedding，可拖动到目录中分类。</span>
            </div>
          )}
        </div>

        {selectedVisibleCount ? (
          <div className="selection-bar">
            <span>当前视图选中 {selectedVisibleCount} 个</span>
            <button type="button" onClick={() => onMoveFiles(selectedFileIds, null)}>
              移至未分类
            </button>
            <button type="button" onClick={() => onSelectFilesForChat([])}>
              清空聊天选择
            </button>
          </div>
        ) : null}
      </section>

      <aside className="file-detail-panel">
        {activeFileDetail ? (
          <>
            <div className="drawer-heading">
              <div>
                <strong>{activeFileDetail.file.name}</strong>
                <small>{activeFileDetail.chunkCount} chunks</small>
              </div>
              <div className="file-detail-actions">
                <PermissionBadge permission={activeFileDetail.file.permission} />
                {activeFileDetail.file.permission === "owner" ? (
                  <>
                    <IconButton
                      label="重新 embedding"
                      disabled={isUploading}
                      onClick={handleReembed}
                    >
                      {isUploading ? <Loader2 className="spin" size={16} /> : <RefreshCw size={16} />}
                    </IconButton>
                    <IconButton label="编辑权限" onClick={handleEditPermissions}>
                      <Users size={16} />
                    </IconButton>
                    <IconButton
                      label="删除文件"
                      variant="danger"
                      disabled={isUploading}
                      onClick={handleDeleteFile}
                    >
                      <Trash2 size={16} />
                    </IconButton>
                  </>
                ) : null}
                <PieChart size={18} />
              </div>
            </div>

            <section className="file-detail-section">
              <div className="file-detail-grid">
                <span>
                  <small>来源</small>
                  <strong>{activeFileDetail.file.source}</strong>
                </span>
                <span>
                  <small>大小</small>
                  <strong>{formatSize(activeFileDetail.file.size)}</strong>
                </span>
                <span>
                  <small>目录</small>
                  <strong>{directoryName(activeFileDetail.file.directoryId, directories)}</strong>
                </span>
                <span>
                  <small>更新时间</small>
                  <strong>{new Date(activeFileDetail.file.updatedAt).toLocaleString()}</strong>
                </span>
                <span>
                  <small>权限</small>
                  <strong>{permissionText(activeFileDetail.file.permission)}</strong>
                </span>
                <span>
                  <small>ACL</small>
                  <strong>{permissionSummary(activeFileDetail.permissions)}</strong>
                </span>
              </div>
              <p className="file-detail-summary">{activeFileDetail.file.summary}</p>
            </section>

            <section className="file-detail-section file-detail-section--grow">
              <div className="section-heading">
                <div>
                  <span>Chunk 明细</span>
                  <small>{chunkTypeSummary(activeFileDetail.chunkTypeCounts)}</small>
                </div>
                <select
                  className="chunk-filter"
                  value={activeChunkType}
                  onChange={(event) => handleChunkTypeChange(event.target.value)}
                >
                  <option value="all">全部</option>
                  {Object.keys(activeFileDetail.chunkTypeCounts).map((type) => (
                    <option key={type} value={type}>
                      {type}
                    </option>
                  ))}
                </select>
              </div>
              <div className="chunk-list">
                {activeFileDetail.chunks.map((chunk) => (
                  <article className="chunk-card" key={`${chunk.id}-${chunk.index}`}>
                    <header>
                      <strong>#{chunk.index}</strong>
                      <span>{chunk.type}</span>
                      {chunk.pageLabel ? <small>Page {chunk.pageLabel}</small> : null}
                    </header>
                    <p>{chunk.text || "该 chunk 没有可展示文本。"}</p>
                  </article>
                ))}
              </div>
            </section>
          </>
        ) : (
          <div className="file-detail-empty">
            <FileText size={28} />
            <strong>选择文件查看详情</strong>
            <span>{activeFileDetailError ?? "点击左侧文件即可查看 chunk、页码和索引信息。"}</span>
          </div>
        )}
      </aside>
    </main>
  );
}

function directoryName(directoryId: string | null | undefined, directories: FileDirectory[]) {
  if (!directoryId) return "未分类";
  return directories.find((directory) => directory.id === directoryId)?.name ?? "未分类";
}

function chunkTypeSummary(counts: Record<string, number>) {
  const parts = Object.entries(counts).map(([type, count]) => `${count} ${type}`);
  return parts.length ? parts.join(" / ") : "0 chunks";
}

function permissionText(permission: string) {
  if (permission === "owner") return "所有者";
  if (permission === "public") return "公开可读";
  return "可读";
}

function permissionSummary(permissions: SourcePermissionItem[]) {
  if (!permissions.length) return "默认权限";
  return permissions
    .map((permission) =>
      permission.principalType === "public"
        ? "公开可读"
        : `${permission.principalId}: ${permission.permission === "owner" ? "所有者" : "可读"}`,
    )
    .join(" / ");
}

function PermissionBadge({ permission }: { permission: string }) {
  return (
    <span className={`permission-badge permission-badge--${permission}`}>
      <Shield size={13} />
      {permissionText(permission)}
    </span>
  );
}

interface DirectoryDropRowProps {
  id: string;
  label: string;
  count: number;
  active: boolean;
  dragOver: boolean;
  onClick: () => void;
  onDragOver: (event: DragEvent<HTMLButtonElement>) => void;
  onDragLeave: () => void;
  onDrop: (event: DragEvent<HTMLButtonElement>) => void;
}

function DirectoryDropRow({
  id,
  label,
  count,
  active,
  dragOver,
  onClick,
  onDragOver,
  onDragLeave,
  onDrop,
}: DirectoryDropRowProps) {
  return (
    <button
      className={`directory-row ${active ? "is-active" : ""} ${dragOver ? "is-drag-over" : ""}`}
      data-directory-id={id}
      type="button"
      onClick={onClick}
      onDragOver={onDragOver}
      onDragLeave={onDragLeave}
      onDrop={onDrop}
    >
      <Folder size={17} />
      <span>{label}</span>
      <small>{count}</small>
    </button>
  );
}
