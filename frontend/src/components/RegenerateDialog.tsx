import { useEffect, useRef, useState } from "react";

import { Button, ErrorNote, Eyebrow } from "./ui";

export function RegenerateDialog({
  error,
  pending,
  onClose,
  onStart,
}: {
  error: unknown;
  pending: boolean;
  onClose: () => void;
  onStart: (instructions?: string) => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [text, setText] = useState("");

  useEffect(() => {
    const element = dialog.current;
    if (element && !element.open) element.showModal?.();
  }, []);

  return (
    <dialog
      ref={dialog}
      onClose={onClose}
      className="rise m-auto w-[min(32rem,calc(100vw-2rem))] border border-ink bg-paper p-6 text-ink"
    >
      <Eyebrow>Regenerate</Eyebrow>
      <label className="mt-2 block text-sm font-medium">
        What should the model fix in this version? (optional)
        <textarea
          rows={5}
          maxLength={2000}
          value={text}
          onChange={(event) => setText(event.target.value)}
          placeholder="For example: shorten the second bullet, and make the cover letter warmer."
          className="mt-2 block w-full border border-rule bg-wash p-2 font-normal outline-none focus:border-ink"
        />
      </label>
      <p className="mt-2 text-xs text-muted">
        The model reads the current CV and letters, including your edits, and changes only what you
        comment on. It still will not claim anything your CV and profile do not support. Leave this
        empty to start over from your original CV. The current version moves to Previous versions
        once the new one is ready, and stays as it is if this fails. Unsaved changes in the editor
        are lost.
      </p>
      <div className="mt-3 empty:hidden">
        <ErrorNote error={error} />
      </div>
      <div className="mt-6 flex justify-end gap-2">
        <Button onClick={() => dialog.current?.close()}>Cancel</Button>
        <Button primary disabled={pending} onClick={() => onStart(text.trim() || undefined)}>
          {pending ? "Starting…" : "Regenerate"}
        </Button>
      </div>
    </dialog>
  );
}
