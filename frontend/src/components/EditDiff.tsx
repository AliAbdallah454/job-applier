import { diffWordsWithSpace } from "diff";

import type { Change } from "../api/client";

export function EditDiff({ change }: { change: Change }) {
  const parts = diffWordsWithSpace(change.find, change.replace);
  return (
    <article className="border-t border-rule py-4">
      <div className="flex items-baseline justify-between gap-4">
        <p className="text-sm font-medium">{change.reason}</p>
        <span className="shrink-0 font-mono text-[11px] text-muted">
          {change.applied ? "applied" : "skipped"}
        </span>
      </div>
      <pre className="mt-2 overflow-x-auto bg-wash p-3 font-mono text-xs leading-relaxed break-words whitespace-pre-wrap">
        {parts.map((part, index) => {
          if (part.added) {
            return (
              <ins key={index} className="bg-ink text-paper no-underline">
                {part.value}
              </ins>
            );
          }
          if (part.removed) {
            return (
              <del key={index} className="text-muted">
                {part.value}
              </del>
            );
          }
          return <span key={index}>{part.value}</span>;
        })}
      </pre>
      {change.error && <p className="mt-2 text-xs text-muted">Not applied: {change.error}</p>}
    </article>
  );
}
