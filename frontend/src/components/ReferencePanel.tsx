import { useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { Expand, FileText, Layers3, Network, X } from "lucide-react";
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
      <GraphRelationMap entities={entities} relationships={relationships} paths={paths} />
      <GraphEvidenceSection title="关系明细" items={relationships} />
      <GraphEvidenceSection title="答案片段" items={fragments} />
      {!citations.length ? (
        <p className="graph-reference__empty">暂无图谱证据。</p>
      ) : null}
    </div>
  );
}

interface GraphNode {
  id: string;
  label: string;
  type: string;
  x: number;
  y: number;
}

interface GraphEdge {
  id: string;
  source: string;
  target: string;
  label: string;
  evidence: string;
}

function GraphRelationMap({
  entities,
  relationships,
  paths,
}: {
  entities: Citation[];
  relationships: Citation[];
  paths: Citation[];
}) {
  const [expanded, setExpanded] = useState(false);
  const graph = buildGraphModel(entities, relationships);
  useEffect(() => {
    if (!expanded) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") setExpanded(false);
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [expanded]);

  if (!graph.nodes.length && !paths.length) {
    return null;
  }

  return (
    <section className="graph-map" aria-label="GraphRAG 关系图">
      <div className="graph-map__head">
        <div>
          <span>关系图</span>
          <small>{graph.nodes.length} 个节点 / {graph.edges.length} 条关系</small>
        </div>
        <button
          className="graph-map__expand"
          type="button"
          onClick={() => setExpanded(true)}
          aria-label="放大关系图"
          title="放大关系图"
        >
          <Expand size={15} />
        </button>
      </div>
      <button className="graph-map__preview" type="button" onClick={() => setExpanded(true)}>
        <GraphSvg graph={graph} />
      </button>
      {paths.length ? (
        <div className="graph-map__paths">
          {paths.slice(0, 3).map((path) => (
            <span key={path.id}>{path.title.replace("路径：", "")}</span>
          ))}
        </div>
      ) : null}
      {expanded
        ? createPortal(
            <GraphModal graph={graph} onClose={() => setExpanded(false)} />,
            document.body,
          )
        : null}
    </section>
  );
}

function GraphModal({
  graph,
  onClose,
}: {
  graph: ReturnType<typeof buildGraphModel>;
  onClose: () => void;
}) {
  return (
    <div className="graph-modal" role="dialog" aria-modal="true" aria-label="放大关系图">
      <button
        className="graph-modal__backdrop"
        type="button"
        aria-label="关闭放大关系图"
        onClick={onClose}
      />
      <div className="graph-modal__panel">
        <div className="graph-modal__head">
          <div>
            <strong>GraphRAG 关系图</strong>
            <small>{graph.nodes.length} 个节点 / {graph.edges.length} 条关系</small>
          </div>
          <IconButton label="关闭放大关系图" onClick={onClose}>
            <X size={17} />
          </IconButton>
        </div>
        <div className="graph-modal__canvas">
          <GraphSvg graph={graph} enlarged />
        </div>
      </div>
    </div>
  );
}

function GraphSvg({
  graph,
  enlarged = false,
}: {
  graph: ReturnType<typeof buildGraphModel>;
  enlarged?: boolean;
}) {
  return (
    <svg
      className={`graph-map__canvas ${enlarged ? "graph-map__canvas--large" : ""}`}
      viewBox="0 0 720 430"
      role="img"
    >
      <defs>
        <marker
          id={enlarged ? "graph-arrow-large" : "graph-arrow"}
          markerHeight="8"
          markerWidth="8"
          orient="auto"
          refX="7"
          refY="4"
        >
          <path d="M0,0 L8,4 L0,8 Z" />
        </marker>
      </defs>
      <rect className="graph-map__backdrop" x="1" y="1" width="718" height="428" rx="12" />
      {graph.edges.map((edge) => {
        const source = graph.nodeById.get(edge.source);
        const target = graph.nodeById.get(edge.target);
        if (!source || !target) return null;
        const midX = (source.x + target.x) / 2;
        const midY = (source.y + target.y) / 2;
        return (
          <g className="graph-map__edge" key={edge.id}>
            <line
              className={enlarged ? "graph-map__line--large" : undefined}
              x1={source.x}
              y1={source.y}
              x2={target.x}
              y2={target.y}
            />
            <text x={midX} y={midY - 8}>
              {edge.label}
            </text>
            <title>{edge.evidence}</title>
          </g>
        );
      })}
      {graph.nodes.map((node) => (
        <g className={`graph-map__node graph-map__node--${node.type}`} key={node.id}>
          <circle cx={node.x} cy={node.y} r={enlarged ? "34" : "29"} />
          <text x={node.x} y={node.y + 4}>
            {enlarged ? node.label : compactNodeLabel(node.label)}
          </text>
          <title>{node.label}</title>
        </g>
      ))}
    </svg>
  );
}

function buildGraphModel(entities: Citation[], relationships: Citation[]) {
  const entityType = new Map<string, string>();
  entities.forEach((entity) => {
    const label = entity.title.replace("实体：", "").trim();
    if (label) entityType.set(label, inferEntityType(label, entity.excerpt));
  });

  const edges: GraphEdge[] = [];
  relationships.forEach((relationship, index) => {
    const match = relationship.title.match(/^关系：(.+?)\s*->\s*(.+)$/);
    if (!match) return;
    const source = match[1].trim();
    const target = match[2].trim();
    if (!source || !target) return;
    edges.push({
      id: relationship.id || `edge-${index}`,
      source,
      target,
      label: relationLabel(relationship.excerpt),
      evidence: relationship.excerpt,
    });
    if (!entityType.has(source)) entityType.set(source, inferEntityType(source, relationship.excerpt));
    if (!entityType.has(target)) entityType.set(target, inferEntityType(target, relationship.excerpt));
  });

  const labels = Array.from(entityType.keys()).slice(0, 12);
  const centerLabel = labels.find((label) => /^BX-|^HT-|^PAY-|^PERM-|^OPS-/.test(label)) ?? labels[0];
  const outerLabels = labels.filter((label) => label !== centerLabel);
  const nodes: GraphNode[] = [];
  if (centerLabel) {
    nodes.push({ id: centerLabel, label: centerLabel, type: entityType.get(centerLabel) ?? "entity", x: 360, y: 215 });
  }
  outerLabels.forEach((label, index) => {
    const angle = -Math.PI / 2 + (index * Math.PI * 2) / Math.max(outerLabels.length, 1);
    const rx = 245;
    const ry = 138;
    nodes.push({
      id: label,
      label,
      type: entityType.get(label) ?? "entity",
      x: 360 + Math.cos(angle) * rx,
      y: 215 + Math.sin(angle) * ry,
    });
  });

  return {
    nodes,
    edges: edges.filter((edge) => labels.includes(edge.source) && labels.includes(edge.target)).slice(0, 14),
    nodeById: new Map(nodes.map((node) => [node.id, node])),
  };
}

function inferEntityType(label: string, evidence: string) {
  if (/元$/.test(label)) return "amount";
  if (/^BX-|^HT-|^PAY-|^PERM-|^OPS-/.test(label)) return "id";
  if (/退回|完成|审批|关闭|执行|回滚/.test(label)) return "status";
  if (/原因|缺少|说明/.test(label) || /退回原因/.test(evidence)) return "reason";
  return "entity";
}

function relationLabel(text: string) {
  if (/金额/.test(text)) return "金额";
  if (/退回原因|原因/.test(text)) return "退回原因";
  if (/状态/.test(text)) return "状态";
  if (/关联|同一证据/.test(text)) return "关联";
  return "关系";
}

function compactNodeLabel(label: string) {
  if (label.length <= 12) return label;
  return `${label.slice(0, 10)}...`;
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
