import { useState } from "react";
import { Link } from "react-router-dom";

import type { Application, ApplicationStatus, Interest } from "../api/client";
import {
  useAnswerInterest,
  useApplications,
  useChangeApplication,
  useInterests,
  useRemoveApplication,
} from "../api/queries";
import { MailBar } from "../components/MailBar";
import {
  Button,
  Empty,
  ErrorNote,
  formatDate,
  label,
  LeaveIcon,
  Skeleton,
  TAB,
} from "../components/ui";

const STATUSES: ApplicationStatus[] = [
  "applied",
  "in_review",
  "assessment",
  "interview",
  "offer",
  "rejected",
];

export function Applications() {
  const applications = useApplications();
  const interests = useInterests().data ?? [];
  const [status, setStatus] = useState<ApplicationStatus | "all">("all");
  const all = applications.data ?? [];
  const list = all.filter((item) => status === "all" || item.status === status);
  return (
    <div className="mx-auto w-full max-w-5xl p-4 sm:p-6">
      <h1 className="text-3xl font-bold tracking-tight">Applications</h1>
      <p className="mt-1 text-sm text-muted">
        Followed from your email. The model reads each update and can get one wrong, so you can
        correct any status here.
      </p>
      <MailBar />
      {interests.length > 0 && (
        <section className="mt-6 border border-rule p-4">
          <h2 className="font-semibold tracking-tight">Did you apply?</h2>
          <p className="text-sm text-muted">
            You opened these job posts, but no email has mentioned them yet.
          </p>
          <ul>
            {interests.map((item) => (
              <Question key={item.job_id} interest={item} />
            ))}
          </ul>
        </section>
      )}
      <div className="mt-5 flex flex-wrap gap-x-4 gap-y-1 border-b border-ink pb-2 text-sm">
        {(["all", ...STATUSES] as const).map((item) => (
          <button
            key={item}
            type="button"
            aria-pressed={status === item}
            onClick={() => setStatus(item)}
            className={TAB}
          >
            {label(item)}{" "}
            <span className="font-mono text-[11px]">
              {all.filter((entry) => item === "all" || entry.status === item).length}
            </span>
          </button>
        ))}
      </div>
      <div className="mt-4 empty:hidden">
        <ErrorNote error={applications.error} />
      </div>
      {applications.isPending && <Skeleton rows={4} />}
      <ul>
        {list.map((item) => (
          <Row key={item.application_id} application={item} />
        ))}
      </ul>
      {applications.isSuccess && list.length === 0 && (
        <Empty>{all.length ? "Nothing matches." : "No applications found in your mail yet."}</Empty>
      )}
    </div>
  );
}

function Question({ interest }: { interest: Interest }) {
  const answer = useAnswerInterest(interest.job_id);
  const name = `${interest.title}${interest.company ? ` at ${interest.company}` : ""}`;
  return (
    <li className="border-t border-rule py-2 first:mt-2">
      <div className="flex flex-wrap items-center justify-between gap-x-6 gap-y-2">
        <div className="min-w-0">
          <p className="text-sm font-medium break-words">{interest.title}</p>
          <p className="text-sm text-muted">
            {[interest.company, `opened ${formatDate(interest.opened_at)}`]
              .filter(Boolean)
              .join(" / ")}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-3">
          {interest.url && (
            <a
              href={interest.url}
              target="_blank"
              rel="noreferrer"
              aria-label={`Open ${name}`}
              className="line inline-flex items-center gap-1 text-sm text-muted hover:text-ink"
            >
              Job post
              <LeaveIcon />
            </a>
          )}
          <Button
            disabled={answer.isPending}
            aria-label={`I applied to ${name}`}
            onClick={() => answer.mutate(true)}
          >
            I applied
          </Button>
          <button
            type="button"
            disabled={answer.isPending}
            aria-label={`I did not apply to ${name}`}
            onClick={() => answer.mutate(false)}
            className="line text-sm text-muted hover:text-ink"
          >
            No
          </button>
        </div>
      </div>
      <ErrorNote error={answer.error} />
    </li>
  );
}

function Row({ application }: { application: Application }) {
  const change = useChangeApplication();
  const remove = useRemoveApplication();
  const id = application.application_id;
  const name = `${application.company}${application.role ? `, ${application.role}` : ""}`;
  return (
    <li className="border-b border-rule py-4 lg:px-2">
      <div className="flex flex-wrap items-start justify-between gap-x-6 gap-y-3">
        <div className="min-w-0">
          <p className="font-medium tracking-tight">{application.company}</p>
          <p className="text-sm text-muted">{application.role ?? "Role not named in the emails"}</p>
        </div>
        <div className="flex flex-wrap items-center gap-3">
          {application.job_url && (
            <a
              href={application.job_url}
              target="_blank"
              rel="noreferrer"
              className="line inline-flex items-center gap-1 text-sm text-muted hover:text-ink"
            >
              Job post
              <LeaveIcon />
            </a>
          )}
          {application.generation_id && (
            <Link
              to={`/generations/${application.generation_id}`}
              className="line text-sm text-muted hover:text-ink"
            >
              Tailored CV
            </Link>
          )}
          <select
            aria-label={`Status of ${name}`}
            value={application.status}
            disabled={change.isPending}
            onChange={(event) =>
              change.mutate({ id, status: event.target.value as ApplicationStatus })
            }
            className="border border-rule bg-paper p-1.5 text-sm"
          >
            {STATUSES.map((status) => (
              <option key={status} value={status}>
                {label(status)}
              </option>
            ))}
          </select>
          <button
            type="button"
            aria-label={`Remove ${name}`}
            disabled={remove.isPending}
            onClick={() => remove.mutate(id)}
            className="line text-sm text-muted hover:text-ink"
          >
            Remove
          </button>
        </div>
      </div>
      <ErrorNote error={change.error ?? remove.error} />
      <ol className="mt-3 space-y-1 border-l border-rule pl-3 text-sm">
        {application.events.toReversed().map((event) => (
          <li key={`${event.received_at}-${event.subject}`} className="flex flex-wrap gap-x-3">
            <span className="font-mono text-[11px] leading-5 text-muted">
              {formatDate(event.received_at)}
            </span>
            <span className="font-medium">{label(event.status)}</span>
            <span className="min-w-0 break-words text-muted">{event.subject}</span>
          </li>
        ))}
      </ol>
    </li>
  );
}
