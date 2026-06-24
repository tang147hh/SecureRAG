import { useEffect, useMemo, useState } from "react";
import {
  Activity,
  ClipboardList,
  Eye,
  FilePlus2,
  Loader2,
  Play,
  Plus,
  RefreshCw,
  Save,
  Trash2,
} from "lucide-react";
import { apiClient } from "../api/client";
import type {
  FileItem,
  RagEvalDataset,
  RagEvalExample,
  RagEvalExamplePayload,
  RagEvalRun,
  RagEvalRunDetail,
} from "../api/types";
import { IconButton } from "./IconButton";
import { TracePanel } from "./TracePanel";

interface EvaluationWorkspaceProps {
  files: FileItem[];
}

const emptyExample: RagEvalExamplePayload = {
  question: "",
  expectedAnswer: "",
  expectedSourceIds: [],
  expectedKeywords: [],
  evaluatorUserId: "",
  selectedFileIds: [],
  tags: [],
};

export function EvaluationWorkspace({ files }: EvaluationWorkspaceProps) {
  const [datasets, setDatasets] = useState<RagEvalDataset[]>([]);
  const [activeDatasetId, setActiveDatasetId] = useState<string>();
  const [examples, setExamples] = useState<RagEvalExample[]>([]);
  const [runs, setRuns] = useState<RagEvalRun[]>([]);
  const [activeRun, setActiveRun] = useState<RagEvalRunDetail>();
  const [draft, setDraft] = useState<RagEvalExamplePayload>(emptyExample);
  const [editingExampleId, setEditingExampleId] = useState<string>();
  const [loading, setLoading] = useState(true);
  const [runningId, setRunningId] = useState<string>();
  const [error, setError] = useState<string>();

  const activeDataset = useMemo(
    () => datasets.find((dataset) => dataset.id === activeDatasetId),
    [activeDatasetId, datasets],
  );

  const runSummary = useMemo(() => {
    const completed = runs.filter((run) => run.status === "completed");
    const latest = runs[0];
    const avgLatency =
      completed.reduce((total, run) => total + numberMetric(run, "latency_ms"), 0) /
      Math.max(completed.length, 1);
    const leakCount = runs.filter((run) => Boolean(run.metrics.acl_leak_detected)).length;
    return { completed: completed.length, latest, avgLatency, leakCount };
  }, [runs]);

  useEffect(() => {
    let mounted = true;
    apiClient
      .listEvalDatasets()
      .then((items) => {
        if (!mounted) return;
        setDatasets(items);
        setActiveDatasetId((current) => current ?? items[0]?.id);
      })
      .catch((err: unknown) => setError(messageOf(err, "加载测试集失败。")))
      .finally(() => {
        if (mounted) setLoading(false);
      });
    return () => {
      mounted = false;
    };
  }, []);

  useEffect(() => {
    if (!activeDatasetId) {
      setExamples([]);
      setRuns([]);
      setActiveRun(undefined);
      return;
    }
    let mounted = true;
    Promise.all([
      apiClient.listEvalExamples(activeDatasetId),
      apiClient.listEvalRuns(activeDatasetId),
    ])
      .then(([nextExamples, nextRuns]) => {
        if (!mounted) return;
        setExamples(nextExamples);
        setRuns(nextRuns);
        if (nextRuns[0]) void openRun(nextRuns[0].id);
      })
      .catch((err: unknown) => setError(messageOf(err, "加载评测数据失败。")));
    return () => {
      mounted = false;
    };
  }, [activeDatasetId]);

  const refreshDatasets = async () => {
    const next = await apiClient.listEvalDatasets();
    setDatasets(next);
    return next;
  };

  const refreshActive = async () => {
    if (!activeDatasetId) return;
    const [nextExamples, nextRuns] = await Promise.all([
      apiClient.listEvalExamples(activeDatasetId),
      apiClient.listEvalRuns(activeDatasetId),
      refreshDatasets(),
    ]);
    setExamples(nextExamples);
    setRuns(nextRuns);
  };

  const createDataset = async () => {
    const name = window.prompt("测试集名称", "核心知识库回归集");
    if (!name?.trim()) return;
    const dataset = await apiClient.createEvalDataset({ name: name.trim() });
    await refreshDatasets();
    setActiveDatasetId(dataset.id);
  };

  const deleteDataset = async (datasetId: string) => {
    if (!window.confirm("删除测试集会同时删除样例和运行记录，确认继续？")) return;
    await apiClient.deleteEvalDataset(datasetId);
    const next = await refreshDatasets();
    setActiveDatasetId(next[0]?.id);
  };

  const editExample = (example: RagEvalExample) => {
    setEditingExampleId(example.id);
    setDraft({
      question: example.question,
      expectedAnswer: example.expectedAnswer ?? "",
      expectedSourceIds: example.expectedSourceIds,
      expectedKeywords: example.expectedKeywords,
      evaluatorUserId: example.evaluatorUserId,
      selectedFileIds: example.selectedFileIds,
      tags: example.tags,
    });
  };

  const resetDraft = () => {
    setEditingExampleId(undefined);
    setDraft(emptyExample);
  };

  const saveExample = async () => {
    if (!activeDatasetId || !draft.question?.trim()) return;
    const payload = {
      ...draft,
      question: draft.question.trim(),
      evaluatorUserId: draft.evaluatorUserId?.trim() || undefined,
    };
    if (editingExampleId) {
      await apiClient.updateEvalExample(editingExampleId, payload);
    } else {
      await apiClient.createEvalExample(activeDatasetId, payload);
    }
    resetDraft();
    await refreshActive();
  };

  const deleteExample = async (exampleId: string) => {
    if (!window.confirm("删除该测试样例？")) return;
    await apiClient.deleteEvalExample(exampleId);
    await refreshActive();
  };

  const runExample = async (exampleId: string) => {
    setRunningId(exampleId);
    setError(undefined);
    try {
      const result = await apiClient.runEvalExample(exampleId);
      setActiveRun(result);
      await refreshActive();
    } catch (err) {
      setError(messageOf(err, "运行样例失败。"));
    } finally {
      setRunningId(undefined);
    }
  };

  const runDataset = async () => {
    if (!activeDatasetId) return;
    setRunningId(activeDatasetId);
    setError(undefined);
    try {
      await apiClient.runEvalDataset(activeDatasetId);
      await refreshActive();
      const nextRuns = await apiClient.listEvalRuns(activeDatasetId);
      if (nextRuns[0]) await openRun(nextRuns[0].id);
    } catch (err) {
      setError(messageOf(err, "批量运行失败。"));
    } finally {
      setRunningId(undefined);
    }
  };

  const openRun = async (runId: string) => {
    try {
      const detail = await apiClient.getEvalRun(runId);
      setActiveRun(detail);
    } catch (err) {
      setError(messageOf(err, "加载运行详情失败。"));
    }
  };

  return (
    <main className="eval-workspace">
      <aside className="eval-sidebar">
        <div className="drawer-heading">
          <div>
            <strong>测试集</strong>
            <small>{datasets.length} 个评测集合</small>
          </div>
          <IconButton label="新建测试集" onClick={createDataset}>
            <Plus size={17} />
          </IconButton>
        </div>

        <div className="eval-dataset-list">
          {loading ? <EmptyLine text="加载中..." /> : null}
          {!loading && datasets.length === 0 ? <EmptyLine text="暂无测试集" /> : null}
          {datasets.map((dataset) => (
            <article
              className={`eval-dataset-card ${dataset.id === activeDatasetId ? "is-active" : ""}`}
              key={dataset.id}
            >
              <button type="button" onClick={() => setActiveDatasetId(dataset.id)}>
                <strong>{dataset.name}</strong>
                <small>
                  {dataset.exampleCount} 样例 · {dataset.runCount} 结果
                </small>
              </button>
              <IconButton label="删除测试集" onClick={() => deleteDataset(dataset.id)}>
                <Trash2 size={15} />
              </IconButton>
            </article>
          ))}
        </div>
      </aside>

      <section className="eval-main">
        <header className="eval-toolbar">
          <div>
            <span>RAG 评测中心</span>
            <strong>{activeDataset?.name ?? "选择或新建测试集"}</strong>
          </div>
          <div className="file-toolbar__actions">
            <button className="secondary-button" type="button" onClick={refreshActive}>
              <RefreshCw size={15} />
              刷新
            </button>
            <button
              className="primary-button"
              type="button"
              disabled={!activeDatasetId || !examples.length || runningId === activeDatasetId}
              onClick={runDataset}
            >
              {runningId === activeDatasetId ? <Loader2 className="spin" size={15} /> : <Play size={15} />}
              批量运行
            </button>
          </div>
        </header>

        {error ? <div className="eval-error">{error}</div> : null}

        <section className="eval-summary">
          <MetricCard label="样例" value={examples.length} />
          <MetricCard label="已完成" value={runSummary.completed} />
          <MetricCard label="平均耗时" value={`${Math.round(runSummary.avgLatency)} ms`} />
          <MetricCard label="ACL 风险" value={runSummary.leakCount} danger={runSummary.leakCount > 0} />
        </section>

        <section className="eval-editor">
          <div className="section-heading">
            <div>
              <span>{editingExampleId ? "编辑样例" : "新增样例"}</span>
              <small>以 evaluator_user_id 的权限运行正常聊天 pipeline</small>
            </div>
          </div>
          <div className="eval-form-grid">
            <label className="field">
              question
              <textarea
                value={draft.question}
                onChange={(event) => setDraft({ ...draft, question: event.target.value })}
              />
            </label>
            <label className="field">
              expected_answer
              <textarea
                value={draft.expectedAnswer ?? ""}
                onChange={(event) => setDraft({ ...draft, expectedAnswer: event.target.value })}
              />
            </label>
            <TokenField
              label="expected_source_ids"
              value={draft.expectedSourceIds ?? []}
              onChange={(expectedSourceIds) => setDraft({ ...draft, expectedSourceIds })}
            />
            <TokenField
              label="expected_keywords"
              value={draft.expectedKeywords ?? []}
              onChange={(expectedKeywords) => setDraft({ ...draft, expectedKeywords })}
            />
            <label className="field">
              evaluator_user_id
              <input
                value={draft.evaluatorUserId ?? ""}
                onChange={(event) => setDraft({ ...draft, evaluatorUserId: event.target.value })}
                placeholder="默认当前用户"
              />
            </label>
            <TokenField
              label="tags"
              value={draft.tags ?? []}
              onChange={(tags) => setDraft({ ...draft, tags })}
            />
          </div>
          <FileScopePicker
            files={files}
            selectedFileIds={draft.selectedFileIds ?? []}
            onChange={(selectedFileIds) => setDraft({ ...draft, selectedFileIds })}
          />
          <div className="eval-editor-actions">
            <button className="secondary-button" type="button" onClick={resetDraft}>
              <FilePlus2 size={15} />
              清空
            </button>
            <button
              className="primary-button"
              type="button"
              disabled={!activeDatasetId || !draft.question?.trim()}
              onClick={saveExample}
            >
              <Save size={15} />
              保存样例
            </button>
          </div>
        </section>

        <section className="eval-table-section">
          <div className="eval-table-head">
            <span>question</span>
            <span>身份</span>
            <span>期望</span>
            <span>文件范围</span>
            <span>操作</span>
          </div>
          <div className="eval-table">
            {!examples.length ? <EmptyLine text="暂无样例，先新增一条问题。" /> : null}
            {examples.map((example) => (
              <article className="eval-row" key={example.id}>
                <button type="button" onClick={() => editExample(example)}>
                  <strong>{example.question}</strong>
                  <small>{example.tags.join(", ") || "未设置标签"}</small>
                </button>
                <span>{example.evaluatorUserId}</span>
                <span>
                  {example.expectedSourceIds.length} sources · {example.expectedKeywords.length} keywords
                </span>
                <span>{example.selectedFileIds.length || "全部可见"}</span>
                <div className="eval-row-actions">
                  <IconButton label="运行样例" onClick={() => runExample(example.id)}>
                    {runningId === example.id ? <Loader2 className="spin" size={15} /> : <Play size={15} />}
                  </IconButton>
                  <IconButton label="删除样例" onClick={() => deleteExample(example.id)}>
                    <Trash2 size={15} />
                  </IconButton>
                </div>
              </article>
            ))}
          </div>
        </section>
      </section>

      <aside className="eval-results">
        <div className="drawer-heading">
          <div>
            <strong>最近运行结果</strong>
            <small>{runs.length} 条历史</small>
          </div>
          <Activity size={17} />
        </div>
        <div className="eval-run-list">
          {!runs.length ? <EmptyLine text="还没有运行结果。" /> : null}
          {runs.map((run) => (
            <button
              className={`eval-run-card ${activeRun?.id === run.id ? "is-active" : ""}`}
              key={run.id}
              type="button"
              onClick={() => openRun(run.id)}
            >
              <span>
                <strong>{run.status}</strong>
                <small>{new Date(run.createdAt).toLocaleString()}</small>
              </span>
              <MetricPill label="src" value={percentMetric(run, "expected_source_hit_rate")} />
              <MetricPill label="kw" value={percentMetric(run, "keyword_hit_rate")} />
            </button>
          ))}
        </div>

        <section className="eval-run-detail">
          {activeRun ? (
            <>
              <header>
                <div>
                  <strong>{activeRun.status}</strong>
                  <small>{activeRun.evaluatorUserId}</small>
                </div>
                {activeRun.traceId ? (
                  <button className="secondary-button" type="button">
                    <Eye size={15} />
                    Trace
                  </button>
                ) : null}
              </header>
              <div className="eval-answer">{activeRun.answer || activeRun.error || "无答案"}</div>
              <div className="eval-metric-grid">
                <MetricCard label="source hit" value={percentMetric(activeRun, "expected_source_hit_rate")} />
                <MetricCard label="Hit@K" value={boolMetric(activeRun, "hit_at_k")} />
                <MetricCard label="MRR" value={decimalMetric(activeRun, "mrr")} />
                <MetricCard label="NDCG@K" value={percentMetric(activeRun, "ndcg_at_k")} />
                <MetricCard label="keyword hit" value={percentMetric(activeRun, "keyword_hit_rate")} />
                <MetricCard label="拒答准确" value={nullableBoolMetric(activeRun, "refusal_accuracy")} />
                <MetricCard label="引用支持率" value={percentMetric(activeRun, "citation_support_rate")} />
                <MetricCard label="latency" value={`${numberMetric(activeRun, "latency_ms")} ms`} />
                <MetricCard
                  label="ACL leak"
                  value={String(Boolean(activeRun.metrics.acl_leak_detected))}
                  danger={Boolean(activeRun.metrics.acl_leak_detected)}
                />
              </div>
              <TracePanel trace={activeRun.trace} />
            </>
          ) : (
            <div className="file-empty">
              <ClipboardList size={24} />
              <strong>选择一条运行结果</strong>
              <span>这里会展示答案、指标和关联 Trace。</span>
            </div>
          )}
        </section>
      </aside>
    </main>
  );
}

