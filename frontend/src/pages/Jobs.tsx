import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";

import type { Generation, Job } from "../api/client";
import {
  type JobQuery,
  useGenerations,
  useJobApplicationStatuses,
  useJobs,
  useNoteJobOpened,
  useOptions,
} from "../api/queries";
import { GenerationLinks } from "../components/GenerationLinks";
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
  StatusTag,
} from "../components/ui";
import { descriptionBlocks } from "./jobText";

/** How many pages in a row may filter down to nothing before asking the user to continue. */
const EMPTY_PAGE_LIMIT = 10;
const SELECT = "w-full border-b border-ink bg-paper py-1.5 text-sm outline-none";

export function Jobs() {
  const [params, setParams] = useSearchParams();
  const query: JobQuery = {
    tech: params.get("tech") ?? "tech",
    seniority: params.get("seniority") ?? "entry,junior",
    category: params.get("category") ?? "",
    contract: params.get("contract") ?? "",
    q: params.get("q") ?? "",
  };
  const [search, setSearch] = useState(query.q);
  const options = useOptions();
  const jobs = useJobs(query);
  const generations = useGenerations();

  const update = (name: string, value: string | null) =>
    setParams(
      (current) => {
        const next = new URLSearchParams(current);
        if (value === null) next.delete(name);
        else next.set(name, value);
        return next;
      },
      { replace: true },
    );

  useEffect(() => {
    const timer = setTimeout(() => {
      if (search !== query.q) update("q", search || null);
    }, 300);
    return () => clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [search]);

  const pages = jobs.data?.pages ?? [];
  const list = useMemo(() => pages.flatMap((page) => page.jobs), [pages]);
  const trailingEmpty = pages.length - 1 - pages.findLastIndex((page) => page.jobs.length > 0);
  const { hasNextPage, isFetchingNextPage, fetchNextPage } = jobs;

  useEffect(() => {
    // A filtered page can be empty while more remain, so keep going without a click.
    const lastEmpty = pages.at(-1)?.jobs.length === 0;
    if (lastEmpty && hasNextPage && !isFetchingNextPage && trailingEmpty < EMPTY_PAGE_LIMIT) {
      void fetchNextPage();
    }
  }, [pages, hasNextPage, isFetchingNextPage, fetchNextPage, trailingEmpty]);

  const detail = useRef<HTMLElement>(null);
  const selectedId = params.get("job");
  const selected = list.find((job) => job.job_id === selectedId) ?? list[0];
  const levels = query.seniority.split(",").filter(Boolean);

  const toggleLevel = (level: string) => {
    const next = levels.includes(level)
      ? levels.filter((item) => item !== level)
      : [...levels, level];
    update("seniority", next.join(","));
  };

  return (
    <div className="grid flex-1 md:h-[calc(100dvh-53px)] md:min-h-0 md:flex-none md:grid-cols-[11rem_minmax(0,0.8fr)_minmax(0,1.4fr)] md:grid-rows-[minmax(0,1fr)] lg:grid-cols-[12rem_minmax(0,0.8fr)_minmax(0,1.4fr)]">
      <aside className="flex flex-col gap-5 border-b border-rule p-4 md:overflow-y-auto md:border-r md:border-b-0 sm:px-6 md:px-4">
        <div>
          <label htmlFor="job-search">
            <Eyebrow>Search</Eyebrow>
          </label>
          <input
            id="job-search"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            placeholder="Python, Paris…"
            className={SELECT}
          />
        </div>
        <div>
          <Eyebrow>Level</Eyebrow>
          <div className="mt-2 flex flex-wrap gap-1">
            {[...(options.data?.seniorities ?? []), "unknown"].map((level) => (
              <button
                key={level}
                type="button"
                aria-pressed={levels.includes(level)}
                onClick={() => toggleLevel(level)}
                className={`border px-2 py-0.5 text-xs transition-colors ${
                  levels.includes(level)
                    ? "border-ink bg-ink text-paper"
                    : "border-rule text-muted hover:border-ink hover:text-ink"
                }`}
              >
                {label(level === "unknown" ? null : level)}
              </button>
            ))}
          </div>
        </div>
        <div>
          <label htmlFor="job-relevance">
            <Eyebrow>Field</Eyebrow>
          </label>
          <select
            id="job-relevance"
            value={query.tech}
            onChange={(event) => update("tech", event.target.value)}
            className={SELECT}
          >
            <option value="tech">Tech</option>
            <option value="non_tech">Non-tech</option>
            <option value="unknown">Unspecified</option>
          </select>
        </div>
        <div>
          <label htmlFor="job-category">
            <Eyebrow>Category</Eyebrow>
          </label>
          <select
            id="job-category"
            value={query.category}
            onChange={(event) => update("category", event.target.value || null)}
            className={SELECT}
          >
            <option value="">All</option>
            {options.data?.categories.map((category) => (
              <option key={category} value={category}>
                {label(category)}
              </option>
            ))}
          </select>
        </div>
        <div>
          <label htmlFor="job-contract">
            <Eyebrow>Contract</Eyebrow>
          </label>
          <select
            id="job-contract"
            value={query.contract}
            onChange={(event) => update("contract", event.target.value || null)}
            className={SELECT}
          >
            <option value="">All</option>
            {options.data?.contract_types.map((contract) => (
              <option key={contract} value={contract}>
                {label(contract)}
              </option>
            ))}
            <option value="unknown">Unspecified</option>
          </select>
        </div>
      </aside>

      <section className="min-w-0 border-b border-rule md:overflow-y-auto md:border-r md:border-b-0">
        {list.length > 0 && (
          <div className="sticky top-0 z-10 border-b border-rule bg-paper px-4 py-1.5 font-mono text-[11px] text-muted sm:px-6">
            {list.length}
            {hasNextPage ? "+" : ""} {list.length === 1 ? "job" : "jobs"}
          </div>
        )}
        {jobs.error && (
          <div className="p-4 sm:px-6">
            <ErrorNote error={jobs.error} />
          </div>
        )}
        {list.map((job, index) => (
          <JobRow
            key={job.job_id}
            job={job}
            index={index}
            selected={job.job_id === selected?.job_id}
            generation={generations.data?.find((item) => item.job_id === job.job_id)}
            onSelect={() => {
              update("job", job.job_id);
              // On narrow screens the detail sits below the whole list.
              if (window.innerWidth < 768) detail.current?.scrollIntoView?.();
            }}
          />
        ))}
        {!jobs.isPending && !jobs.error && list.length === 0 && !hasNextPage && (
          <Empty>No jobs match these filters.</Empty>
        )}
        {jobs.isPending && <Skeleton />}
        {hasNextPage && (
          <div className="flex justify-center p-4 sm:px-6">
            <Button disabled={isFetchingNextPage} onClick={() => void fetchNextPage()}>
              {isFetchingNextPage ? "Loading…" : "Load more"}
            </Button>
          </div>
        )}
      </section>

      <section ref={detail} className="min-w-0 md:overflow-y-auto">
        {selected && <JobDetail key={selected.job_id} job={selected} generations={generations.data} />}
      </section>
    </div>
  );
}

function JobRow({
  job,
  index,
  selected,
  generation,
  onSelect,
}: {
  job: Job;
  index: number;
  selected: boolean;
  generation?: Generation;
  onSelect: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onSelect}
      aria-current={selected}
      style={{ animationDelay: `${Math.min(index, 12) * 24}ms` }}
      className={`fill rise block w-full border-b border-rule px-4 py-3 text-left sm:px-6 ${
        selected ? "" : "hover:bg-wash"
      }`}
    >
      <span className="flex items-baseline justify-between gap-3">
        <span className="min-w-0 font-medium tracking-tight">{job.title}</span>
        {generation && <StatusTag status={generation.status} />}
      </span>
      <span className={`block truncate text-sm ${selected ? "opacity-70" : "text-muted"}`}>
        {[job.company, job.location].filter(Boolean).join(" · ")}
      </span>
      <span className={`mt-1 block font-mono text-[11px] ${selected ? "opacity-70" : "text-muted"}`}>
        {[label(job.seniority), label(job.contract_type)].join(" / ")}
      </span>
    </button>
  );
}

