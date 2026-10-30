import { diffWordsWithSpace } from "diff";

import type { Change } from "../api/client";

export function EditDiff({ change }: { change: Change }) {
  const parts = diffWordsWithSpace(change.before, change.after);
  return (
    <article className="border-t border-rule py-4">
      <pre className="overflow-x-auto bg-wash p-3 font-mono text-xs leading-relaxed break-words whitespace-pre-wrap">
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
    </article>
  );
}
