import type {
  ChatMessage,
  ChatSettings,
  Conversation,
  FileDetail,
  FileDirectory,
  FileWorkspaceState,
  RagTraceDetail,
  RagTraceSummary,
  RagEvalDataset,
  RagEvalDatasetPayload,
  RagEvalExample,
  RagEvalExamplePayload,
  RagEvalRun,
  RagEvalRunPayload,
  RagEvalRunDetail,
  ReferenceDocument,
  SendMessagePayload,
  SendMessageResult,
  SourcePermissionItem,
  UploadIndexingOptions,
} from "./types";

const API_BASE = import.meta.env.VITE_API_BASE_URL ?? "/api/react";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    headers: {
      "Content-Type": "application/json",
      ...init?.headers,
    },
    ...init,
  });

  if (!response.ok) {
    throw new Error(await errorMessage(response, "API"));
  }

  return response.json() as Promise<T>;
}

async function errorMessage(response: Response, label: string): Promise<string> {
  const fallback = `${label} ${response.status}: ${response.statusText}`;
  const contentType = response.headers.get("Content-Type") ?? "";
  try {
    if (contentType.includes("application/json")) {
      const body = await response.json();
      return body.detail || body.message || fallback;
    }
    const text = await response.text();
    return text || fallback;
  } catch {
    return fallback;
  }
}

async function* streamFromResponse(response: Response): AsyncGenerator<string, SendMessageResult, void> {
  if (!response.ok || !response.body) {
    throw new Error(await errorMessage(response, "Stream API"));
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let finalResult: SendMessageResult | undefined;

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;

    buffer += decoder.decode(value, { stream: true });
    const events = buffer.split("\n\n");
    buffer = events.pop() ?? "";

    for (const rawEvent of events) {
      const eventType = rawEvent.match(/^event:\s*(.+)$/m)?.[1]?.trim();
      const dataLine = rawEvent.match(/^data:\s*(.+)$/m)?.[1];
      if (!eventType || !dataLine) continue;

      const data = JSON.parse(dataLine);
      if (eventType === "token") {
        yield data.token;
      }
      if (eventType === "done") {
        finalResult = data as SendMessageResult;
      }
      if (eventType === "error") {
        throw new Error(data.message ?? "Stream API failed.");
      }
    }
  }

  if (!finalResult) {
    throw new Error("Stream ended without a final message.");
  }

  return finalResult;
}

