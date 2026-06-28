import type { ChatSettings, GraphServiceConfig, ServiceConfigs } from "./types";

const defaultServiceConfigs: ServiceConfigs = {
  rerank: {
    enabled: true,
    provider: "default",
    model: "",
    baseUrl: "",
    apiKey: "",
    timeout: 30,
  },
  graph: {
    enabled: false,
    provider: "lightrag",
    searchType: "local",
    batchSize: 5,
  },
  fileProcessing: {
    readerMode: "default",
    ocrProvider: "default",
    chunkSize: 1024,
    chunkOverlap: 256,
    tableExtraction: true,
  },
  securityAudit: {
    enabled: true,
    logRetentionDays: 180,
    auditFrequency: "monthly",
    maskSecrets: true,
  },
};

type LegacyRetrievalGraphSettings = {
  graphEnabled?: boolean;
  graphProvider?: string;
  graphSearchType?: string;
};

const cleanRetrievalSettings = (retrieval: ChatSettings["retrieval"]) => {
  const {
    graphEnabled: _graphEnabled,
    graphProvider: _graphProvider,
    graphSearchType: _graphSearchType,
    ...cleanRetrieval
  } = retrieval as ChatSettings["retrieval"] & LegacyRetrievalGraphSettings;

  return cleanRetrieval;
};

export function normalizeChatSettings(settings: ChatSettings): ChatSettings {
  const legacyGraph = settings.retrieval as ChatSettings["retrieval"] &
    LegacyRetrievalGraphSettings;
  const serviceConfigs = settings.serviceConfigs ?? defaultServiceConfigs;
  const graph = serviceConfigs.graph;

  return {
    ...settings,
    retrieval: cleanRetrievalSettings(settings.retrieval),
    serviceConfigs: {
      ...defaultServiceConfigs,
      ...serviceConfigs,
      rerank: {
        ...defaultServiceConfigs.rerank,
        ...serviceConfigs.rerank,
      },
      graph: {
        ...defaultServiceConfigs.graph,
        ...graph,
        enabled: graph?.enabled ?? legacyGraph.graphEnabled ?? false,
        provider: graph?.provider ?? legacyGraph.graphProvider ?? "lightrag",
        searchType: graph?.searchType ?? legacyGraph.graphSearchType ?? "local",
      },
      fileProcessing: {
        ...defaultServiceConfigs.fileProcessing,
        ...serviceConfigs.fileProcessing,
      },
      securityAudit: {
        ...defaultServiceConfigs.securityAudit,
        ...serviceConfigs.securityAudit,
      },
    },
  };
}

export function graphServiceConfig(settings: ChatSettings): GraphServiceConfig {
  return normalizeChatSettings(settings).serviceConfigs.graph;
}

export function patchGraphServiceConfig(
  settings: ChatSettings,
  partial: Partial<GraphServiceConfig>,
): ChatSettings {
  const normalized = normalizeChatSettings(settings);

  return {
    ...normalized,
    serviceConfigs: {
      ...normalized.serviceConfigs,
      graph: {
        ...normalized.serviceConfigs.graph,
        ...partial,
      },
    },
  };
}