function TokenField({
  label,
  value,
  onChange,
}: {
  label: string;
  value: string[];
  onChange: (value: string[]) => void;
}) {
  return (
    <label className="field">
      {label}
      <input
        value={value.join(", ")}
        onChange={(event) => onChange(splitTokens(event.target.value))}
        placeholder="逗号分隔"
      />
    </label>
  );
}

function FileScopePicker({
  files,
  selectedFileIds,
  onChange,
}: {
  files: FileItem[];
  selectedFileIds: string[];
  onChange: (value: string[]) => void;
}) {
  const selected = new Set(selectedFileIds);
  return (
    <div className="eval-file-scope">
      <div className="section-heading">
        <div>
          <span>selected_file_ids</span>
          <small>{selectedFileIds.length ? `${selectedFileIds.length} 个文件` : "不指定则使用身份可见范围"}</small>
        </div>
      </div>
      <div className="eval-file-chips">
        {files.slice(0, 24).map((file) => (
          <button
            className={selected.has(file.id) ? "is-active" : ""}
            key={file.id}
            type="button"
            onClick={() => {
              onChange(
                selected.has(file.id)
                  ? selectedFileIds.filter((fileId) => fileId !== file.id)
                  : [...selectedFileIds, file.id],
              );
            }}
          >
            {file.name}
          </button>
        ))}
        {!files.length ? <EmptyLine text="当前用户没有可见文件。" /> : null}
      </div>
    </div>
  );
}

