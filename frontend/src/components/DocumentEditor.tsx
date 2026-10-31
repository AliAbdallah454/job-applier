import { useEffect, useRef, useState } from "react";

import { ApiError, type Document, type GenerationDetail } from "../api/client";
import { useEditDocument, useSource } from "../api/queries";
import { LatexCode } from "./LatexCode";
import { refineLine } from "./sourceLine";
import { Button, ErrorNote, Skeleton } from "./ui";

// Both layers need the same box and wrapping, or the colours drift off the letters.
const SOURCE_TEXT =
  "absolute inset-0 size-full p-3 font-mono text-xs leading-relaxed break-words whitespace-pre-wrap [scrollbar-gutter:stable]";

/** Selects a 1-based line and scrolls to it; long lines wrap, so it measures rather than counts. */
function selectLine(textarea: HTMLTextAreaElement, line: number) {
  const lines = textarea.value.split("\n");
  const index = Math.min(Math.max(line, 1), lines.length) - 1;
  const start = lines.slice(0, index).reduce((total, row) => total + row.length + 1, 0);
  textarea.focus();
  textarea.setSelectionRange(start, start + lines[index].length);

  const style = getComputedStyle(textarea);
  const mirror = document.createElement("div");
  mirror.textContent = `${textarea.value.slice(0, start)}\u200b`;
  Object.assign(mirror.style, {
    position: "absolute",
    visibility: "hidden",
    boxSizing: "border-box",
    width: `${textarea.clientWidth}px`,
    padding: style.padding,
    font: style.font,
    lineHeight: style.lineHeight,
    whiteSpace: "pre-wrap",
    overflowWrap: "break-word",
  });
  document.body.append(mirror);
  textarea.scrollTop = Math.max(0, mirror.offsetHeight - textarea.clientHeight / 3);
  mirror.remove();
}

/** Saving compiles and replaces the document; a compile error keeps the draft open beside it. */
export function DocumentEditor({
  generation,
  document: name,
  label,
  jump = null,
}: {
  generation: GenerationDetail;
  document: Document;
  label: string;
  jump?: { line: number; text: string } | null;
}) {
  const id = generation.generation_id;
  const revision = generation.revisions[name];
  const source = useSource(id, name, revision);
  const save = useEditDocument(id, name);
  // A draft keeps the state it started from, so a newer version is not replaced without a word.
  const [draft, setDraft] = useState<{ text: string; base: number | null } | null>(null);
  const text = draft?.text ?? source.data ?? "";
  const base = draft?.base ?? revision;
  const textarea = useRef<HTMLTextAreaElement>(null);
  const colored = useRef<HTMLPreElement>(null);

  useEffect(() => {
    const element = textarea.current;
    if (jump && element) selectLine(element, refineLine(element.value, jump.line, jump.text));
  }, [jump, source.isPending]);

  if (source.isPending) return <Skeleton rows={6} />;

  return (
    <div className="flex h-full flex-col gap-3 p-4 sm:px-6">
      <label className="sr-only" htmlFor={`${name}-source`}>
        {label} LaTeX source
      </label>
      {/* A textarea can't colour text, so the coloured copy shows through its transparent text. */}
      <div className="relative h-[60dvh] w-full border border-rule bg-wash focus-within:border-ink lg:h-auto lg:flex-1">
        <pre
          ref={colored}
          aria-hidden
          className={`pointer-events-none overflow-hidden ${SOURCE_TEXT}`}
        >
          <LatexCode text={text} />
          {"\n"}
        </pre>
        <textarea
          ref={textarea}
          id={`${name}-source`}
          value={text}
          spellCheck={false}
          readOnly={save.isPending}
          onChange={(event) => setDraft({ text: event.target.value, base })}
          onScroll={(event) => {
            if (colored.current) colored.current.scrollTop = event.currentTarget.scrollTop;
          }}
          className={`resize-none bg-transparent text-transparent caret-ink outline-none ${SOURCE_TEXT}`}
        />
      </div>
      <div>
        <Button
          disabled={save.isPending || source.data === undefined || text === source.data}
          onClick={() =>
            save.mutate(
              { latex: text, revision: base },
              {
                onSuccess: () => setDraft(null),
                // The page now shows the newer version, so saving again replaces it knowingly.
                onError: (error) => {
                  if (error instanceof ApiError && error.status === 409) {
                    setDraft({ text, base: null });
                  }
                },
              },
            )
          }
        >
          {save.isPending ? "Compiling…" : "Save changes"}
        </Button>
      </div>
      <ErrorNote error={source.error ?? save.error} />
    </div>
  );
}
