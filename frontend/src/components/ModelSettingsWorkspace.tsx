import {
  BrainCircuit,
  FileCog,
  GitBranch,
  RotateCcw,
  Save,
  ShieldCheck,
} from "lucide-react";
import { normalizeChatSettings, patchGraphServiceConfig } from "../api/settings";
import type { ChatSettings } from "../api/types";
import { SelectField } from "./SelectField";
import { SwitchField } from "./SwitchField";

interface ModelSettingsWorkspaceProps {
  settings: ChatSettings;
  onChange: (settings: ChatSettings) => void;
  onSave: () => void;
}

export function ModelSettingsWorkspace({
  settings,
  onChange,
  onSave,
}: ModelSettingsWorkspaceProps) {
  const normalizedSettings = normalizeChatSettings(settings);
  const patch = (partial: Partial<ChatSettings>) =>
    onChange({ ...normalizedSettings, ...partial });
  const patchGraph = (partial: Parameters<typeof patchGraphServiceConfig>[1]) =>
    onChange(patchGraphServiceConfig(normalizedSettings, partial));
  const options = normalizedSettings.options ?? {};
  const optionOr = (key: string, defaultOptions: { label: string; value: string }[]) =>
    options[key]?.length ? options[key] : defaultOptions;
  const modelConfig = settings.modelConfig ?? {
    name: "",
    baseUrl: "",
    model: "",
    apiKey: "",
    timeout: 60,
    isDefault: false,
  };
  const embeddingConfig = settings.embeddingConfig ?? {
    name: "",
    baseUrl: "",
    model: "",
    apiKey: "",
    timeout: 30,
    isDefault: false,
  };
  const modelConfigs = settings.modelConfigs ?? {};
  const embeddingConfigs = settings.embeddingConfigs ?? {};
  const selectedModelConfig = normalizedSettings.model
    ? modelConfigs[normalizedSettings.model]
    : undefined;
  const selectedEmbeddingConfig = normalizedSettings.embeddingModel
    ? embeddingConfigs[normalizedSettings.embeddingModel]
    : undefined;
  const patchModelConfig = (partial: Partial<typeof modelConfig>) =>
    patch({ modelConfig: { ...modelConfig, ...partial } });
  const patchEmbeddingConfig = (partial: Partial<typeof embeddingConfig>) =>
    patch({ embeddingConfig: { ...embeddingConfig, ...partial } });
  const serviceConfigs = normalizedSettings.serviceConfigs;
  const patchServiceConfig = <T extends keyof typeof serviceConfigs>(
    key: T,
    partial: Partial<(typeof serviceConfigs)[T]>,
  ) =>
    patch({
      serviceConfigs: {
        ...serviceConfigs,
        [key]: { ...serviceConfigs[key], ...partial },
      },
    });

  return (
    <main className="settings-workspace">
      <section className="settings-main-panel">
        <header className="settings-workspace__head">
          <div>
            <span>系统设置</span>
            <strong>模型配置</strong>
          </div>
          <button className="settings-save-button" type="button" onClick={onSave}>
            <Save size={16} />
            保存设置
          </button>
        </header>

        <div className="model-settings-grid">
          <section className="model-settings-card">
            <div className="section-title">
              <BrainCircuit size={16} />
              LLM 大模型
            </div>
            <SelectField
              label="当前模型"
              value={normalizedSettings.model}
              onChange={(model) => patch({ model })}
              options={optionOr("model", [{ label: "默认", value: "" }])}
            />
            {selectedModelConfig ? (
              <p className="settings-note">
                当前使用：{selectedModelConfig.model || selectedModelConfig.name}
                {selectedModelConfig.baseUrl ? ` · ${selectedModelConfig.baseUrl}` : ""}
              </p>
            ) : null}
            <div className="settings-subtitle">新增 LLM 配置</div>
            <div className="model-config-grid">
              <label className="field">
                <span>配置名称</span>
                <input
                  value={modelConfig.name}
                  onChange={(event) => patchModelConfig({ name: event.target.value })}
                  placeholder="deepseek"
                />
              </label>
              <label className="field">
                <span>模型 ID</span>
                <input
                  value={modelConfig.model}
                  onChange={(event) => patchModelConfig({ model: event.target.value })}
                  placeholder="deepseek-chat"
                />
              </label>
              <label className="field model-config-grid__wide">
                <span>Base URL</span>
                <input
                  value={modelConfig.baseUrl}
                  onChange={(event) => patchModelConfig({ baseUrl: event.target.value })}
                  placeholder="https://api.deepseek.com"
                />
              </label>
              <label className="field">
                <span>API Key</span>
                <input
                  type="password"
                  value={modelConfig.apiKey}
                  onChange={(event) => patchModelConfig({ apiKey: event.target.value })}
                  placeholder={modelConfig.hasApiKey ? "已配置，留空保持不变" : "sk-..."}
                />
              </label>
              <label className="field">
                <span>超时秒数</span>
                <input
                  type="number"
                  min="1"
                  max="600"
                  value={modelConfig.timeout ?? 60}
                  onChange={(event) =>
                    patchModelConfig({ timeout: Number(event.target.value) || 60 })
                  }
                />
              </label>
              <SwitchField
                label="保存后设为默认 LLM"
                checked={modelConfig.isDefault}
                onChange={(isDefault) => patchModelConfig({ isDefault })}
              />
            </div>
          </section>

          <section className="model-settings-card">
            <div className="section-title">
              <BrainCircuit size={16} />
              Embedding 模型
            </div>
            <SelectField
              label="当前模型"
              value={normalizedSettings.embeddingModel ?? ""}
              onChange={(embeddingModel) => patch({ embeddingModel })}
              options={optionOr("embeddingModel", [{ label: "默认", value: "" }])}
            />
            {selectedEmbeddingConfig ? (
              <p className="settings-note">
                当前使用：{selectedEmbeddingConfig.model || selectedEmbeddingConfig.name}
                {selectedEmbeddingConfig.baseUrl
                  ? ` · ${selectedEmbeddingConfig.baseUrl}`
                  : ""}
              </p>
            ) : null}
            <div className="settings-subtitle">新增 Embedding 配置</div>
            <div className="model-config-grid">
              <label className="field">
                <span>配置名称</span>
                <input
                  value={embeddingConfig.name}
                  onChange={(event) =>
                    patchEmbeddingConfig({ name: event.target.value })
                  }
                  placeholder="ollama"
                />
              </label>
              <label className="field">
                <span>模型 ID</span>
                <input
                  value={embeddingConfig.model}
                  onChange={(event) =>
                    patchEmbeddingConfig({ model: event.target.value })
                  }
                  placeholder="nomic-embed-text"
                />
              </label>
              <label className="field model-config-grid__wide">
                <span>Base URL</span>
                <input
                  value={embeddingConfig.baseUrl}
                  onChange={(event) =>
                    patchEmbeddingConfig({ baseUrl: event.target.value })
                  }
                  placeholder="http://localhost:11434/v1"
                />
              </label>
              <label className="field">
                <span>API Key</span>
                <input
                  type="password"
                  value={embeddingConfig.apiKey}
                  onChange={(event) =>
                    patchEmbeddingConfig({ apiKey: event.target.value })
                  }
                  placeholder={
                    embeddingConfig.hasApiKey ? "已配置，留空保持不变" : "ollama"
                  }
                />
              </label>
              <label className="field">
                <span>超时秒数</span>
                <input
                  type="number"
                  min="1"
                  max="600"
                  value={embeddingConfig.timeout ?? 30}
                  onChange={(event) =>
                    patchEmbeddingConfig({ timeout: Number(event.target.value) || 30 })
                  }
                />
              </label>
              <SwitchField
                label="保存后设为默认 Embedding"
                checked={embeddingConfig.isDefault}
                onChange={(isDefault) => patchEmbeddingConfig({ isDefault })}
              />
            </div>
            <p className="settings-note">
              Embedding 模型会影响后续上传和重新 embedding 的文件。
            </p>
          </section>

          <section className="model-settings-card">
            <div className="section-title">
              <RotateCcw size={16} />
              Rerank 服务
            </div>
            <SwitchField
              label="启用 Rerank"
              checked={serviceConfigs.rerank.enabled}
              onChange={(enabled) => {
                patchServiceConfig("rerank", { enabled });
                patch({
                  retrieval: { ...settings.retrieval, rerank: enabled },
                  serviceConfigs: {
                    ...serviceConfigs,
                    rerank: { ...serviceConfigs.rerank, enabled },
                  },
                });
              }}
            />
            <div className="model-config-grid">
              <label className="field">
                <span>服务提供方</span>
                <input
                  value={serviceConfigs.rerank.provider}
                  onChange={(event) =>
                    patchServiceConfig("rerank", { provider: event.target.value })
                  }
                  placeholder="cohere / voyage / tei"
                />
              </label>
              <label className="field">
                <span>模型 ID</span>
                <input
                  value={serviceConfigs.rerank.model}
                  onChange={(event) =>
                    patchServiceConfig("rerank", { model: event.target.value })
                  }
                  placeholder="rerank-v3.5"
                />
              </label>
              <label className="field model-config-grid__wide">
                <span>Base URL</span>
                <input
                  value={serviceConfigs.rerank.baseUrl}
                  onChange={(event) =>
                    patchServiceConfig("rerank", { baseUrl: event.target.value })
                  }
                  placeholder="https://api.example.com"
                />
              </label>
              <label className="field">
                <span>API Key</span>
                <input
                  type="password"
                  value={serviceConfigs.rerank.apiKey}
                  onChange={(event) =>
                    patchServiceConfig("rerank", { apiKey: event.target.value })
                  }
                  placeholder={
                    serviceConfigs.rerank.hasApiKey ? "已配置，留空保持不变" : "key"
                  }
                />
              </label>
              <label className="field">
                <span>超时秒数</span>
                <input
                  type="number"
                  min="1"
                  max="600"
                  value={serviceConfigs.rerank.timeout ?? 30}
                  onChange={(event) =>
                    patchServiceConfig("rerank", {
                      timeout: Number(event.target.value) || 30,
                    })
                  }
                />
              </label>
            </div>
          </section>

          <section className="model-settings-card">
            <div className="section-title">
              <GitBranch size={16} />
              GraphRAG 服务
            </div>
            <SwitchField
              label="启用 GraphRAG"
              checked={serviceConfigs.graph.enabled}
              onChange={(enabled) => patchGraph({ enabled })}
            />
            <SelectField
              label="图谱引擎"
              value={serviceConfigs.graph.provider}
              onChange={(provider) => patchGraph({ provider })}
              options={optionOr("graphProvider", [
                { label: "LightRAG", value: "lightrag" },
                { label: "NanoGraphRAG", value: "nano" },
              ])}
            />
            <SelectField
              label="检索方式"
              value={serviceConfigs.graph.searchType}
              onChange={(searchType) => patchGraph({ searchType })}
              options={optionOr("graphSearchType", [
                { label: "Local", value: "local" },
                { label: "Global", value: "global" },
                { label: "Hybrid", value: "hybrid" },
              ])}
            />
            <label className="field">
              <span>索引批大小</span>
              <input
                type="number"
                min="1"
                max="100"
                value={serviceConfigs.graph.batchSize}
                onChange={(event) =>
                  patchServiceConfig("graph", {
                    batchSize: Number(event.target.value) || 5,
                  })
                }
              />
            </label>
          </section>

          <section className="model-settings-card">
            <div className="section-title">
              <FileCog size={16} />
              文件处理服务
            </div>
            <div className="model-config-grid">
              <label className="field">
                <span>Reader 模式</span>
                <input
                  value={serviceConfigs.fileProcessing.readerMode}
                  onChange={(event) =>
                    patchServiceConfig("fileProcessing", {
                      readerMode: event.target.value,
                    })
                  }
                  placeholder="default"
                />
              </label>
              <label className="field">
                <span>OCR 服务</span>
                <input
                  value={serviceConfigs.fileProcessing.ocrProvider}
                  onChange={(event) =>
                    patchServiceConfig("fileProcessing", {
                      ocrProvider: event.target.value,
                    })
                  }
                  placeholder="default / paddleocr / docling"
                />
              </label>
              <label className="field">
                <span>默认 Chunk Size</span>
                <input
                  type="number"
                  min="1"
                  max="50000"
                  value={serviceConfigs.fileProcessing.chunkSize}
                  onChange={(event) =>
                    patchServiceConfig("fileProcessing", {
                      chunkSize: Number(event.target.value) || 1024,
                    })
                  }
                />
              </label>
              <label className="field">
                <span>默认 Chunk Overlap</span>
                <input
                  type="number"
                  min="0"
                  max="50000"
                  value={serviceConfigs.fileProcessing.chunkOverlap}
                  onChange={(event) =>
                    patchServiceConfig("fileProcessing", {
                      chunkOverlap: Number(event.target.value) || 0,
                    })
                  }
                />
              </label>
              <SwitchField
                label="启用表格抽取"
                checked={serviceConfigs.fileProcessing.tableExtraction}
                onChange={(tableExtraction) =>
                  patchServiceConfig("fileProcessing", { tableExtraction })
                }
              />
            </div>
          </section>

          <section className="model-settings-card">
            <div className="section-title">
              <ShieldCheck size={16} />
              安全与审计
            </div>
            <SwitchField
              label="启用审计日志"
              checked={serviceConfigs.securityAudit.enabled}
              onChange={(enabled) =>
                patchServiceConfig("securityAudit", { enabled })
              }
            />
            <div className="model-config-grid">
              <label className="field">
                <span>日志保留天数</span>
                <input
                  type="number"
                  min="1"
                  max="3650"
                  value={serviceConfigs.securityAudit.logRetentionDays}
                  onChange={(event) =>
                    patchServiceConfig("securityAudit", {
                      logRetentionDays: Number(event.target.value) || 180,
                    })
                  }
                />
              </label>
              <SelectField
                label="审计频率"
                value={serviceConfigs.securityAudit.auditFrequency}
                onChange={(auditFrequency) =>
                  patchServiceConfig("securityAudit", { auditFrequency })
                }
                options={[
                  { label: "每日", value: "daily" },
                  { label: "每周", value: "weekly" },
                  { label: "每月", value: "monthly" },
                  { label: "每季度", value: "quarterly" },
                ]}
              />
              <SwitchField
                label="日志脱敏"
                checked={serviceConfigs.securityAudit.maskSecrets}
                onChange={(maskSecrets) =>
                  patchServiceConfig("securityAudit", { maskSecrets })
                }
              />
            </div>
          </section>
        </div>
      </section>
    </main>
  );
}
