import createClient from "openapi-fetch";

import type { components, paths } from "./schema";

type Schemas = components["schemas"];
export type Job = Schemas["JobSummary"];
export type Cv = Schemas["Cv"];
export type Change = Schemas["Change"];
export type Generation = Schemas["Generation"];
export type GenerationDetail = Schemas["GenerationDetail"];
export type CandidateProfile = Schemas["CandidateProfile"];
export type Status = Generation["status"];

export const api = createClient<paths>({
  baseUrl: window.location.origin,
  // Read at call time so tests can replace fetch.
  fetch: (request) => globalThis.fetch(request),
});

export class ApiError extends Error {
  status: number;
  fields: Record<string, string>;

  constructor(status: number, message: string, fields: Record<string, string> = {}) {
    super(message);
    this.status = status;
    this.fields = fields;
  }
}

type Issue = { loc: (string | number)[]; msg: string };

/** FastAPI sends a string, or a list of issues whose loc starts with "body". */
export function toApiError(status: number, error: unknown): ApiError {
  const detail = (error as { detail?: string | Issue[] } | undefined)?.detail;
  if (Array.isArray(detail)) {
    const fields = Object.fromEntries(
      detail.map((issue) => [
        issue.loc.slice(1).join("."),
        issue.msg.replace(/^(Value error|String should match pattern .*)/, (text) =>
          text.startsWith("Value") ? "Invalid" : "Use YYYY or YYYY-MM",
        ),
      ]),
    );
    return new ApiError(status, "Some fields need attention", fields);
  }
  return new ApiError(status, detail ?? "The server did not respond. Is the API running?");
}

type Result<T> = { data?: T; error?: unknown; response: Response };

export async function unwrap<T>(call: Promise<Result<T>>): Promise<T> {
  const { data, error, response } = await call;
  if (!response.ok) throw toApiError(response.status, error);
  return data as T;
}

export function fileUrl(generationId: string, name: string, download = false) {
  const path = `/api/generations/${encodeURIComponent(generationId)}/files/${name}`;
  return download ? `${path}?download=true` : path;
}

/** An edit changes what fileUrl returns, so previews and downloads name the state they want. */
export function previewUrl(generationId: string, name: string, updatedAt: string) {
  return `${fileUrl(generationId, name)}?v=${encodeURIComponent(updatedAt)}`;
}

export function downloadUrl(generationId: string, name: string, updatedAt: string) {
  return `${fileUrl(generationId, name, true)}&v=${encodeURIComponent(updatedAt)}`;
}

export function versionFileUrl(
  generationId: string,
  number: number,
  name: string,
  download = false,
) {
  const path = `/api/generations/${encodeURIComponent(generationId)}/versions/${number}/files/${name}`;
  return download ? `${path}?download=true` : path;
}
