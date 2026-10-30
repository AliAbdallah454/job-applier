import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";

import { fileUrl, type GenerationDetail } from "../api/client";
import { useGeneration, useRegenerate, useRetry } from "../api/queries";
import { EditDiff } from "../components/EditDiff";
import { Button, Empty, ErrorNote, Eyebrow, formatDate, label, StatusTag } from "../components/ui";

const STEPS = ["queued", "processing", "ready"] as const;
const WAITING: Record<string, string> = {
  queued: "Waiting for a worker. Workers start from zero, so this can take a few minutes.",
  processing: "A worker is tailoring your CV and compiling the PDFs.",
};
const FILES = [
  ["cv_pdf", "CV .pdf"],
  ["cv_tex", "CV .tex"],
  ["motivation_letter_pdf", "Letter .pdf"],
  ["motivation_letter_tex", "Letter .tex"],
  ["changes_json", "Changes .json"],
];

const FAILURE_FILES = [
  ["compilation_source", "Failed source .tex"],
  ["compilation_log", "Compile log .log"],
];

export function GenerationPage() {
  const { id = "" } = useParams();
  const generation = useGeneration(id);
  const retry = useRetry(id);
  const regenerate = useRegenerate(id);
  const navigate = useNavigate();
  const data = generation.data;

  if (generation.error) {
    return (
      <div className="p-6">
        <ErrorNote error={generation.error} />
      </div>
    );
  }
  if (!data) return <Empty>Loading…</Empty>;

  return (
    <div className="flex flex-1 flex-col">
      <header className="flex flex-wrap items-end justify-between gap-4 border-b border-rule p-4 sm:px-6">
        <div className="min-w-0">
          <Eyebrow>
            <Link to="/generations" className="hover:text-ink">
              Generations
            </Link>{" "}
            / {formatDate(data.created_at)}
          </Eyebrow>
          <h1 className="mt-1 text-2xl font-bold tracking-tight text-balance">{data.job_title}</h1>
        </div>
        <div className="flex items-center gap-2">
          <StatusTag status={data.status} />
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
      {data.status === "ready" && <Review generation={data} />}
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
    <div className="mx-auto w-full max-w-xl p-6 pt-20">
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
    </div>
  );
}

function Failure({ generation }: { generation: GenerationDetail }) {
  return (
    <div className="max-w-4xl p-4 sm:p-6">
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
  return (
    <div className="grid flex-1 lg:grid-cols-2">
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
              className={
                document === name ? "font-semibold underline underline-offset-4" : "text-muted"
              }
            >
              {text}
            </button>
          ))}
        </div>
        <iframe
          key={document}
          title={document === "cv_pdf" ? "CV preview" : "Letter preview"}
          src={fileUrl(generation.generation_id, document)}
          className="mt-3 h-[70dvh] w-full flex-1 bg-wash lg:h-auto"
        />
      </section>
      <section className="min-w-0 p-4 sm:p-6">
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
            <h2 className="mt-6 text-lg font-semibold tracking-tight">Edits made</h2>
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
        <div className="mt-6 flex flex-wrap gap-x-4 gap-y-1 border-t border-ink pt-3 text-sm">
          {FILES.filter(([name]) => generation.files.includes(name)).map(([name, text]) => (
            <a
              key={name}
              href={fileUrl(generation.generation_id, name, true)}
              className="underline underline-offset-4"
            >
              {text}
            </a>
          ))}
        </div>
      </section>
    </div>
  );
}
