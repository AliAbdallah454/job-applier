import { useState } from "react";
import { Link } from "react-router-dom";

import type { Status } from "../api/client";
import { useGenerations } from "../api/queries";
import { Empty, ErrorNote, Eyebrow, formatDate, StatusTag } from "../components/ui";

const STATUSES: (Status | "all")[] = ["all", "queued", "processing", "ready", "failed"];

export function History() {
  const generations = useGenerations();
  const [status, setStatus] = useState<Status | "all">("all");
  const [job, setJob] = useState("");
  const words = job.toLowerCase();
  const list = (generations.data ?? []).filter(
    (item) =>
      (status === "all" || item.status === status) && item.job_title.toLowerCase().includes(words),
  );
  return (
    <div className="mx-auto w-full max-w-4xl p-4 sm:p-6">
      <h1 className="text-3xl font-bold tracking-tight">Generations</h1>
      <div className="mt-4 flex flex-wrap items-center justify-between gap-4">
        <div className="flex gap-4 text-sm">
          {STATUSES.map((item) => (
            <button
              key={item}
              type="button"
              aria-pressed={status === item}
              onClick={() => setStatus(item)}
              className={status === item ? "font-semibold underline underline-offset-4" : "text-muted"}
            >
              {item[0].toUpperCase() + item.slice(1)}
            </button>
          ))}
        </div>
        <label className="sr-only" htmlFor="history-job">
          Filter by job
        </label>
        <input
          id="history-job"
          value={job}
          onChange={(event) => setJob(event.target.value)}
          placeholder="Filter by job"
          className="border-b border-ink bg-paper py-1 text-sm outline-none"
        />
      </div>
      <div className="mt-4">
        <ErrorNote error={generations.error} />
      </div>
      <ul className="mt-2">
        {list.map((item) => (
          <li key={item.generation_id} className="border-t border-rule">
            <Link
              to={`/generations/${item.generation_id}`}
              className="flex items-center justify-between gap-4 py-3 hover:bg-wash"
            >
              <span className="min-w-0">
                <span className="block truncate font-medium tracking-tight">{item.job_title}</span>
                <Eyebrow>{formatDate(item.created_at)}</Eyebrow>
              </span>
              <StatusTag status={item.status} />
            </Link>
          </li>
        ))}
      </ul>
      {generations.isSuccess && list.length === 0 && (
        <Empty>
          {generations.data.length
            ? "No generations match."
            : "Nothing yet. Pick a job and choose Tailor my CV."}
        </Empty>
      )}
    </div>
  );
}
