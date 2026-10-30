import { useEffect, useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";

import { ApiError, type Job } from "../api/client";
import { useCvs, useOptions, useStartGeneration, useUploadCv } from "../api/queries";
import { Button, ErrorNote, Eyebrow, formatDate } from "./ui";

export function TailorDialog({ job, onClose }: { job: Job; onClose: () => void }) {
  const dialog = useRef<HTMLDialogElement>(null);
  const navigate = useNavigate();
  const cvs = useCvs();
  const upload = useUploadCv();
  const start = useStartGeneration();
  const models = useOptions().data?.models ?? [];
  const [picked, setPicked] = useState<string | null>(null);
  const [pickedModel, setPickedModel] = useState<string | null>(null);
  const selected = picked ?? cvs.data?.[0]?.cv_id ?? null;
  const model = pickedModel ?? models[0]?.id;

  useEffect(() => {
    const element = dialog.current;
    if (element && !element.open) element.showModal?.();
  }, []);

  const needsProfile = start.error instanceof ApiError && start.error.status === 409;

  return (
    <dialog
      ref={dialog}
      onClose={onClose}
      className="rise m-auto w-[min(32rem,calc(100vw-2rem))] border border-ink bg-paper p-6 text-ink"
    >
      <Eyebrow>Tailor my CV</Eyebrow>
      <h2 className="mt-1 text-xl font-semibold tracking-tight text-balance">{job.title}</h2>
      <p className="text-sm text-muted">{job.company}</p>

      <fieldset className="mt-5">
        <legend className="mb-2 text-sm font-medium">Which CV?</legend>
        {cvs.data?.length === 0 && (
          <p className="text-sm text-muted">You have no CVs yet. Upload a .tex file to start.</p>
        )}
        <div className="max-h-56 overflow-y-auto">
          {cvs.data?.map((cv) => (
            <label
              key={cv.cv_id}
              className="flex cursor-pointer items-baseline gap-3 border-t border-rule py-2 text-sm"
            >
              <input
                type="radio"
                name="cv"
                className="accent-current"
                checked={selected === cv.cv_id}
                onChange={() => setPicked(cv.cv_id)}
              />
              <span className="min-w-0 flex-1 truncate font-mono text-xs">{cv.filename}</span>
              <span className="text-xs text-muted">{formatDate(cv.uploaded_at)}</span>
            </label>
          ))}
        </div>
        <label className="mt-2 inline-block cursor-pointer text-sm underline underline-offset-4">
          {upload.isPending ? "Uploading…" : "Upload a .tex file"}
          <input
            type="file"
            accept=".tex"
            className="sr-only"
            onChange={(event) => {
              const file = event.target.files?.[0];
              if (file) {
                upload.mutate(file, { onSuccess: (cv) => setPicked(cv.cv_id) });
              }
              event.target.value = "";
            }}
          />
        </label>
      </fieldset>

      {models.length > 0 && (
        <label className="mt-5 block text-sm font-medium">
          Which model?
          <select
            className="mt-2 block w-full border border-rule bg-paper p-2 font-normal"
            value={model}
            onChange={(event) => setPickedModel(event.target.value)}
          >
            {models.map((option) => (
              <option key={option.id} value={option.id}>
                {option.label}
              </option>
            ))}
          </select>
        </label>
      )}

      <div className="mt-4 flex flex-col gap-2">
        <ErrorNote error={cvs.error ?? upload.error ?? start.error} />
        {needsProfile && (
          <Link to="/profile" className="text-sm underline underline-offset-4">
            Go to your profile
          </Link>
        )}
      </div>

      <div className="mt-6 flex justify-end gap-2">
        <Button onClick={() => dialog.current?.close()}>Cancel</Button>
        <Button
          primary
          disabled={!selected || start.isPending}
          onClick={() =>
            selected &&
            start.mutate(
              { job_id: job.job_id, cv_id: selected, model_id: model },
              { onSuccess: (generation) => navigate(`/generations/${generation.generation_id}`) },
            )
          }
        >
          {start.isPending ? "Starting…" : "Start tailoring"}
        </Button>
      </div>
    </dialog>
  );
}
