import { ExternalLink } from "lucide-react";
import type { Citation } from "../api/types";

interface CitationCardProps {
  citation: Citation;
  index: number;
}

export function CitationCard({ citation, index }: CitationCardProps) {
  const parts = citation.excerpt.split(citation.highlight);

  return (
    <article className="citation-card">
      <div className="citation-card__head">
        <span>#{index + 1}</span>
        <strong>{citation.title}</strong>
        <ExternalLink size={14} />
      </div>
      <p>
        {parts.length > 1 ? (
          <>
            {parts[0]}
            <mark>{citation.highlight}</mark>
            {parts.slice(1).join(citation.highlight)}
          </>
        ) : (
          citation.excerpt
        )}
      </p>
      <footer>
        <span>{citation.page ? `Page ${citation.page}` : "Snippet"}</span>
        <span>{citation.score > 0 ? `${Math.round(citation.score * 100)}% match` : "retrieved"}</span>
      </footer>
    </article>
  );
}
