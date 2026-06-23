import { FileText, Layers3, X } from "lucide-react";
import type { ReferenceDocument } from "../api/types";
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
            {allCitations.map((citation, index) => (
              <CitationCard key={citation.id} citation={citation} index={index} />
            ))}
          </div>
        </section>
      </div>
    </aside>
  );
}
