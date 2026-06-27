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
    original_question?: string;
    retrieval_query?: string;
    retrieval_enhancement?: {
      strategy?: string;
      original_question?: string;
      rewritten_question?: string | null;
      hyde_document?: string | null;
      fusion_queries?: string[];
      retrieval_query?: string;
    };
    query_rewrite?: {
      enabled?: boolean;
      rewritten_question?: string | null;
    };
    hyde?: {
      enabled?: boolean;
      document?: string | null;
    };
    rag_fusion?: {
      enabled?: boolean;
      queries?: string[];
      raw_response?: string | null;
    };
    graph_rag?: {
      enabled?: boolean;
      provider?: string | null;
      search_type?: string | null;
      graph_ids?: string[];
      entities?: Array<Record<string, unknown>>;
      relationships?: Array<Record<string, unknown>>;
      paths?: Array<Record<string, unknown>>;
      sources?: Array<Record<string, unknown>>;
      answer_fragments?: string[];
    };
    selected_file_ids?: string[];
    effective_principal?: Record<string, unknown>;
    retrieval_params?: Record<string, unknown>;
    acl?: Record<string, unknown>;
    rerank_enabled?: boolean;
    vector_candidate_chunks?: RagTraceChunk[];
    text_candidate_chunks?: RagTraceChunk[];
    fusion_query_candidates?: RagFusionQueryCandidates[];
    fused_candidate_chunks?: RagTraceChunk[];
    reranked_candidate_chunks?: RagTraceChunk[];
    candidate_chunks_before_rerank?: RagTraceChunk[];
    candidate_chunks_after_rerank?: RagTraceChunk[];
    context_chunks?: RagTraceChunk[];
    citation_chunks?: RagTraceChunk[];
    answer_verification?: RagAnswerVerification;
    tokens?: Record<string, unknown>;
    durations_ms?: Record<string, unknown>;
    errors?: Array<Record<string, unknown>>;
    [key: string]: unknown;
  };
}

export interface RagAnswerVerification {
  sentence_count?: number;
  supported_count?: number;
  unsupported_count?: number;
  insufficient_count?: number;
  evidence_coverage?: number;
  checks?: RagAnswerSentenceCheck[];
  gate?: {
    status?: "supported" | "unsupported" | "insufficient" | string;
    should_retry?: boolean;
    should_refuse?: boolean;
    reason?: string | null;
  };
  retry?: {
    triggered?: boolean;
    query?: string | null;
    added_context_count?: number;
    added_context_chunks?: RagTraceChunk[];
  };
  final_action?: string;
}

export interface RagAnswerSentenceCheck {
  index?: number;
  sentence?: string;
  status?: "supported" | "unsupported" | "insufficient" | string;
  score?: number;
  reason?: string;
  evidence?: Array<{
    chunk_id?: string;
    source_id?: string;
    source_name?: string;
    page_label?: string | null;
    overlap_terms?: string[];
    excerpt?: string;
  }>;
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
  retrieval_channel?: string | null;
  retrieval_channels?: string[] | null;
  vector_rank?: number | null;
  text_rank?: number | null;
  rrf_score?: number | null;
  fusion_query?: string | null;
  fusion_query_index?: number | null;
  fusion_channel?: string | null;
  fusion_query_hits?: number[] | null;
  fusion_rank_contributions?: Record<string, number> | null;
  final_rank?: number | null;
  rank_before_fusion?: number | null;
  rank_after_fusion?: number | null;
  rank_after_rerank?: number | null;
  excerpt?: string;
  text?: string;
  metadata?: Record<string, unknown>;
}

export interface RagFusionQueryCandidates {
  query?: string;
  query_index?: number;
  vector_candidate_chunks?: RagTraceChunk[];
  text_candidate_chunks?: RagTraceChunk[];
  fused_candidate_chunks?: RagTraceChunk[];
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
  enhancement: "none" | "rewrite" | "hyde" | "fusion" | string;
  rerank: boolean;
  llmRerank: boolean;
  mmr: boolean;
  prioritizeTable: boolean;
  graphEnabled: boolean;
  graphProvider: "lightrag" | "nano" | string;
  graphSearchType: "local" | "global" | "hybrid" | string;
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
  permissionLeakCount: number;
  permissionLeakTotal: number;
  permissionLeakRate?: number | null;
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

export interface RagEvalRunPayload {
  selectedFileIds?: string[] | null;
  strategies?: string[];
  experimentTag?: string | null;
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
  settingsSnapshot?: Record<string, unknown>;
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