function posted(job: Job) {
  const value = job.published_at ?? job.published_text;
  if (!value) return "Unspecified";
  // Some boards put an ISO date in the free text field too.
  if (!/^\d{4}-\d{2}-\d{2}/.test(value)) return value;
  return new Date(value).toLocaleDateString(undefined, { dateStyle: "medium" });
}

function Description({ text }: { text: string | null }) {
  return (
    <div className="mt-5 space-y-3 text-[15px] leading-relaxed">
      {descriptionBlocks(text).map((block, index) => {
        if (block.kind === "heading") {
          return (
            <h2 key={index} className="pt-3 font-semibold tracking-tight first:pt-0">
              {block.text}
            </h2>
          );
        }
        const item = "relative -mt-1.5 pl-4 before:absolute before:left-0 before:content-['–']";
        return (
          <p key={index} className={block.kind === "item" ? item : undefined}>
            {block.text}
          </p>
        );
      })}
    </div>
  );
}

function JobDetail({ job, generations }: { job: Job; generations?: Generation[] }) {
  const [tailoring, setTailoring] = useState(false);
  const past = generations?.filter((item) => item.job_id === job.job_id) ?? [];
  const url = job.application_url ?? job.posting_url;
  const noteOpened = useNoteJobOpened(job.job_id);
  const application = useJobApplicationStatuses().get(job.job_id);
  const facts = [
    ["Level", label(job.seniority)],
    ["Contract", label(job.contract_type)],
    ["Source", label(job.source)],
    ["Posted", posted(job)],
  ];
  return (
    <article className="rise p-4 sm:p-6">
      <Eyebrow>{label(job.category)}</Eyebrow>
      <h1 className="mt-1 text-3xl font-bold tracking-tight text-balance">{job.title}</h1>
      <p className="mt-1 text-muted">
        {[job.company, job.location].filter(Boolean).join(" · ")}
      </p>
      <div className="sticky top-0 z-10 -mx-4 mt-2 flex flex-wrap items-center gap-2 border-b border-rule bg-paper px-4 py-3 sm:-mx-6 sm:px-6">
        <Button primary className="wiggle" onClick={() => setTailoring(true)}>
          Tailor my CV
        </Button>
        {url && (
          <a href={url} target="_blank" rel="noreferrer" className={ACTION} {...noteOpened}>
            Job posting
            <LeaveIcon />
          </a>
        )}
        {application && (
          <Link to="/applications" className="line text-sm text-muted hover:text-ink">
            Application: {label(application)}
          </Link>
        )}
      </div>
      <dl className="grid grid-cols-2 gap-x-6 gap-y-3 border-b border-rule py-4 lg:grid-cols-4">
        {facts.map(([name, value]) => (
          <div key={name} className="min-w-0">
            <dt>
              <Eyebrow>{name}</Eyebrow>
            </dt>
            <dd className="mt-0.5 truncate text-sm">{value}</dd>
          </div>
        ))}
      </dl>
      {past.length > 0 && (
        <section className="border-b border-rule py-4">
          <Eyebrow>Tailored for this job</Eyebrow>
          <ul className="mt-1">
            {past.map((generation) => (
              <li
                key={generation.generation_id}
                className="flex flex-wrap items-center justify-between gap-x-4 gap-y-1 py-1.5"
              >
                <Link
                  to={`/generations/${generation.generation_id}`}
                  className="flex items-center gap-3 text-sm underline-offset-4 hover:underline"
                >
                  {formatDate(generation.created_at)}
                  <StatusTag status={generation.status} />
                </Link>
                <GenerationLinks generation={generation} posting={false} />
              </li>
            ))}
          </ul>
        </section>
      )}
      <Description text={job.description} />
      {tailoring && <TailorDialog job={job} onClose={() => setTailoring(false)} />}
    </article>
  );
}