function MetricCard({ label, value, danger = false }: { label: string; value: string | number; danger?: boolean }) {
  return (
    <div className={`eval-metric-card ${danger ? "is-danger" : ""}`}>
      <small>{label}</small>
      <strong>{value}</strong>
    </div>
  );
}

function MetricPill({ label, value }: { label: string; value: string }) {
  return (
    <small className="eval-pill">
      {label} {value}
    </small>
  );
}

function EmptyLine({ text }: { text: string }) {
  return <div className="eval-empty-line">{text}</div>;
}

function splitTokens(value: string) {
  return value
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);
}

function numberMetric(run: RagEvalRun, key: string) {
  const value = run.metrics[key];
  return typeof value === "number" ? value : 0;
}

function percentMetric(run: RagEvalRun, key: string) {
  const value = run.metrics[key];
  if (typeof value !== "number") return "-";
  return `${Math.round(value * 100)}%`;
}

function decimalMetric(run: RagEvalRun, key: string) {
  const value = run.metrics[key];
  if (typeof value !== "number") return "-";
  return value.toFixed(2);
}

function boolMetric(run: RagEvalRun, key: string) {
  const value = run.metrics[key];
  if (typeof value !== "boolean") return "-";
  return value ? "true" : "false";
}

function nullableBoolMetric(run: RagEvalRun, key: string) {
  const value = run.metrics[key];
  if (value === null || value === undefined) return "-";
  return value === true ? "true" : "false";
}

function messageOf(error: unknown, fallback: string) {
  return error instanceof Error ? error.message : fallback;
}
