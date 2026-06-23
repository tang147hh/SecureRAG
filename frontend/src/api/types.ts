export type MessageRole = "user" | "assistant" | "system";

export interface Conversation {
  id: string;
  title: string;
  updatedAt: string;
  messageCount: number;
  pinned?: boolean;
}

export interface Citation {
  id: string;
  documentId: string;
  title: string;
  excerpt: string;
  page?: number;
  score: number;
  highlight: string;
}

export interface ChatMessage {
  id: string;
  conversationId: string;
  role: MessageRole;
  content: string;
  createdAt: string;
  status?: "sent" | "loading" | "streaming" | "error";
  citations?: Citation[];
}

export interface RagTraceSummary {
  traceId: string;
  conversationId: string;
  messageId?: string | null;
  turnIndex?: number | null;
  userId: string;
  question: string;
  status: string;
  createdAt: string;
  durationMs: number;
}

export interface RagTraceDetail extends RagTraceSummary {
  data: {
    trace_id?: string;
    conversation_id?: string;
    message_id?: string | null;
    user_id?: string;
    question?: string;
    selected_file_ids?: string[];
    effective_principal?: Record<string, unknown>;
    retrieval_params?: Record<string, unknown>;
    acl?: Record<string, unknown>;
    candidate_chunks_before_rerank?: RagTraceChunk[];
    candidate_chunks_after_rerank?: RagTraceChunk[];
    context_chunks?: RagTraceChunk[];
    citation_chunks?: RagTraceChunk[];
    tokens?: Record<string, unknown>;
    durations_ms?: Record<string, unknown>;
    errors?: Array<Record<string, unknown>>;
    [key: string]: unknown;
  };
}

export interface RagTraceChunk {
  chunk_id: string;
  source_id: string;
  source_name?: string;
  page_label?: string | null;
  type?: string;
  score?: number | null;
  reranking_score?: number | null;
  llm_reranking_score?: number | null;
  excerpt?: string;
  text?: string;
  metadata?: Record<string, unknown>;
}

export interface ReferenceDocument {
  id: string;
  title: string;
  source: string;
  summary: string;
  updatedAt: string;
  permission: "owner" | "read" | "public" | string;
  citations: Citation[];
}

export interface FileItem {
  id: string;
  name: string;
  source: string;
  summary: string;
  updatedAt: string;
  size: number;
  directoryId?: string | null;
  selected?: boolean;
  permission: "owner" | "read" | "public" | string;
}

export interface SourcePermissionItem {
  principalType: "user" | "public" | string;
  principalId: string;
  permission: "owner" | "read" | string;
}

export interface FileChunk {
  id: string;
  index: number;
  type: string;
  text: string;
  pageLabel?: string | null;
  metadata: Record<string, string | number | boolean | null>;
}

export interface FileDetail {
  file: FileItem;
  chunkCount: number;
  chunkTypeCounts: Record<string, number>;
  chunks: FileChunk[];
  permissions: SourcePermissionItem[];
}

export interface FileDirectory {
  id: string;
  name: string;
  updatedAt: string;
  fileIds: string[];
}

export interface FileWorkspaceState {
  directories: FileDirectory[];
  files: FileItem[];
}

export interface UploadIndexingOptions {
  chunkSize?: number | null;
  chunkOverlap?: number | null;
  reindex?: boolean;
}

export interface RetrievalSettings {
  topK: number;
  firstRoundMultiplier: number;
  retrievalMode: string;
  rerank: boolean;
  llmRerank: boolean;
  mmr: boolean;
  prioritizeTable: boolean;
}

export interface SelectOption {
  label: string;
  value: string;
}

export interface ChatSettings {
  suggestedChat: boolean;
  reasoningMethod: string;
  model: string;
  language: string;
  citationHighlight: string;
  mindmap: boolean;
  promptTemplate: string;
  promptTemplateText: string;
  promptTemplates?: Record<string, string>;
  retrieval: RetrievalSettings;
  options?: Record<string, SelectOption[]>;
}

export interface SendMessagePayload {
  conversationId: string;
  content: string;
  attachments?: File[];
  settings: ChatSettings;
  selectedFileIds?: string[];
}

export interface SendMessageResult {
  message: ChatMessage;
  references: ReferenceDocument[];
  trace?: RagTraceSummary | null;
}

export interface RagEvalDataset {
  id: string;
  name: string;
  description: string;
  tags: string[];
  exampleCount: number;
  runCount: number;
  latestRunStatus?: string | null;
  createdAt: string;
  updatedAt: string;
}

export interface RagEvalDatasetPayload {
  name: string;
  description?: string;
  tags?: string[];
}

export interface RagEvalExample {
  id: string;
  datasetId: string;
  question: string;
  expectedAnswer?: string | null;
  expectedSourceIds: string[];
  expectedKeywords: string[];
  evaluatorUserId: string;
  selectedFileIds: string[];
  tags: string[];
  createdAt: string;
  updatedAt: string;
}

export interface RagEvalExamplePayload {
  question: string;
  expectedAnswer?: string | null;
  expectedSourceIds?: string[];
  expectedKeywords?: string[];
  evaluatorUserId?: string | null;
  selectedFileIds?: string[];
  tags?: string[];
}

export interface RagEvalRun {
  id: string;
  datasetId: string;
  exampleId?: string | null;
  evaluatorUserId: string;
  status: string;
  question: string;
  answer: string;
  metrics: Record<string, unknown>;
  traceId?: string | null;
  error?: string | null;
  createdAt: string;
  updatedAt: string;
}

export interface RagEvalRunDetail extends RagEvalRun {
  references: Array<Record<string, unknown>>;
  settingsSnapshot: Record<string, unknown>;
  trace?: RagTraceDetail | null;
}

export type MessageStreamEvent =
  | { type: "token"; token: string }
  | { type: "citations"; references: ReferenceDocument[] }
  | { type: "done"; message: ChatMessage };
