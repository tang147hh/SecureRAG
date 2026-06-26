import { useEffect, useMemo, useRef, useState } from "react";
import type { ChangeEvent, CSSProperties, PointerEvent as ReactPointerEvent } from "react";
import {
  Activity,
  ChevronLeft,
  ChevronRight,
  ClipboardList,
  Eye,
  FilePlus2,
  FileUp,
  FlaskConical,
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

const comparisonStrategies = [
  { id: "normal_query", label: "Normal Query" },
  { id: "rewrite", label: "Query Rewrite" },
  { id: "hyde", label: "HyDE" },
];

const comparisonTags = [
  "semantic",
  "exact_id",
  "permission",
  "temporal_policy",
  "no_answer",
];

export function EvaluationWorkspace({ files }: EvaluationWorkspaceProps) {
  const [datasets, setDatasets] = useState<RagEvalDataset[]>([]);
  const [activeDatasetId, setActiveDatasetId] = useState<string>();
  const [examples, setExamples] = useState<RagEvalExample[]>([]);
  const [runs, setRuns] = useState<RagEvalRun[]>([]);
  const [activeRun, setActiveRun] = useState<RagEvalRunDetail>();
  const [draft, setDraft] = useState<RagEvalExamplePayload>(emptyExample);
  const [batchSelectedFileIds, setBatchSelectedFileIds] = useState<string[]>([]);
  const [selectedStrategyIds, setSelectedStrategyIds] = useState<string[]>(
    comparisonStrategies.map((strategy) => strategy.id),
  );
  const [experimentTag, setExperimentTag] = useState("query-rewrite-hyde");
  const [workspaceMode, setWorkspaceMode] = useState<"examples" | "experiment">("examples");
  const [editingExampleId, setEditingExampleId] = useState<string>();
  const [loading, setLoading] = useState(true);
  const [runningId, setRunningId] = useState<string>();
  const [pollingRunIds, setPollingRunIds] = useState<string[]>([]);
  const [importing, setImporting] = useState(false);
  const [error, setError] = useState<string>();
  const importInputRef = useRef<HTMLInputElement>(null);
  const mainSplit = useVerticalSplit<HTMLDivElement>(42, 22, 68);
  const resultsSplit = useVerticalSplit<HTMLElement>(42, 22, 72);

  const activeDataset = useMemo(
    () => datasets.find((dataset) => dataset.id === activeDatasetId),
    [activeDatasetId, datasets],
  );

  const exampleById = useMemo(
    () => new Map(examples.map((example) => [example.id, example])),
    [examples],
  );

  const runSummary = useMemo(() => {
    const completed = runs.filter((run) => run.status === "completed");
    const latest = runs[0];
    const avgLatency =
      completed.reduce((total, run) => total + numberMetric(run, "latency_ms"), 0) /
      Math.max(completed.length, 1);
    const fallbackLeakCount = completed.filter((run) =>
      Boolean(run.metrics.acl_leak_detected),
    ).length;
    const leakCount = activeDataset?.permissionLeakCount ?? fallbackLeakCount;
    const leakTotal = activeDataset?.permissionLeakTotal ?? completed.length;
    const permissionLeakRate =
      activeDataset?.permissionLeakRate ??
      (leakTotal > 0 ? leakCount / leakTotal : null);
    return {
      completed: completed.length,
      latest,
      avgLatency,
      leakCount,
      leakTotal,
      permissionLeakRate,
    };
  }, [activeDataset, runs]);

  const comparison = useMemo(
    () => buildTagComparison(runs, exampleById),
    [exampleById, runs],
  );

  const resumeConclusion = useMemo(
    () => buildResumeConclusion(comparison),
    [comparison],
  );

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
      setBatchSelectedFileIds([]);
      return;
    }
    let mounted = true;
    Promise.all([
      apiClient.listEvalExamples(activeDatasetId),
      apiClient.listEvalRuns(activeDatasetId, undefined, 1000),
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

  useEffect(() => {
    if (!pollingRunIds.length) return;
    let cancelled = false;
    const timer = window.setInterval(() => {
      void pollRuns(pollingRunIds, () => cancelled);
    }, 1500);
    void pollRuns(pollingRunIds, () => cancelled);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [pollingRunIds]);

  const refreshDatasets = async () => {
    const next = await apiClient.listEvalDatasets();
    setDatasets(next);
    return next;
  };

  const refreshActive = async () => {
    if (!activeDatasetId) return;
    const [nextExamples, nextRuns] = await Promise.all([
      apiClient.listEvalExamples(activeDatasetId),
      apiClient.listEvalRuns(activeDatasetId, undefined, 1000),
      refreshDatasets(),
    ]);
    setExamples(nextExamples);
    setRuns(nextRuns);
  };

  const upsertRuns = (nextRuns: RagEvalRun[]) => {
    setRuns((current) => {
      const byId = new Map<string, RagEvalRun>();
      for (const run of [...current, ...nextRuns]) byId.set(run.id, run);
      return Array.from(byId.values()).sort(
        (left, right) => Date.parse(right.createdAt) - Date.parse(left.createdAt),
      );
    });
  };

  const trackRuns = (runIds: string[]) => {
    const nextIds = runIds.filter(Boolean);
    if (!nextIds.length) return;
    setPollingRunIds((current) => Array.from(new Set([...current, ...nextIds])));
  };

  const replaceTrackedRuns = (runIds: string[]) => {
    const nextIds = Array.from(new Set(runIds));
    setPollingRunIds((current) =>
      arraysEqual(current, nextIds) ? current : nextIds,
    );
  };

  const pollRuns = async (runIds: string[], isCancelled: () => boolean) => {
    const details = await Promise.allSettled(runIds.map((runId) => apiClient.getEvalRun(runId)));
    if (isCancelled()) return;

    const nextDetails = details
      .filter((result): result is PromiseFulfilledResult<RagEvalRunDetail> => result.status === "fulfilled")
      .map((result) => result.value);
    if (nextDetails.length) {
      upsertRuns(nextDetails);
      setActiveRun((current) => {
        if (!current) return nextDetails[0];
        return nextDetails.find((detail) => detail.id === current.id) ?? current;
      });
    }
    const stillRunning = nextDetails
      .filter((detail) => detail.status === "running")
      .map((detail) => detail.id);
    const missing = runIds.filter((runId) => !nextDetails.some((detail) => detail.id === runId));
    replaceTrackedRuns([...stillRunning, ...missing]);
    if (!stillRunning.length && !missing.length) {
      void refreshActive();
    }
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
      upsertRuns([result]);
      trackRuns([result.id]);
    } catch (err) {
      setError(messageOf(err, "运行样例失败。"));
    } finally {
      setRunningId(undefined);
    }
  };

  const compareExample = async (exampleId: string) => {
    setRunningId(`compare-${exampleId}`);
    setError(undefined);
    try {
      const nextRuns = await apiClient.compareEvalExample(exampleId, {
        strategies: selectedStrategyIds,
        experimentTag: experimentTag.trim() || null,
      });
      upsertRuns(nextRuns);
      if (nextRuns[0]) {
        const detail = await apiClient.getEvalRun(nextRuns[0].id);
        setActiveRun(detail);
      }
      trackRuns(nextRuns.map((run) => run.id));
    } catch (err) {
      setError(messageOf(err, "对比运行失败。"));
    } finally {
      setRunningId(undefined);
    }
  };

  const runDatasetExperiment = async () => {
    if (!activeDatasetId || !selectedStrategyIds.length) return;
    setRunningId(`experiment-${activeDatasetId}`);
    setError(undefined);
    try {
      const nextRuns = await apiClient.runEvalDataset(activeDatasetId, {
        selectedFileIds: batchSelectedFileIds.length ? batchSelectedFileIds : null,
        strategies: selectedStrategyIds,
        experimentTag: experimentTag.trim() || null,
      });
      upsertRuns(nextRuns);
      if (nextRuns[0]) {
        const detail = await apiClient.getEvalRun(nextRuns[0].id);
        setActiveRun(detail);
      }
      trackRuns(nextRuns.map((run) => run.id));
    } catch (err) {
      setError(messageOf(err, "批量实验失败。"));
    } finally {
      setRunningId(undefined);
    }
  };

  const runDataset = async () => {
    if (!activeDatasetId) return;
    setRunningId(activeDatasetId);
    setError(undefined);
    try {
      const nextRuns = await apiClient.runEvalDataset(activeDatasetId, {
        selectedFileIds: batchSelectedFileIds.length ? batchSelectedFileIds : null,
      });
      upsertRuns(nextRuns);
      if (nextRuns[0]) {
        const detail = await apiClient.getEvalRun(nextRuns[0].id);
        setActiveRun(detail);
      }
      trackRuns(nextRuns.map((run) => run.id));
    } catch (err) {
      setError(messageOf(err, "批量运行失败。"));
    } finally {
      setRunningId(undefined);
    }
  };

  const importExamples = async (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file || !activeDatasetId) return;
    setImporting(true);
    setError(undefined);
    try {
      const text = await file.text();
      const rows = parseCsv(text);
      const payloads = rows.map(csvRowToExample).filter((item) => item.question.trim());
      if (!payloads.length) {
        throw new Error("CSV 中没有可导入的问题。");
      }
      for (const payload of payloads) {
        await apiClient.createEvalExample(activeDatasetId, payload);
      }
      await refreshActive();
    } catch (err) {
      setError(messageOf(err, "导入 CSV 失败。"));
    } finally {
      setImporting(false);
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
            <input
              ref={importInputRef}
              type="file"
              accept=".csv,text/csv"
              hidden
              onChange={importExamples}
            />
            <button
              className="secondary-button"
              type="button"
              disabled={!activeDatasetId || importing}
              onClick={() => importInputRef.current?.click()}
            >
              {importing ? <Loader2 className="spin" size={15} /> : <FileUp size={15} />}
              导入 CSV
            </button>
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
          <MetricCard
            label="permission leak rate"
            value={
              runSummary.permissionLeakRate === null
                ? "N/A"
                : `${Math.round(runSummary.permissionLeakRate * 100)}% (${runSummary.leakCount}/${runSummary.leakTotal})`
            }
            danger={runSummary.leakCount > 0}
          />
        </section>

        <div className="eval-mode-switch" role="tablist" aria-label="评测工作区视图">
          <button
            className={workspaceMode === "examples" ? "is-active" : ""}
            type="button"
            role="tab"
            aria-selected={workspaceMode === "examples"}
            onClick={() => setWorkspaceMode("examples")}
          >
            <ClipboardList size={15} />
            样例管理
          </button>
          <button
            className={workspaceMode === "experiment" ? "is-active" : ""}
            type="button"
            role="tab"
            aria-selected={workspaceMode === "experiment"}
            onClick={() => setWorkspaceMode("experiment")}
          >
            <FlaskConical size={15} />
            策略实验
          </button>
        </div>

        {workspaceMode === "examples" ? (
          <div
            className="eval-main-stack"
            ref={mainSplit.containerRef}
            style={{ "--eval-top-panel": `${mainSplit.topPercent}%` } as CSSProperties}
          >
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

            <ResizeHandle label="调整问题列表高度" onPointerDown={mainSplit.startDrag} />

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
                      <IconButton label="三路对比" onClick={() => compareExample(example.id)}>
                        {runningId === `compare-${example.id}` ? (
                          <Loader2 className="spin" size={15} />
                        ) : (
                          <Activity size={15} />
                        )}
                      </IconButton>
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
          </div>
        ) : (
          <div className="eval-experiment-stack">
            <section className="eval-batch-options">
              <FileScopePicker
                files={files}
                label="批量运行文件范围"
                help={batchSelectedFileIds.length ? `${batchSelectedFileIds.length} 个文件` : "默认沿用每条样例的文件范围"}
                selectedFileIds={batchSelectedFileIds}
                onChange={setBatchSelectedFileIds}
              />
              <ExperimentControls
                experimentTag={experimentTag}
                selectedStrategyIds={selectedStrategyIds}
                running={runningId === `experiment-${activeDatasetId}`}
                disabled={!activeDatasetId || !examples.length}
                onExperimentTagChange={setExperimentTag}
                onSelectedStrategyIdsChange={setSelectedStrategyIds}
                onRun={runDatasetExperiment}
              />
            </section>
            <section className="eval-comparison-panel">
              <div className="section-heading">
                <div>
                  <span>策略对比</span>
                  <small>按 tag 聚合最近 {runs.length} 条运行结果</small>
                </div>
              </div>
              <ComparisonTable comparison={comparison} />
              <div className="eval-conclusion">
                <strong>简历结论</strong>
                <span>{resumeConclusion}</span>
              </div>
            </section>
          </div>
        )}
      </section>

      <aside
        className="eval-results"
        ref={resultsSplit.containerRef}
        style={{ "--eval-top-panel": `${resultsSplit.topPercent}%` } as CSSProperties}
      >
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
                <strong>{run.question}</strong>
                <small>
                  {run.status} · {formatChinaDateTime(run.createdAt)}
                  {variantLabel(run) ? ` · ${variantLabel(run)}` : ""}
                </small>
              </span>
              <MetricPill label="src" value={percentMetric(run, "expected_source_hit_rate")} />
              <MetricPill label="kw" value={percentMetric(run, "keyword_hit_rate")} />
            </button>
          ))}
        </div>

        <ResizeHandle label="调整运行详情高度" onPointerDown={resultsSplit.startDrag} />

        <section className="eval-run-detail">
          {activeRun ? (
            <>
              <header>
                <div>
                  <strong>{activeRun.question}</strong>
                  <small>
                    {activeRun.status} · {activeRun.evaluatorUserId}
                  </small>
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
                <MetricCard label="ragas faith" value={decimalMetric(activeRun, "ragas_faithfulness")} />
                <MetricCard label="ragas relevancy" value={decimalMetric(activeRun, "ragas_answer_relevancy")} />
                <MetricCard label="ragas ctx precision" value={decimalMetric(activeRun, "ragas_context_precision")} />
                <MetricCard label="ragas ctx recall" value={decimalMetric(activeRun, "ragas_context_recall")} />
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

function useVerticalSplit<TElement extends HTMLElement>(
  initialTopPercent: number,
  minTopPercent: number,
  maxTopPercent: number,
) {
  const containerRef = useRef<TElement | null>(null);
  const [topPercent, setTopPercent] = useState(initialTopPercent);

  const startDrag = (event: ReactPointerEvent<HTMLButtonElement>) => {
    const container = containerRef.current;
    if (!container) return;
    event.preventDefault();
    event.currentTarget.setPointerCapture(event.pointerId);

    const bounds = container.getBoundingClientRect();
    const updateSplit = (clientY: number) => {
      const nextPercent = ((clientY - bounds.top) / bounds.height) * 100;
      setTopPercent(Math.min(maxTopPercent, Math.max(minTopPercent, nextPercent)));
    };
    const handlePointerMove = (moveEvent: PointerEvent) => updateSplit(moveEvent.clientY);
    const handlePointerUp = () => {
      document.removeEventListener("pointermove", handlePointerMove);
      document.removeEventListener("pointerup", handlePointerUp);
    };

    updateSplit(event.clientY);
    document.addEventListener("pointermove", handlePointerMove);
    document.addEventListener("pointerup", handlePointerUp, { once: true });
  };

  return { containerRef, topPercent, startDrag };
}

function ResizeHandle({
  label,
  onPointerDown,
}: {
  label: string;
  onPointerDown: (event: ReactPointerEvent<HTMLButtonElement>) => void;
}) {
  return (
    <button className="eval-resize-handle" type="button" aria-label={label} onPointerDown={onPointerDown}>
      <span />
    </button>
  );
}

function ExperimentControls({
  experimentTag,
  selectedStrategyIds,
  running,
  disabled,
  onExperimentTagChange,
  onSelectedStrategyIdsChange,
  onRun,
}: {
  experimentTag: string;
  selectedStrategyIds: string[];
  running: boolean;
  disabled: boolean;
  onExperimentTagChange: (value: string) => void;
  onSelectedStrategyIdsChange: (value: string[]) => void;
  onRun: () => void;
}) {
  const selected = new Set(selectedStrategyIds);
  return (
    <div className="eval-experiment-controls">
      <label className="field eval-experiment-tag">
        experiment_tag
        <input
          value={experimentTag}
          onChange={(event) => onExperimentTagChange(event.target.value)}
          placeholder="例如 baseline-vs-hybrid"
        />
      </label>
      <div className="eval-strategy-picker">
        {comparisonStrategies.map((strategy) => (
          <button
            className={selected.has(strategy.id) ? "is-active" : ""}
            key={strategy.id}
            type="button"
            onClick={() => {
              onSelectedStrategyIdsChange(
                selected.has(strategy.id)
                  ? selectedStrategyIds.filter((strategyId) => strategyId !== strategy.id)
                  : [...selectedStrategyIds, strategy.id],
              );
            }}
          >
            {strategy.label}
          </button>
        ))}
      </div>
      <button
        className="primary-button eval-experiment-run"
        type="button"
        disabled={disabled || running || !selectedStrategyIds.length}
        onClick={onRun}
      >
        {running ? <Loader2 className="spin" size={15} /> : <FlaskConical size={15} />}
        批量实验
      </button>
    </div>
  );
}

interface TagComparisonRow {
  tag: string;
  strategy: string;
  label: string;
  count: number;
  primary: number | null;
  primaryDelta: number | null;
  sourceHit: number | null;
  keywordHit: number | null;
  refusalAccuracy: number | null;
  mrr: number | null;
  ndcgAtK: number | null;
  citationSupport: number | null;
  aclLeakRate: number | null;
  latencyMs: number | null;
}

function ComparisonTable({ comparison }: { comparison: TagComparisonRow[] }) {
  const tableRef = useRef<HTMLDivElement | null>(null);

  const scrollTable = (direction: -1 | 1) => {
    const table = tableRef.current;
    if (!table) return;
    table.scrollBy({
      left: direction * Math.max(table.clientWidth * 0.72, 360),
      behavior: "smooth",
    });
  };

  if (!comparison.length) {
    return <EmptyLine text="完成一次批量实验后，这里会按 tag 展示策略差异。" />;
  }
  return (
    <div className="eval-comparison-wrap">
      <div className="eval-table-scroll-actions">
        <IconButton label="向左滚动指标表" onClick={() => scrollTable(-1)}>
          <ChevronLeft size={16} />
        </IconButton>
        <IconButton label="向右滚动指标表" onClick={() => scrollTable(1)}>
          <ChevronRight size={16} />
        </IconButton>
      </div>
      <div className="eval-comparison-table" ref={tableRef}>
        <div className="eval-comparison-head">
          <span>tag</span>
          <span>strategy</span>
          <span>n</span>
          <span>主指标</span>
          <span>Δ vs Vector</span>
          <span>source</span>
          <span>keyword</span>
          <span>refusal</span>
          <span>MRR</span>
          <span>NDCG</span>
          <span>citation</span>
          <span>ACL leak</span>
          <span>latency</span>
        </div>
        {comparison.map((row) => (
          <div className="eval-comparison-row" key={`${row.tag}-${row.strategy}`}>
            <strong>{row.tag}</strong>
            <span>{row.label}</span>
            <span>{row.count}</span>
            <span>{formatMetricRatio(row.primary)}</span>
            <span className={deltaClass(row.primaryDelta)}>{formatDelta(row.primaryDelta)}</span>
            <span>{formatMetricRatio(row.sourceHit)}</span>
            <span>{formatMetricRatio(row.keywordHit)}</span>
            <span>{formatMetricRatio(row.refusalAccuracy)}</span>
            <span>{formatDecimal(row.mrr)}</span>
            <span>{formatMetricRatio(row.ndcgAtK)}</span>
            <span>{formatMetricRatio(row.citationSupport)}</span>
            <span>{formatMetricRatio(row.aclLeakRate)}</span>
            <span>{formatLatency(row.latencyMs)}</span>
          </div>
        ))}
      </div>
    </div>
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
  label = "selected_file_ids",
  help,
  selectedFileIds,
  onChange,
}: {
  files: FileItem[];
  label?: string;
  help?: string;
  selectedFileIds: string[];
  onChange: (value: string[]) => void;
}) {
  const selected = new Set(selectedFileIds);
  return (
    <div className="eval-file-scope">
      <div className="section-heading">
        <div>
          <span>{label}</span>
          <small>{help ?? (selectedFileIds.length ? `${selectedFileIds.length} 个文件` : "不指定则使用身份可见范围")}</small>
        </div>
        {selectedFileIds.length ? (
          <button className="secondary-button eval-scope-clear" type="button" onClick={() => onChange([])}>
            清空
          </button>
        ) : null}
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

function parseCsv(text: string) {
  const rows: string[][] = [];
  let row: string[] = [];
  let cell = "";
  let inQuotes = false;

  for (let index = 0; index < text.length; index += 1) {
    const char = text[index];
    const next = text[index + 1];
    if (char === '"' && inQuotes && next === '"') {
      cell += '"';
      index += 1;
    } else if (char === '"') {
      inQuotes = !inQuotes;
    } else if (char === "," && !inQuotes) {
      row.push(cell);
      cell = "";
    } else if ((char === "\n" || char === "\r") && !inQuotes) {
      if (char === "\r" && next === "\n") index += 1;
      row.push(cell);
      if (row.some((value) => value.trim())) rows.push(row);
      row = [];
      cell = "";
    } else {
      cell += char;
    }
  }

  row.push(cell);
  if (row.some((value) => value.trim())) rows.push(row);
  const [headers = [], ...records] = rows;
  return records.map((record) => {
    const item: Record<string, string> = {};
    headers.forEach((header, index) => {
      item[header.trim()] = (record[index] ?? "").trim();
    });
    return item;
  });
}

function csvRowToExample(row: Record<string, string>): RagEvalExamplePayload {
  return {
    question: row.question ?? "",
    expectedAnswer: row.expected_answer ?? row.expectedAnswer ?? "",
    expectedSourceIds: splitTokens(row.expected_source_ids ?? row.expectedSourceIds ?? ""),
    expectedKeywords: splitTokens(row.expected_keywords ?? row.expectedKeywords ?? ""),
    evaluatorUserId: row.evaluator_user_id ?? row.evaluatorUserId ?? "",
    selectedFileIds: splitTokens(row.selected_file_ids ?? row.selectedFileIds ?? ""),
    tags: splitTokens(row.tags ?? ""),
  };
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

function buildTagComparison(
  runs: RagEvalRun[],
  exampleById: Map<string, RagEvalExample>,
): TagComparisonRow[] {
  const aggregates = new Map<
    string,
    {
      tag: string;
      strategy: string;
      label: string;
      primary: number[];
      sourceHit: number[];
      keywordHit: number[];
      refusalAccuracy: number[];
      mrr: number[];
      ndcgAtK: number[];
      citationSupport: number[];
      aclLeakRate: number[];
      latencyMs: number[];
    }
  >();

  for (const run of runs) {
    if (run.status !== "completed" || !run.exampleId) continue;
    const example = exampleById.get(run.exampleId);
    if (!example) continue;
    const tags = new Set(example.tags.map((tag) => tag.toLowerCase()));
    const strategy = variantLabel(run) || "current";
    const label = strategyLabel(run, strategy);
    for (const tag of comparisonTags) {
      if (!tags.has(tag)) continue;
      const key = `${tag}::${strategy}`;
      const item =
        aggregates.get(key) ??
        {
          tag,
          strategy,
          label,
          primary: [],
          sourceHit: [],
          keywordHit: [],
          refusalAccuracy: [],
          mrr: [],
          ndcgAtK: [],
          citationSupport: [],
          aclLeakRate: [],
          latencyMs: [],
        };
      pushNumber(item.primary, primaryMetricForTag(run, tag));
      pushNumber(item.sourceHit, metricNumber(run, "expected_source_hit_rate"));
      pushNumber(item.keywordHit, metricNumber(run, "keyword_hit_rate"));
      pushNumber(item.refusalAccuracy, boolAsNumber(run.metrics.refusal_accuracy));
      pushNumber(item.mrr, metricNumber(run, "mrr"));
      pushNumber(item.ndcgAtK, metricNumber(run, "ndcg_at_k"));
      pushNumber(item.citationSupport, metricNumber(run, "citation_support_rate"));
      pushNumber(item.aclLeakRate, boolAsNumber(run.metrics.acl_leak_detected));
      pushNumber(item.latencyMs, metricNumber(run, "latency_ms"));
      aggregates.set(key, item);
    }
  }

  const rows = Array.from(aggregates.values()).map((item) => ({
    tag: item.tag,
    strategy: item.strategy,
    label: item.label,
    count: item.primary.length,
    primary: average(item.primary),
    primaryDelta: null as number | null,
    sourceHit: average(item.sourceHit),
    keywordHit: average(item.keywordHit),
    refusalAccuracy: average(item.refusalAccuracy),
    mrr: average(item.mrr),
    ndcgAtK: average(item.ndcgAtK),
    citationSupport: average(item.citationSupport),
    aclLeakRate: average(item.aclLeakRate),
    latencyMs: average(item.latencyMs),
  }));
  const baselineByTag = new Map(
    rows
      .filter((row) => row.strategy === "vector")
      .map((row) => [row.tag, row.primary] as const),
  );
  for (const row of rows) {
    const baseline = baselineByTag.get(row.tag);
    row.primaryDelta =
      typeof row.primary === "number" && typeof baseline === "number"
        ? row.primary - baseline
        : null;
  }
  const strategyOrder = new Map(comparisonStrategies.map((strategy, index) => [strategy.id, index]));
  return rows.sort(
    (left, right) =>
      comparisonTags.indexOf(left.tag) - comparisonTags.indexOf(right.tag) ||
      (strategyOrder.get(left.strategy) ?? 99) - (strategyOrder.get(right.strategy) ?? 99),
  );
}

function primaryMetricForTag(run: RagEvalRun, tag: string) {
  if (tag === "semantic") {
    return metricNumber(run, "ragas_answer_relevancy") ?? metricNumber(run, "keyword_hit_rate");
  }
  if (tag === "exact_id") return metricNumber(run, "expected_source_hit_rate");
  if (tag === "permission") {
    const leak = boolAsNumber(run.metrics.acl_leak_detected);
    return typeof leak === "number" ? 1 - leak : null;
  }
  if (tag === "temporal_policy") {
    return metricNumber(run, "keyword_hit_rate") ?? metricNumber(run, "expected_source_hit_rate");
  }
  if (tag === "no_answer") return boolAsNumber(run.metrics.refusal_accuracy);
  return metricNumber(run, "keyword_hit_rate");
}

function buildResumeConclusion(rows: TagComparisonRow[]) {
  const exact = rowFor(rows, "exact_id", "hybrid_rrf");
  const exactBase = rowFor(rows, "exact_id", "vector");
  const permission = rowFor(rows, "permission", "hybrid_rrf");
  const permissionBase = rowFor(rows, "permission", "vector");
  const exactDelta = diffPoints(exact?.primary, exactBase?.primary);
  const permissionDelta = diffPoints(permission?.primary, permissionBase?.primary);
  if (exactDelta !== null || permissionDelta !== null) {
    const bits = [];
    if (exactDelta !== null) bits.push(`exact_id 主指标提升 ${formatPointDelta(exactDelta)}`);
    if (permissionDelta !== null) bits.push(`permission 安全通过率提升 ${formatPointDelta(permissionDelta)}`);
    return `Hybrid+RRF 相比 Vector 在 ${bits.join("，")}；该结论来自按 tag 分层的 RAG 回归实验。`;
  }
  return "运行包含 Vector 与 Hybrid+RRF 的批量实验后，将自动生成可写进简历的量化结论。";
}

function rowFor(rows: TagComparisonRow[], tag: string, strategy: string) {
  return rows.find((row) => row.tag === tag && row.strategy === strategy);
}

function pushNumber(values: number[], value: number | null) {
  if (typeof value === "number" && Number.isFinite(value)) values.push(value);
}

function average(values: number[]) {
  if (!values.length) return null;
  return values.reduce((total, value) => total + value, 0) / values.length;
}

function metricNumber(run: RagEvalRun, key: string) {
  const value = run.metrics[key];
  return typeof value === "number" ? value : null;
}

function boolAsNumber(value: unknown) {
  if (typeof value === "boolean") return value ? 1 : 0;
  return null;
}

function diffPoints(current?: number | null, baseline?: number | null) {
  if (typeof current !== "number" || typeof baseline !== "number") return null;
  return Math.round((current - baseline) * 100);
}

function formatPointDelta(points: number) {
  return `${points >= 0 ? "+" : ""}${points} 个百分点`;
}

function formatMetricRatio(value: number | null) {
  if (typeof value !== "number") return "-";
  return `${Math.round(value * 100)}%`;
}

function formatDecimal(value: number | null) {
  if (typeof value !== "number") return "-";
  return value.toFixed(2);
}

function formatLatency(value: number | null) {
  if (typeof value !== "number") return "-";
  return `${Math.round(value)}ms`;
}

function formatDelta(value: number | null) {
  if (typeof value !== "number") return "-";
  const points = Math.round(value * 100);
  if (points === 0) return "same";
  return `${points > 0 ? "+" : ""}${points}pp`;
}

function deltaClass(value: number | null) {
  if (typeof value !== "number" || Math.abs(value) < 0.005) return "";
  return value > 0 ? "is-positive" : "is-negative";
}

function variantLabel(run: RagEvalRun) {
  const fromMetrics = run.metrics.evaluation_variant;
  if (typeof fromMetrics === "string") return fromMetrics;
  const snapshot =
    "settingsSnapshot" in run &&
    run.settingsSnapshot &&
    typeof run.settingsSnapshot === "object"
      ? (run.settingsSnapshot as Record<string, unknown>)
      : undefined;
  const fromSettings = snapshot?.evaluation_variant;
  return typeof fromSettings === "string" ? fromSettings : "";
}

function strategyLabel(run: RagEvalRun, strategy: string) {
  const fromMetrics = run.metrics.strategy_label;
  if (typeof fromMetrics === "string") return fromMetrics;
  const snapshot =
    "settingsSnapshot" in run &&
    run.settingsSnapshot &&
    typeof run.settingsSnapshot === "object"
      ? (run.settingsSnapshot as Record<string, unknown>)
      : undefined;
  const fromSettings = snapshot?.strategy_label;
  if (typeof fromSettings === "string") return fromSettings;
  return comparisonStrategies.find((item) => item.id === strategy)?.label ?? strategy;
}

function formatChinaDateTime(value: string) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("zh-CN", {
    timeZone: "Asia/Shanghai",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).format(date);
}

function arraysEqual(left: string[], right: string[]) {
  return left.length === right.length && left.every((value, index) => value === right[index]);
}

function messageOf(error: unknown, fallback: string) {
  return error instanceof Error ? error.message : fallback;
}
