import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";

import {
  downloadUrl,
  fileUrl,
  type GenerationDetail,
  previewUrl,
  versionFileUrl,
} from "../api/client";
import { useGeneration, useRegenerate, useRetry } from "../api/queries";
import { CvEditor } from "../components/CvEditor";
import { EditDiff } from "../components/EditDiff";
import { GenerationLinks } from "../components/GenerationLinks";
import { Celebration, Joke, Mascot, Snip } from "../components/Silly";
import {
  Button,
  ErrorNote,
  Eyebrow,
  formatDate,
  label,
  Skeleton,
  StatusTag,
  TAB,
} from "../components/ui";

const STEPS = ["queued", "processing", "ready"] as const;
const WAITING: Record<string, string> = {
  queued: "Waiting for a worker. Workers start from zero, so this can take a few minutes.",
  processing: "A worker is tailoring your CV and compiling the PDFs.",
};
const FILES = [
  ["cv_tex", "CV .tex"],
  ["motivation_letter_tex", "Letter .tex"],
  ["changes_json", "Changes .json"],
];

const FAILURE_FILES = [
  ["compilation_source", "Failed source .tex"],
  ["compilation_log", "Compile log .log"],
];

/** A row from the old edit flow already holds the user's CV before it has any versions. */
function isEdited(generation: GenerationDetail) {
  return generation.previous_versions.length > 0 || generation.starts_from_edit;
}

export function GenerationPage() {
  const { id = "" } = useParams();
  const generation = useGeneration(id);
  const retry = useRetry(id);
  const regenerate = useRegenerate(id);
  const navigate = useNavigate();
  const data = generation.data;
  const status = data?.status;
  const previous = useRef(status);
  const [celebrating, setCelebrating] = useState(false);
  const stopCelebrating = useCallback(() => setCelebrating(false), []);

  useEffect(() => {
    // Only for a run seen finishing here, not for reopening an old one.
    if (status === "ready" && (previous.current === "queued" || previous.current === "processing")) {
      setCelebrating(true);
    }
    previous.current = status;
  }, [status]);

  // A failed refresh of a page already showing keeps it, and with it any unsaved draft.
  if (generation.error && !data) {
    return (
      <div className="p-6">
        <ErrorNote error={generation.error} />
      </div>
    );
  }
  if (!data) return <Skeleton rows={3} />;

  return (
    // A ready review scrolls its two sides apart, so the PDF stays beside the changes.
    <div
      className={`flex flex-1 flex-col ${
        data.status === "ready" ? "lg:h-[calc(100dvh-53px)] lg:flex-none" : ""
      }`}
    >
      <header className="flex flex-wrap items-end justify-between gap-4 border-b border-rule p-4 sm:px-6">
        <div className="min-w-0">
          <Eyebrow>
            <Link to="/generations" className="hover:text-ink">
              Tailored CVs
            </Link>{" "}
            / {formatDate(data.created_at)} / {data.model_id}
            {isEdited(data) && " / edited by you"}
          </Eyebrow>
          <h1 className="mt-1 text-2xl font-bold tracking-tight text-balance">{data.job_title}</h1>
          {data.company && <p className="text-muted">{data.company}</p>}
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <span className="mr-2">
            <StatusTag status={data.status} />
          </span>
          <GenerationLinks generation={data} />
          {data.retryable && (
            <Button disabled={retry.isPending} onClick={() => retry.mutate()}>
              {retry.isPending ? "Retrying…" : "Retry"}
            </Button>
          )}
          {(data.status === "ready" || data.status === "failed") && (
            <Button
              disabled={regenerate.isPending}
              onClick={() =>
                regenerate.mutate(undefined, {
                  onSuccess: (next) => navigate(`/generations/${next.generation_id}`),
                })
              }
            >
              {regenerate.isPending ? "Starting…" : "Regenerate"}
            </Button>
          )}
        </div>
      </header>
      <div className="px-4 sm:px-6">
        <ErrorNote error={retry.error ?? regenerate.error} />
      </div>
      {celebrating && <Celebration onDone={stopCelebrating} />}
      {data.status === "ready" && <Review key={data.generation_id} generation={data} />}
      {data.status === "failed" && <Failure generation={data} />}
      {(data.status === "queued" || data.status === "processing") && (
        <Progress status={data.status} />
      )}
    </div>
  );
}

