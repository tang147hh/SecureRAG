import { FileText, Layers3, Network, X } from "lucide-react";
import type { Citation, ReferenceDocument } from "../api/types";
import { CitationCard } from "./CitationCard";
import { IconButton } from "./IconButton";

interface ReferencePanelProps {
  references: ReferenceDocument[];
  activeDocumentId?: string;
  isOpen: boolean;
  onClose: () => void;
  onSelectDocument: (documentId: string) => void;
}

export function ReferencePanel({
  references,
  activeDocumentId,
  isOpen,
  onClose,
  onSelectDocument,
}: ReferencePanelProps) {
  const activeDocument =
    references.find((document) => document.id === activeDocumentId) ?? references[0];
  const allCitations = activeDocument?.citations ?? references.flatMap((document) => document.citations);
  const isGraphReference = activeDocument?.source === "GraphRAG";

  return (
    <aside
      className={`reference-panel app-drawer app-drawer--right ${isOpen ? "is-open" : ""}`}
      style={isOpen ? { left: "auto", right: "clamp(0.6rem, 1.4vw, 1.25rem)" } : undefined}
    >
      <div className="drawer-heading">
        <div>
          <strong>知识引用</strong>
          <small>Evidence & diagnostics</small>
        </div>
        <IconButton className="tablet-only" label="关闭引用面板" onClick={onClose}>
          <X size={17} />
        </IconButton>
      </div>

      <div className="reference-panel__body">
        <section className="reference-section reference-section--summary">
          <label className="field">
            <span>当前引用文档</span>
            <select
              value={activeDocument?.id ?? ""}
              onChange={(event) => onSelectDocument(event.target.value)}
            >
              {references.map((document) => (
                <option key={document.id} value={document.id}>
                  {document.title}
                </option>
              ))}
            </select>
          </label>
          {activeDocument ? (
            <div className="reference-summary">
              <div className="reference-summary__icon">
                <FileText size={18} />
              </div>
              <div>
                <strong>{activeDocument.source}</strong>
                <p>{activeDocument.summary}</p>
              </div>
            </div>
          ) : null}
        </section>

        <section className="reference-section reference-section--grow">
          <div className="section-heading">
            <div>
              <span>引用片段</span>
              <small>{allCitations.length} 个证据块</small>
            </div>
            <Layers3 size={16} />
          </div>
          <div className="citation-list">
            {isGraphReference ? (
              <GraphEvidenceList citations={allCitations} />
            ) : (
              allCitations.map((citation, index) => (
                <CitationCard key={citation.id} citation={citation} index={index} />
              ))
            )}
          </div>
        </section>
      </div>
    </aside>
  );
}

function GraphEvidenceList({ citations }: { citations: Citation[] }) {
  const entities = citations.filter((citation) => citation.id.startsWith("graph-entity"));
  const relationships = citations.filter((citation) =>
    citation.id.startsWith("graph-relation"),
  );
  const paths = citations.filter((citation) => citation.id.startsWith("graph-path"));
  const fragments = citations.filter((citation) =>
    citation.id.startsWith("graph-fragment"),
  );

  return (
    <div className="graph-reference">
      <GraphEvidenceSection title="实体" items={entities} />
      <GraphEvidenceSection title="关系" items={relationships} />
      <GraphEvidenceSection title="路径" items={paths} emphasize />
      <GraphEvidenceSection title="答案片段" items={fragments} />
      {!citations.length ? (
        <p className="graph-reference__empty">暂无图谱证据。</p>
      ) : null}
    </div>
  );
}

function GraphEvidenceSection({
  title,
  items,
  emphasize = false,
}: {
  title: string;
  items: Citation[];
  emphasize?: boolean;
}) {
  if (!items.length) return null;
  return (
    <section className="graph-reference__section">
      <div className="graph-reference__title">
        <Network size={14} />
        <span>{title}</span>
        <small>{items.length}</small>
      </div>
      <div className="graph-reference__items">
        {items.map((item) => (
          <article
            className={`graph-reference__item ${emphasize ? "graph-reference__item--path" : ""}`}
            key={item.id}
          >
            <strong>{item.title.replace(`${title}：`, "")}</strong>
            <p>{item.excerpt}</p>
          </article>
        ))}
      </div>
    </section>
  );
}
