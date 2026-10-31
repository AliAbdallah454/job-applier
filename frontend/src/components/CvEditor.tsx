import { useState } from "react";

import { ApiError, type GenerationDetail } from "../api/client";
import { useCvSource, useEditCv } from "../api/queries";
import { Button, ErrorNote, Skeleton } from "./ui";

/** Saving compiles and replaces the CV; a compile error keeps the draft open beside it. */
export function CvEditor({ generation }: { generation: GenerationDetail }) {
  const id = generation.generation_id;
  const source = useCvSource(id, generation.updated_at);
  const save = useEditCv(id);
  // A draft keeps the state it started from, so a newer CV is not replaced without a word.
  const [draft, setDraft] = useState<{ text: string; base: string | null } | null>(null);
  const text = draft?.text ?? source.data ?? "";
  const base = draft?.base ?? generation.updated_at;

  if (source.isPending) return <Skeleton rows={6} />;

  return (
    <div className="flex h-full flex-col gap-3 p-4 sm:px-6">
      <label className="sr-only" htmlFor="cv-source">
        CV LaTeX source
      </label>
      <textarea
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