function Progress({ status }: { status: "queued" | "processing" }) {
  const current = STEPS.indexOf(status);
  return (
    <div className="mx-auto w-full max-w-xl p-6 pt-12">
      <div className="mb-8 flex justify-center">
        <Mascot mood="working" />
      </div>
      <ol className="grid grid-cols-3 gap-1">
        {STEPS.map((step, index) => (
          <li key={step}>
            <div className="relative h-1 overflow-hidden bg-rule">
              {index < current && <div className="absolute inset-0 bg-ink" />}
              {index === current && <div className="travel absolute inset-y-0 w-1/4 bg-ink" />}
            </div>
            <span className={`mt-2 block font-mono text-[11px] ${index > current ? "text-muted" : ""}`}>
              {step}
            </span>
          </li>
        ))}
      </ol>
      <p className="mt-8 text-muted" aria-live="polite">
        {WAITING[status]}
      </p>
      <Snip />
      <Joke />
    </div>
  );
}

function Failure({ generation }: { generation: GenerationDetail }) {
  return (
    <div className="max-w-4xl p-4 sm:p-6">
      <div className="mb-6 ml-4 h-20">
        <Mascot mood="sad" size={60} />
      </div>
      <Eyebrow>Failed while {label(generation.failure_stage).toLowerCase()}</Eyebrow>
      <p className="mt-2 text-lg">{generation.error_detail ?? "The worker gave no details."}</p>
      {generation.compilation_log && (
        <>
          <h2 className="mt-6 text-sm font-medium">Compile log</h2>
          <pre className="mt-2 max-h-[60dvh] overflow-auto bg-wash p-4 font-mono text-xs leading-relaxed">
            {generation.compilation_log}
          </pre>
          <div className="mt-3 flex gap-4 text-sm">
            {FAILURE_FILES.filter(([name]) => generation.files.includes(name)).map(([name, text]) => (
              <a
                key={name}
                href={fileUrl(generation.generation_id, name, true)}
                className="underline underline-offset-4"
              >
                {text}
              </a>
            ))}
          </div>
        </>
      )}
    </div>
  );
}

function Review({ generation }: { generation: GenerationDetail }) {
  const [document, setDocument] = useState<"cv_pdf" | "motivation_letter_pdf">("cv_pdf");
  const [panel, setPanel] = useState<"review" | "edit">("review");
  return (
    <div className="grid min-h-0 flex-1 lg:grid-cols-2 lg:grid-rows-[minmax(0,1fr)]">
      <section className="flex min-w-0 flex-col border-b border-rule lg:border-r lg:border-b-0">
        <div className="flex gap-5 px-4 pt-3 text-sm sm:px-6" role="tablist">
          {(
            [
              ["cv_pdf", "CV"],
              ["motivation_letter_pdf", "Letter"],
            ] as const
          ).map(([name, text]) => (
            <button
              key={name}
              role="tab"
              type="button"
              aria-selected={document === name}
              onClick={() => setDocument(name)}
              className={TAB}
            >
              {text}
            </button>
          ))}
        </div>
        <iframe
          key={`${document}-${generation.updated_at}`}
          title={document === "cv_pdf" ? "CV preview" : "Letter preview"}
          src={previewUrl(generation.generation_id, document, generation.updated_at)}
          className="mt-3 h-[70dvh] w-full flex-1 bg-wash lg:h-auto"
        />
      </section>
      <section className="flex min-w-0 flex-col">
        <div className="flex gap-5 px-4 pt-3 text-sm sm:px-6" role="tablist">
          {(
            [
              ["review", "Review"],
              ["edit", "Edit LaTeX"],
            ] as const
          ).map(([name, text]) => (
            <button
              key={name}
              role="tab"
              type="button"
              aria-selected={panel === name}
              onClick={() => setPanel(name)}
              className={TAB}
            >
              {text}
            </button>
          ))}
        </div>
        {/* Hidden rather than removed, so switching tabs keeps an unsaved draft. */}
        <div hidden={panel !== "edit"} className="min-h-0 flex-1">
          <CvEditor generation={generation} />
        </div>
        <div hidden={panel !== "review"} className="min-h-0 flex-1 lg:overflow-y-auto">
          <ReviewList generation={generation} />
        </div>
      </section>
    </div>
  );
}

