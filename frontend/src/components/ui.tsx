import type { ButtonHTMLAttributes, ReactNode } from "react";

import { ApiError, type Status } from "../api/client";

/** A link that reads as a secondary button. */
export const ACTION =
  "flood inline-flex items-center gap-1.5 border border-rule px-3 py-1.5 text-sm font-medium whitespace-nowrap hover:border-ink active:translate-y-px";
export const TAB =
  "line text-muted hover:text-ink aria-[current=page]:text-ink aria-pressed:text-ink aria-selected:text-ink";

export function label(value: string | null | undefined) {
  if (!value) return "Unspecified";
  const text = value.replaceAll("_", " ");
  return text[0].toUpperCase() + text.slice(1);
}

export function formatDate(iso: string) {
  return new Date(iso).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

export function Button({
  primary,
  className = "",
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & { primary?: boolean }) {
  const tone = primary ? "bg-ink text-paper hover:opacity-80" : "flood text-ink";
  return (
    <button
      type="button"
      className={`border border-ink px-3.5 py-1.5 text-sm font-medium whitespace-nowrap transition-opacity not-disabled:active:translate-y-px disabled:cursor-not-allowed disabled:opacity-40 ${tone} ${className}`}
      {...props}
    />
  );
}

export function DownloadIcon() {
  return (
    <svg aria-hidden viewBox="0 0 12 12" className="size-3 fill-none stroke-current stroke-[1.4]">
      <path d="M6 1v7M3 5.5 6 8.5l3-3M1.5 11h9" />
    </svg>
  );
}

export function LeaveIcon() {
  return (
    <svg aria-hidden viewBox="0 0 12 12" className="size-3 fill-none stroke-current stroke-[1.4]">
      <path d="M2.5 9.5 9.5 2.5M3.5 2.5h6v6" />
    </svg>
  );
}

export function Eyebrow({ children }: { children: ReactNode }) {
  return <p className="font-mono text-[11px] tracking-wider text-muted uppercase">{children}</p>;
}

export function ErrorNote({ error }: { error: unknown }) {
  if (!error) return null;
  const message = error instanceof ApiError ? error.message : "Something went wrong.";
  return (
    <p role="alert" className="border-l-2 border-ink pl-3 text-sm">
      {message}
    </p>
  );
}

const STATUS_MARK: Record<Status, string> = {
  queued: "border border-current",
  processing: "pulse bg-current",
  ready: "bg-current",
  failed: "",
};

/** Marks use the text colour so a tag also reads on a selected, inked row. */
export function StatusTag({ status }: { status: Status }) {
  return (
    <span className="inline-flex shrink-0 items-center gap-1.5 font-mono text-[11px]">
      {status === "failed" ? (
        <span aria-hidden className="w-1.5 text-center leading-none">
          ×
        </span>
      ) : (
        <span aria-hidden className={`size-1.5 ${STATUS_MARK[status]}`} />
      )}
      {status}
    </span>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <p className="py-16 text-center text-sm text-muted">{children}</p>;
}

export function Skeleton({ rows = 6 }: { rows?: number }) {
  return (
    <div role="status" aria-label="Loading">
      {Array.from({ length: rows }, (_, index) => (
        <div key={index} className="space-y-2 border-b border-rule px-4 py-4 sm:px-6">
          <div className="shimmer h-3.5 w-3/5" />
          <div className="shimmer h-2.5 w-2/5" />
        </div>
      ))}
    </div>
  );
}
