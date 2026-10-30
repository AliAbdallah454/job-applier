import { useState } from "react";
import { Link } from "react-router-dom";

import type { Generation, Status } from "../api/client";
import { isActive, useGenerations } from "../api/queries";
import { GenerationLinks } from "../components/GenerationLinks";
import { Empty, ErrorNote, formatDate, Skeleton, StatusTag, TAB } from "../components/ui";

const STATUSES: (Status | "all")[] = ["all", "queued", "processing", "ready", "failed"];

export function History() {
  const generations = useGenerations();
  const [status, setStatus] = useState<Status | "all">("all");
  const [job, setJob] = useState("");
  const words = job.toLowerCase();
  const all = generations.data ?? [];
  const list = all.filter(
    (item) =>
      (status === "all" || item.status === status) &&
      `${item.job_title} ${item.company ?? ""}`.toLowerCase().includes(words),
  );
  return (
    <div className="mx-auto w-full max-w-5xl p-4 sm:p-6">
      <h1 className="text-3xl font-bold tracking-tight">Tailored CVs</h1>
      <p className="mt-1 text-sm text-muted">
        Download what you need here, or open one to review every change before you send it.
      </p>
      <div className="mt-5 flex flex-wrap items-center justify-between gap-4 border-b border-ink pb-2">
        <div className="flex flex-wrap gap-x-4 gap-y-1 text-sm">
          {STATUSES.map((item) => (
            <button
              key={item}
              type="button"
              aria-pressed={status === item}
              onClick={() => setStatus(item)}
              className={TAB}
            >
              {item[0].toUpperCase() + item.slice(1)}{" "}
              <span className="font-mono text-[11px]">
                {all.filter((entry) => item === "all" || entry.status === item).length}
              </span>
            </button>
          ))}
        </div>
        <label className="sr-only" htmlFor="history-job">
          Filter by job or company
        </label>
        <input
          id="history-job"
          value={job}
          onChange={(event) => setJob(event.target.value)}
          placeholder="Filter by job or company"
          className="bg-paper py-1 text-sm outline-none sm:w-56 sm:text-right"
        />
      </div>
      <div className="mt-4 empty:hidden">
        <ErrorNote error={generations.error} />
      </div>
      {generations.isPending && <Skeleton rows={4} />}
      <ul>
        {list.map((item, index) => (
          <Row key={item.generation_id} generation={item} index={index} />
        ))}
      </ul>
      {generations.isSuccess && list.length === 0 && (
        <Empty>
          {all.length ? (
            "Nothing matches."
          ) : (
            <>
              Nothing yet.{" "}
              <Link to="/" className="underline underline-offset-4">
                Pick a job
              </Link>{" "}
              and choose Tailor my CV.
            </>
          )}
        </Empty>
      )}
    </div>
  );
}

function Row({ generation, index }: { generation: Generation; index: number }) {
  const created = new Date(generation.created_at);
  const review = `/generations/${generation.generation_id}`;
  return (
    <li
      style={{ animationDelay: `${Math.min(index, 12) * 24}ms` }}
      className="rise relative grid gap-x-6 gap-y-3 overflow-hidden border-b border-rule py-4 transition-colors hover:bg-wash lg:grid-cols-[3.5rem_minmax(0,1fr)_auto] lg:items-center lg:px-2"
    >
      <time
        dateTime={generation.created_at}
        title={formatDate(generation.created_at)}
        className="hidden font-mono text-[11px] leading-snug text-muted lg:block"
      >
        {created.toLocaleDateString(undefined, { day: "numeric", month: "short" })}
        <br />
        {created.toLocaleTimeString(undefined, { timeStyle: "short" })}
      </time>
      <div className="min-w-0">
        <Link
          to={review}
          className="block truncate font-medium tracking-tight underline-offset-4 hover:underline"
        >
          {generation.job_title}
        </Link>
        <p className="mt-0.5 flex flex-wrap items-center gap-x-3 text-sm text-muted">
          {generation.company && <span>{generation.company}</span>}
          <span className="font-mono text-[11px] lg:hidden">{formatDate(generation.created_at)}</span>
          <span className="text-ink">
            <StatusTag status={generation.status} />
          </span>
        </p>
      </div>
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2 lg:justify-end">
        <GenerationLinks generation={generation} />
        <Link to={review} className="line text-sm text-muted hover:text-ink">
          {generation.status === "failed" ? "See why" : "Review"}
        </Link>
      </div>
      {isActive(generation.status) && (
        <span className="absolute inset-x-0 bottom-0 h-px">
          <span className="travel block h-full w-1/4 bg-ink" />
        </span>
      )}
    </li>
  );
}