function ReviewList({ generation }: { generation: GenerationDetail }) {
  return (
    <div className="p-4 sm:p-6">
      <Eyebrow>Job keywords</Eyebrow>
      <ul className="mt-2 flex flex-wrap gap-1">
        {generation.keywords.map((keyword) => (
          <li key={keyword} className="border border-rule px-2 py-0.5 text-xs">
            {keyword}
          </li>
        ))}
      </ul>
      {generation.explanations.length > 0 && (
        <>
          <h2 className="mt-6 text-lg font-semibold tracking-tight">
            {isEdited(generation) ? "Edits made by the model" : "Edits made"}
          </h2>
          <ul className="mt-2 space-y-2">
            {generation.explanations.map((item, index) => (
              <li key={index} className="text-sm">
                <span className="font-medium">{item.change}</span>
                <span className="text-muted"> — {item.reason}</span>
              </li>
            ))}
          </ul>
        </>
      )}
      <h2 className="mt-6 text-lg font-semibold tracking-tight">
        {generation.changes.length} {generation.changes.length === 1 ? "change" : "changes"} in
        the CV
      </h2>
      <p className="mb-3 text-sm text-muted">
        Compared from the two CV sources, so nothing is left out. Nobody fact-checked them: read
        each one before you send this CV.
      </p>
      {generation.changes.map((change, index) => (
        <EditDiff key={index} change={change} />
      ))}
      <Eyebrow>Sources</Eyebrow>
      <div className="mt-1 flex flex-wrap gap-x-4 gap-y-1 text-sm">
        {FILES.filter(([name]) => generation.files.includes(name)).map(([name, text]) => (
          <a
            key={name}
            href={downloadUrl(generation.generation_id, name, generation.updated_at)}
            className="underline underline-offset-4"
          >
            {text}
          </a>
        ))}
      </div>
      {generation.previous_versions.length > 0 && <PreviousVersions generation={generation} />}
    </div>
  );
}

function PreviousVersions({ generation }: { generation: GenerationDetail }) {
  const id = generation.generation_id;
  return (
    <>
      <h2 className="mt-6 text-lg font-semibold tracking-tight">Previous versions</h2>
      <ul className="mt-2 space-y-2">
        {generation.previous_versions.toReversed().map(({ number, saved_at: savedAt }) => (
          <li key={number} className="flex flex-wrap gap-x-4 gap-y-1 text-sm">
            <span className="font-medium">
              Version {number}
              {number === 1 && !generation.starts_from_edit && " (the model's version)"}
            </span>
            <span className="text-muted">{formatDate(savedAt)}</span>
            <a
              href={versionFileUrl(id, number, "cv_pdf")}
              target="_blank"
              rel="noreferrer"
              aria-label={`Open the PDF of version ${number}`}
              className="underline underline-offset-4"
            >
              PDF
            </a>
            <a
              href={versionFileUrl(id, number, "cv_tex", true)}
              aria-label={`Download the .tex of version ${number}`}
              className="underline underline-offset-4"
            >
              .tex
            </a>
          </li>
        ))}
      </ul>
    </>
  );
}
