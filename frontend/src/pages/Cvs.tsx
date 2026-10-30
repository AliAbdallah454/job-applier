import { useState } from "react";

import { useCvs, useDeleteCv, useUploadCv } from "../api/queries";
import { Button, Empty, ErrorNote, Eyebrow, formatDate } from "../components/ui";

export function Cvs() {
  const cvs = useCvs();
  const upload = useUploadCv();
  const remove = useDeleteCv();
  const [dragging, setDragging] = useState(false);
  const [confirming, setConfirming] = useState<string | null>(null);

  return (
    <div className="mx-auto w-full max-w-3xl p-4 sm:p-6">
      <h1 className="text-3xl font-bold tracking-tight">CVs</h1>
      <p className="mt-1 max-w-prose text-sm text-muted">
        Your own LaTeX source, as one self-contained .tex file. Projects with \input, a custom
        .cls or images aren't supported yet.
      </p>
      <label
        htmlFor="cv-file"
        onDragOver={(event) => {
          event.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(event) => {
          event.preventDefault();
          setDragging(false);
          const file = event.dataTransfer.files[0];
          if (file) upload.mutate(file);
        }}
        className={`mt-5 block cursor-pointer border border-dashed border-ink p-8 text-center text-sm transition-colors ${
          dragging ? "bg-ink text-paper" : "hover:bg-wash"
        }`}
      >
        {upload.isPending ? "Uploading…" : "Drop a .tex file here, or click to choose one"}
        <input
          id="cv-file"
          type="file"
          accept=".tex"
          className="sr-only"
          onChange={(event) => {
            const file = event.target.files?.[0];
            if (file) upload.mutate(file);
            event.target.value = "";
          }}
        />
      </label>
      <div className="mt-3">
        <ErrorNote error={cvs.error ?? upload.error ?? remove.error} />
      </div>
      <ul className="mt-4">
        {cvs.data?.map((cv) => (
          <li
            key={cv.cv_id}
            className="rise flex items-center justify-between gap-4 border-t border-rule py-3"
          >
            <span className="min-w-0">
              <span className="block truncate font-mono text-sm">{cv.filename}</span>
              <Eyebrow>
                {formatDate(cv.uploaded_at)} / {(cv.size / 1000).toFixed(1)} KB
              </Eyebrow>
            </span>
            {confirming === cv.cv_id ? (
              <span className="flex gap-2">
                <Button onClick={() => setConfirming(null)}>Keep</Button>
                <Button primary onClick={() => remove.mutate(cv.cv_id)}>
                  Delete
                </Button>
              </span>
            ) : (
              <Button onClick={() => setConfirming(cv.cv_id)}>Delete</Button>
            )}
          </li>
        ))}
      </ul>
      {cvs.data?.length === 0 && <Empty>No CVs yet.</Empty>}
    </div>
  );
}
