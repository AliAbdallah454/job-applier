import type { ButtonHTMLAttributes, ReactNode } from "react";

import { ApiError, type Status } from "../api/client";

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
  const tone = primary
    ? "bg-ink text-paper hover:opacity-80"
    : "flood border border-ink text-ink";
  return (
    <button
      type="button"
      className={`px-4 py-2 text-sm font-medium transition-opacity disabled:cursor-not-allowed disabled:opacity-40 ${tone} ${className}`}
      {...props}
    />
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

const STATUS_STYLE: Record<Status, string> = {
  queued: "border border-dashed border-muted text-muted",
  processing: "border border-ink text-ink",
  ready: "bg-ink text-paper",
  failed: "border border-ink text-ink line-through",
};

export function StatusTag({ status }: { status: Status }) {
  return (
    <span className={`inline-block px-1.5 py-0.5 font-mono text-[11px] ${STATUS_STYLE[status]}`}>
      {status}
    </span>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <p className="py-16 text-center text-sm text-muted">{children}</p>;
}
