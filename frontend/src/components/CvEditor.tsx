import { useEffect, useRef, useState } from "react";

import { ApiError, type GenerationDetail } from "../api/client";
import { useCvSource, useEditCv } from "../api/queries";
import { refineLine } from "./sourceLine";
import { Button, ErrorNote, Skeleton } from "./ui";

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

/** Saving compiles and replaces the CV; a compile error keeps the draft open beside it. */
export function CvEditor({
  generation,
  jump,
}: {
  generation: GenerationDetail;
  jump: { line: number; text: string } | null;
}) {
  const id = generation.generation_id;
  const source = useCvSource(id, generation.updated_at);
  const save = useEditCv(id);
  // A draft keeps the state it started from, so a newer CV is not replaced without a word.
  const [draft, setDraft] = useState<{ text: string; base: string | null } | null>(null);
  const text = draft?.text ?? source.data ?? "";
  const base = draft?.base ?? generation.updated_at;
  const textarea = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    const element = textarea.current;
    if (jump && element) selectLine(element, refineLine(element.value, jump.line, jump.text));
  }, [jump, source.isPending]);

  if (source.isPending) return <Skeleton rows={6} />;

  return (
    <div className="flex h-full flex-col gap-3 p-4 sm:px-6">
      <label className="sr-only" htmlFor="cv-source">
        CV LaTeX source
      </label>
      <textarea
        ref={textarea}
        id="cv-source"
        value={text}
        spellCheck={false}
        readOnly={save.isPending}
        onChange={(event) => setDraft({ text: event.target.value, base })}
        className="h-[60dvh] w-full flex-1 resize-none border border-rule bg-wash p-3 font-mono text-xs leading-relaxed outline-none focus:border-ink lg:h-auto"
      />
      <div>
        <Button
          disabled={save.isPending || source.data === undefined || text === source.data}
          onClick={() =>
            save.mutate(
              { cvLatex: text, updatedAt: base },
              {
                onSuccess: () => setDraft(null),
                // The page now shows the newer CV, so saving again replaces it knowingly.
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
