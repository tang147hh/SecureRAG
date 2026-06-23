import { FileSearch, Sparkles } from "lucide-react";

export function EmptyState() {
  return (
    <div className="empty-state">
      <div className="empty-state__icon">
        <FileSearch size={28} />
      </div>
      <h1>开始一次有证据链的企业问答</h1>
      <p>提问后，SecureRAG 会从知识库检索相关片段，并把引用、评分和诊断信息展示在右侧。</p>
      <div className="suggestion-row">
        <button type="button">
          <Sparkles size={15} />
          总结最新合规风险
        </button>
        <button type="button">@WebSearch 追踪外部资料</button>
        <button type="button">@Enterprise RAG Security Baseline.pdf</button>
      </div>
    </div>
  );
}
