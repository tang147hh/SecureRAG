import { ChangeEvent, useRef } from "react";
import {
  Database,
  FileUp,
  Loader2,
  Plus,
  SlidersHorizontal,
  Sparkles,
} from "lucide-react";
import type { ChatSettings } from "../api/types";
import { graphServiceConfig, patchGraphServiceConfig } from "../api/settings";
import { SelectField } from "./SelectField";
import { SwitchField } from "./SwitchField";

interface ChatSettingsPanelProps {
  settings: ChatSettings;
  onChange: (settings: ChatSettings) => void;
  onUploadFiles: (files: File[]) => void;
  isUploading: boolean;
}

export function ChatSettingsPanel({
  settings,
  onChange,
  onUploadFiles,
  isUploading,
}: ChatSettingsPanelProps) {
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const patch = (partial: Partial<ChatSettings>) => onChange({ ...settings, ...partial });
  const patchRetrieval = (partial: Partial<ChatSettings["retrieval"]>) =>
    patch({ retrieval: { ...settings.retrieval, ...partial } });
  const patchGraph = (partial: Parameters<typeof patchGraphServiceConfig>[1]) =>
    onChange(patchGraphServiceConfig(settings, partial));
  const options = settings.options ?? {};
  const optionOr = (key: string, defaultOptions: { label: string; value: string }[]) =>
    options[key]?.length ? options[key] : defaultOptions;
  const retrievalEnhancement = settings.retrieval.enhancement ?? "none";
  const graphConfig = graphServiceConfig(settings);
  const retrievalEnhancementOptions = optionOr("retrievalEnhancement", [
    { label: "None", value: "none" },
    { label: "Query Rewrite", value: "rewrite" },
    { label: "HyDE", value: "hyde" },
  ]).filter((option) => option.value !== "fusion");
  const promptTemplates = settings.promptTemplates ?? {
    [settings.promptTemplate]: settings.promptTemplateText,
  };
  const promptTemplateOptions = Object.keys(promptTemplates).map((name) => ({
    label: name,
    value: name,
  }));
  const handleFileChange = (event: ChangeEvent<HTMLInputElement>) => {
    onUploadFiles(Array.from(event.target.files ?? []));
    event.target.value = "";
  };
  const uniquePromptName = () => {
    const baseName = "新 Prompt 模板";
    if (!promptTemplates[baseName]) return baseName;
    let index = 2;
    while (promptTemplates[`${baseName} ${index}`]) index += 1;
    return `${baseName} ${index}`;
  };
  const patchPromptTemplates = (
    promptTemplate: string,
    promptTemplateText: string,
    nextPromptTemplates: Record<string, string>,
  ) =>
    patch({
      promptTemplate,
      promptTemplateText,
      promptTemplates: nextPromptTemplates,
      options: {
        ...options,
        promptTemplate: Object.keys(nextPromptTemplates).map((name) => ({
          label: name,
          value: name,
        })),
      },
    });
  const handlePromptSelect = (promptTemplate: string) => {
    patchPromptTemplates(
      promptTemplate,
      promptTemplates[promptTemplate] ?? "",
      promptTemplates,
    );
  };
  const handlePromptNameChange = (nextName: string) => {
    if (!nextName.trim()) return;

    const nextPromptTemplates = { ...promptTemplates };
    const currentText = settings.promptTemplateText;
    const previousName = settings.promptTemplate;

    if (nextName.trim() && previousName !== nextName) {
      if (previousName !== "默认 RAG") {
        delete nextPromptTemplates[previousName];
      }
      nextPromptTemplates[nextName] = currentText;
    }

    patchPromptTemplates(nextName, currentText, nextPromptTemplates);
  };
  const handlePromptTextChange = (promptTemplateText: string) => {
    patchPromptTemplates(settings.promptTemplate, promptTemplateText, {
      ...promptTemplates,
      [settings.promptTemplate]: promptTemplateText,
    });
  };
  const handleAddPromptTemplate = () => {
    const promptTemplate = uniquePromptName();
    patchPromptTemplates(promptTemplate, settings.promptTemplateText, {
      ...promptTemplates,
      [promptTemplate]: settings.promptTemplateText,
    });
  };

  return (
    <div className="settings-stack">
      <section className="sidebar-section quick-upload">
        <div className="section-heading">
          <div>
            <span>快速上传</span>
            <small>PDF / DOCX / Markdown</small>
          </div>
          <FileUp size={18} />
        </div>
        <input
          ref={fileInputRef}
          className="visually-hidden"
          type="file"
          multiple
          onChange={handleFileChange}
        />
        <button
          className="upload-dropzone"
          type="button"
          disabled={isUploading}
          onClick={() => fileInputRef.current?.click()}
        >
          {isUploading ? <Loader2 className="spin" size={18} /> : <Database size={18} />}
          <span>{isUploading ? "上传中..." : "选择文件上传"}</span>
        </button>
      </section>

      <section className="sidebar-section">
        <div className="section-title">
          <Sparkles size={16} />
          生成设置
        </div>
        <SwitchField
          label="建议聊天"
          description="自动显示追问建议"
          checked={settings.suggestedChat}
          onChange={(suggestedChat) => patch({ suggestedChat })}
        />
        <SelectField
          label="Reasoning"
          value={settings.reasoningMethod}
          onChange={(reasoningMethod) => patch({ reasoningMethod })}
          options={optionOr("reasoningMethod", [{ label: settings.reasoningMethod, value: settings.reasoningMethod }])}
        />
        <SelectField
          label="LLM 模型"
          value={settings.model}
          onChange={(model) => patch({ model })}
          options={optionOr("model", [{ label: "默认", value: "" }])}
        />
        <SelectField
          label="语言"
          value={settings.language}
          onChange={(language) => patch({ language })}
          options={optionOr("language", [{ label: settings.language, value: settings.language }])}
        />
        <SelectField
          label="引用高亮"
          value={settings.citationHighlight}
          onChange={(citationHighlight) => patch({ citationHighlight })}
          options={optionOr("citationHighlight", [
            { label: "高亮", value: "highlight" },
            { label: "内联", value: "inline" },
            { label: "关闭", value: "off" },
          ])}
        />
        <SwitchField
          label="Mindmap"
          checked={settings.mindmap}
          onChange={(mindmap) => patch({ mindmap })}
        />
        <div className="prompt-template-editor">
          <div className="prompt-template-editor__head">
            <SelectField
              label="Prompt 模板"
              value={settings.promptTemplate}
              onChange={handlePromptSelect}
              options={
                promptTemplateOptions.length
                  ? promptTemplateOptions
                  : optionOr("promptTemplate", [
                      { label: settings.promptTemplate, value: settings.promptTemplate },
                    ])
              }
            />
            <button
              className="prompt-template-editor__add"
              type="button"
              onClick={handleAddPromptTemplate}
              aria-label="新增 Prompt 模板"
              title="新增 Prompt 模板"
            >
              <Plus size={16} />
            </button>
          </div>
          <label className="field">
            <span>模板名称</span>
            <input
              value={settings.promptTemplate}
              onChange={(event) => handlePromptNameChange(event.target.value)}
            />
          </label>
          <label className="field">
            <span>Prompt 内容</span>
            <textarea
              value={settings.promptTemplateText}
              onChange={(event) => handlePromptTextChange(event.target.value)}
              rows={8}
              spellCheck={false}
            />
          </label>
        </div>
      </section>

      <section className="sidebar-section">
        <div className="section-title">
          <SlidersHorizontal size={16} />
          检索参数
        </div>
        <label className="range-field">
          <span>Top K: {settings.retrieval.topK}</span>
          <input
            type="range"
            min="3"
            max="20"
            value={settings.retrieval.topK}
            onChange={(event) => patchRetrieval({ topK: Number(event.target.value) })}
          />
        </label>
        <label className="range-field">
          <span>首轮倍数: {settings.retrieval.firstRoundMultiplier}</span>
          <input
            type="range"
            min="1"
            max="20"
            value={settings.retrieval.firstRoundMultiplier}
            onChange={(event) =>
              patchRetrieval({ firstRoundMultiplier: Number(event.target.value) })
            }
          />
        </label>
        <SelectField
          label="检索模式"
          value={settings.retrieval.retrievalMode}
          onChange={(retrievalMode) => patchRetrieval({ retrievalMode })}
          options={optionOr("retrievalMode", [
            { label: "Hybrid", value: "hybrid" },
            { label: "Vector", value: "vector" },
            { label: "Full Text", value: "text" },
          ])}
        />
        <SelectField
          label="检索增强"
          value={retrievalEnhancement === "fusion" ? "none" : retrievalEnhancement}
          onChange={(enhancement) => patchRetrieval({ enhancement })}
          options={retrievalEnhancementOptions}
        />
        <SwitchField
          label="RAG-Fusion"
          description="生成多个查询视角并用 RRF 融合候选"
          checked={retrievalEnhancement === "fusion"}
          onChange={(enabled) =>
            patchRetrieval({ enhancement: enabled ? "fusion" : "none" })
          }
        />
        <SwitchField
          label="Rerank"
          checked={settings.retrieval.rerank}
          onChange={(rerank) => patchRetrieval({ rerank })}
        />
        <SwitchField
          label="LLM Rerank"
          checked={settings.retrieval.llmRerank}
          onChange={(llmRerank) => patchRetrieval({ llmRerank })}
        />
        <SwitchField
          label="MMR"
          checked={settings.retrieval.mmr}
          onChange={(mmr) => patchRetrieval({ mmr })}
        />
        <SwitchField
          label="优先表格"
          checked={settings.retrieval.prioritizeTable}
          onChange={(prioritizeTable) => patchRetrieval({ prioritizeTable })}
        />
        <SwitchField
          label="GraphRAG"
          description="融合实体、关系和多跳路径证据"
          checked={graphConfig.enabled}
          onChange={(enabled) => patchGraph({ enabled })}
        />
        {graphConfig.enabled ? (
          <>
            <SelectField
              label="图谱引擎"
              value={graphConfig.provider ?? "lightrag"}
              onChange={(provider) => patchGraph({ provider })}
              options={optionOr("graphProvider", [
                { label: "LightRAG", value: "lightrag" },
                { label: "NanoGraphRAG", value: "nano" },
              ])}
            />
            <SelectField
              label="图谱检索"
              value={graphConfig.searchType ?? "local"}
              onChange={(searchType) => patchGraph({ searchType })}
              options={optionOr("graphSearchType", [
                { label: "Local", value: "local" },
                { label: "Global", value: "global" },
                { label: "Hybrid", value: "hybrid" },
              ])}
            />
          </>
        ) : null}
      </section>
    </div>
  );
}