export const apiClient = {
  health: () => request<{ status: string; runtime: boolean; time: string }>("/health"),

  listConversations: () => request<Conversation[]>("/conversations"),

  createConversation: () => request<Conversation>("/conversations", { method: "POST" }),

  deleteConversation: (conversationId: string) =>
    request<void>(`/conversations/${conversationId}`, { method: "DELETE" }),

  renameConversation: (conversationId: string, title: string) =>
    request<Conversation>(`/conversations/${conversationId}`, {
      method: "PATCH",
      body: JSON.stringify({ title }),
    }),

  listMessages: (conversationId: string) =>
    request<ChatMessage[]>(`/conversations/${conversationId}/messages`),

  listTraces: (conversationId: string) =>
    request<RagTraceSummary[]>(`/conversations/${conversationId}/traces`),

  getTrace: (traceId: string) => request<RagTraceDetail>(`/traces/${traceId}`),

  getMessageTrace: (messageId: string) =>
    request<RagTraceDetail | null>(`/messages/${encodeURIComponent(messageId)}/trace`),

  sendMessage: (payload: SendMessagePayload) =>
    request<SendMessageResult>("/chat/send", {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  async *streamMessage(payload: SendMessagePayload): AsyncGenerator<string, SendMessageResult, void> {
    const response = await fetch(`${API_BASE}/chat/stream`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    return yield* streamFromResponse(response);
  },

  async uploadFiles(files: File[], directoryId?: string | null, options: UploadIndexingOptions = {}) {
    const formData = new FormData();
    files.forEach((file) => formData.append("files", file));
    const params = new URLSearchParams();
    if (directoryId) params.set("directory_id", directoryId);
    if (options.chunkSize) params.set("chunk_size", String(options.chunkSize));
    if (options.chunkOverlap !== undefined && options.chunkOverlap !== null) {
      params.set("chunk_overlap", String(options.chunkOverlap));
    }
    if (options.reindex) params.set("reindex", "true");
    const query = params.toString() ? `?${params.toString()}` : "";
    const response = await fetch(`${API_BASE}/files/upload${query}`, {
      method: "POST",
      body: formData,
    });
    if (!response.ok) {
      throw new Error(await errorMessage(response, "Upload API"));
    }
    return response.json() as Promise<ReferenceDocument[]>;
  },

  reembedFile: (fileId: string, options: UploadIndexingOptions = {}) => {
    const params = new URLSearchParams();
    if (options.chunkSize) params.set("chunk_size", String(options.chunkSize));
    if (options.chunkOverlap !== undefined && options.chunkOverlap !== null) {
      params.set("chunk_overlap", String(options.chunkOverlap));
    }
    const query = params.toString() ? `?${params.toString()}` : "";
    return request<FileDetail>(`/files/${fileId}/reembed${query}`, { method: "POST" });
  },

  deleteFile: (fileId: string) =>
    request<void>(`/files/${fileId}`, { method: "DELETE" }),

  listFileWorkspace: () => request<FileWorkspaceState>("/files/workspace"),

  getFileDetail: (fileId: string, typeFilter = "all") =>
    request<FileDetail>(`/files/${fileId}?type_filter=${encodeURIComponent(typeFilter)}`),

  updateFilePermissions: (fileId: string, permissions: SourcePermissionItem[]) =>
    request<FileDetail>(`/files/${fileId}/permissions`, {
      method: "PATCH",
      body: JSON.stringify({ permissions }),
    }),

  createDirectory: (name: string, fileIds: string[] = []) =>
    request<FileDirectory>("/files/directories", {
      method: "POST",
      body: JSON.stringify({ name, fileIds }),
    }),

  updateDirectory: (directoryId: string, payload: { name?: string; fileIds?: string[] }) =>
    request<FileDirectory>(`/files/directories/${directoryId}`, {
      method: "PATCH",
      body: JSON.stringify(payload),
    }),

  deleteDirectory: (directoryId: string) =>
    request<void>(`/files/directories/${directoryId}`, { method: "DELETE" }),

  moveFiles: (fileIds: string[], directoryId?: string | null) =>
    request<FileWorkspaceState>("/files/move", {
      method: "POST",
      body: JSON.stringify({ fileIds, directoryId: directoryId ?? null }),
    }),

  getChatSettings: () => request<ChatSettings>("/settings"),

  saveChatSettings: (settings: ChatSettings) =>
    request<ChatSettings>("/settings", {
      method: "PUT",
      body: JSON.stringify(settings),
    }),

  listDefaultReferences: () => request<ReferenceDocument[]>("/references/default"),

  listEvalDatasets: () => request<RagEvalDataset[]>("/eval/datasets"),

  createEvalDataset: (payload: RagEvalDatasetPayload) =>
    request<RagEvalDataset>("/eval/datasets", {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  updateEvalDataset: (datasetId: string, payload: RagEvalDatasetPayload) =>
    request<RagEvalDataset>(`/eval/datasets/${datasetId}`, {
      method: "PATCH",
      body: JSON.stringify(payload),
    }),

  deleteEvalDataset: (datasetId: string) =>
    request<void>(`/eval/datasets/${datasetId}`, { method: "DELETE" }),

  listEvalExamples: (datasetId: string) =>
    request<RagEvalExample[]>(`/eval/datasets/${datasetId}/examples`),

  createEvalExample: (datasetId: string, payload: RagEvalExamplePayload) =>
    request<RagEvalExample>(`/eval/datasets/${datasetId}/examples`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  updateEvalExample: (exampleId: string, payload: RagEvalExamplePayload) =>
    request<RagEvalExample>(`/eval/examples/${exampleId}`, {
      method: "PATCH",
      body: JSON.stringify(payload),
    }),

  deleteEvalExample: (exampleId: string) =>
    request<void>(`/eval/examples/${exampleId}`, { method: "DELETE" }),

  runEvalExample: (exampleId: string, payload: RagEvalRunPayload = {}) =>
    request<RagEvalRunDetail>(`/eval/examples/${exampleId}/run`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  compareEvalExample: (exampleId: string, payload: RagEvalRunPayload = {}) =>
    request<RagEvalRun[]>(`/eval/examples/${exampleId}/compare`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  runEvalDataset: (datasetId: string, payload: RagEvalRunPayload = {}) =>
    request<RagEvalRun[]>(`/eval/datasets/${datasetId}/run`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  listEvalRuns: (datasetId?: string, exampleId?: string, limit = 50) => {
    const params = new URLSearchParams();
    if (datasetId) params.set("dataset_id", datasetId);
    if (exampleId) params.set("example_id", exampleId);
    params.set("limit", String(limit));
    return request<RagEvalRun[]>(`/eval/runs?${params.toString()}`);
  },

  getEvalRun: (runId: string) => request<RagEvalRunDetail>(`/eval/runs/${runId}`),
};
