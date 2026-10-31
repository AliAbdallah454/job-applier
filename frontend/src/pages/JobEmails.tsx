import { useState } from "react";
import { Link } from "react-router-dom";

import type { AlertJob, JobAlert } from "../api/client";
import {
  useGenerations,
  useJobAlerts,
  useJobApplicationStatuses,
  useLoadAlertJob,
  useNoteJobOpened,
} from "../api/queries";
import { MailBar } from "../components/MailBar";
import { TailorDialog } from "../components/TailorDialog";
import {
  ACTION,
  Button,
  Empty,
  ErrorNote,
  Eyebrow,
  formatDate,
  label,
  LeaveIcon,
  Skeleton,
} from "../components/ui";

export function JobEmails() {
  const alerts = useJobAlerts();
  const all = alerts.data ?? [];
  return (
    <div className="mx-auto w-full max-w-5xl p-4 sm:p-6">
      <h1 className="text-3xl font-bold tracking-tight">Job emails</h1>
      <p className="mt-1 text-sm text-muted">
        Jobs LinkedIn suggested by email, one group per email. A job&apos;s page is only read when
        you choose to tailor your CV to it.
      </p>
      <MailBar />
      <div className="mt-4 empty:hidden">
        <ErrorNote error={alerts.error} />
      </div>
      {alerts.isPending && <Skeleton rows={4} />}
      {all.map((alert) => (
        <Group key={alert.alert_id} alert={alert} />
      ))}
      {alerts.isSuccess && all.length === 0 && (
        <Empty>No LinkedIn job emails found in your mail yet.</Empty>
      )}
    </div>
  );
}

function Group({ alert }: { alert: JobAlert }) {
  return (
    <section className="mt-6">
      <div className="flex flex-wrap items-baseline justify-between gap-x-4 border-b border-ink pb-2">
        <h2 className="min-w-0 font-semibold tracking-tight break-words">{alert.subject}</h2>
        <Eyebrow>
          {formatDate(alert.received_at)} / {alert.jobs.length}{" "}
          {alert.jobs.length === 1 ? "job" : "jobs"}
        </Eyebrow>
      </div>
      {alert.jobs.length === 0 && (
        <p className="py-3 text-sm text-muted">No jobs could be read from this email.</p>
      )}
      <ul>
        {alert.jobs.map((job) => (
          <Row key={job.job_id} job={job} />
        ))}
      </ul>
    </section>
  );
}

function Row({ job }: { job: AlertJob }) {
  const generations = useGenerations().data ?? [];
  const tailored = generations.find((item) => item.job_id === job.job_id);
  const load = useLoadAlertJob(job.job_id);
  const noteOpened = useNoteJobOpened(job.job_id);
  const application = useJobApplicationStatuses().get(job.job_id);
  const [tailoring, setTailoring] = useState(false);
  const [pasting, setPasting] = useState(false);
  const [text, setText] = useState("");
  const company = job.company ?? "";
  const fieldId = `paste-${job.job_id}`;

  const startTailoring = () => {
    if (job.has_description) setTailoring(true);
    else {
      load.mutate(undefined, {
        onSuccess: () => setTailoring(true),
        onError: () => setPasting(true),
      });
    }
  };

  return (
    <li className="border-b border-rule py-3 lg:px-2">
      <div className="flex flex-wrap items-center justify-between gap-x-6 gap-y-2">
        <div className="min-w-0">
          <p className="font-medium tracking-tight break-words">{job.title}</p>
          <p className="text-sm text-muted">
            {[job.company, job.location].filter(Boolean).join(" / ")}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {application && (
            <Link to="/applications" className="line text-sm text-muted hover:text-ink">
              Application: {label(application)}
            </Link>
          )}
          {tailored && (
            <Link
              to={`/generations/${tailored.generation_id}`}
              className="line text-sm text-muted hover:text-ink"
            >
              Tailored CV
            </Link>
          )}
          <a
            href={job.url}
            target="_blank"
            rel="noreferrer"
            aria-label={`Open ${job.title} on LinkedIn`}
            className={ACTION}
            {...noteOpened}
          >
            LinkedIn
            <LeaveIcon />
          </a>
          <Button primary disabled={load.isPending} onClick={startTailoring}>
            {load.isPending ? "Reading the job…" : "Tailor my CV"}
          </Button>
        </div>
      </div>
      {pasting && (
        <div className="mt-3 flex flex-col gap-2">
          <ErrorNote error={load.error} />
          <label htmlFor={fieldId} className="text-sm">
            Open the job on LinkedIn and paste its description here instead.
          </label>
          <textarea
            id={fieldId}
            rows={6}
            value={text}
            onChange={(event) => setText(event.target.value)}
            className="w-full border border-rule bg-wash p-2 text-sm outline-none focus:border-ink"
          />
          <div>
            <Button
              disabled={!text.trim() || load.isPending}
              onClick={() =>
                load.mutate(text, {
                  onSuccess: () => {
                    setPasting(false);
                    setTailoring(true);
                  },
                })
              }
            >
              Use this description
            </Button>
          </div>
        </div>
      )}
      {tailoring && (
        <TailorDialog
          job={{ job_id: job.job_id, title: job.title, company }}
          onClose={() => setTailoring(false)}
        />
      )}
    </li>
  );
}
