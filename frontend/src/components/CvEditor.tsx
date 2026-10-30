import { useState } from "react";
import { useNavigate } from "react-router-dom";

import type { GenerationDetail } from "../api/client";
import { useCvSource, useEditCv } from "../api/queries";
import { Button, ErrorNote, Skeleton } from "./ui";

/** Saving compiles and stores a new version; a compile error keeps the draft open beside it. */
export function CvEditor({ generation }: { generation: GenerationDetail }) {
  const id = generation.generation_id;
  const source = useCvSource(id);
  const save = useEditCv(id);
  const navigate = useNavigate();
  const [draft, setDraft] = useState<string | null>(null);
  const text = draft ?? source.data ?? "";

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
        onChange={(event) => setDraft(event.target.value)}
        className="h-[60dvh] w-full flex-1 resize-none border border-rule bg-wash p-3 font-mono text-xs leading-relaxed outline-none focus:border-ink lg:h-auto"
      />
      <div>
        <Button
          disabled={save.isPending || text === source.data}
          onClick={() =>
            save.mutate(text, {
              onSuccess: (next) => {
                navigate(`/generations/${next.generation_id}`);
                setDraft(null);
              },
            })
          }
        >
          {save.isPending ? "Compiling…" : "Save new version"}
        </Button>
      </div>
      <ErrorNote error={source.error ?? save.error} />
    </div>
  );
}
