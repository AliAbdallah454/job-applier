import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { Job } from "../api/client";
import { EditDiff } from "../components/EditDiff";
import { TailorDialog } from "../components/TailorDialog";
import { Jobs } from "../pages/Jobs";

const JOB: Job = {
  job_id: "job-1",
  title: "Junior Engineer",
  company: "Example",
  location: "Paris",
  description: "Build things",
  source: "greenhouse",
  posting_url: "https://example.com",
  application_url: null,
  published_at: null,
  published_text: null,
  seniority: "junior",
  contract_type: "permanent",
  category: "software_engineer",
  is_tech: true,
};
const CV = { cv_id: "a".repeat(32), filename: "cv.tex", size: 10, uploaded_at: "2026-01-01T00:00:00Z" };

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function server(routes: Record<string, () => Response>) {
  const calls: { method: string; path: string; body: string }[] = [];
  vi.stubGlobal("fetch", async (request: Request) => {
    const { pathname } = new URL(request.url);
    const body = await request.clone().text();
    calls.push({ method: request.method, path: pathname, body });
    const route = routes[`${request.method} ${pathname}`];
    return route ? route() : json({ detail: "Not found" }, 404);
  });
  return calls;
}

function mount(element: React.ReactNode) {
  return render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter>
        <Routes>
          <Route path="/" element={element} />
          <Route path="/generations/:id" element={<p>Status page</p>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

afterEach(() => vi.unstubAllGlobals());

describe("EditDiff", () => {
  it("marks added and removed words and shows why an edit was skipped", () => {
    const { container } = render(
      <EditDiff
        change={{
          find: "wrote tests",
          replace: "wrote automated tests",
          reason: "Testing",
          applied: false,
          error: "matches several places",
        }}
      />,
    );
    expect(container.querySelector("ins")).toHaveTextContent("automated");
    expect(screen.getByText("skipped")).toBeInTheDocument();
    expect(screen.getByText(/matches several places/)).toBeInTheDocument();
  });
});

describe("TailorDialog", () => {
  it("starts a generation with the chosen CV and opens its status page", async () => {
    const calls = server({
      "GET /api/cvs": () => json([CV]),
      "POST /api/generations": () => json({ generation_id: "g1", status: "queued" }, 201),
    });
    mount(<TailorDialog job={JOB} onClose={() => {}} />);
    await screen.findByText("cv.tex");
    await userEvent.click(screen.getByRole("button", { name: "Start tailoring", hidden: true }));
    expect(await screen.findByText("Status page")).toBeInTheDocument();
    const post = calls.find((call) => call.method === "POST");
    expect(JSON.parse(post!.body)).toEqual({ job_id: "job-1", cv_id: CV.cv_id });
  });

  it("links to the profile when the API says one is missing", async () => {
    server({
      "GET /api/cvs": () => json([CV]),
      "POST /api/generations": () => json({ detail: "Fill in your profile" }, 409),
    });
    mount(<TailorDialog job={JOB} onClose={() => {}} />);
    await screen.findByText("cv.tex");
    await userEvent.click(screen.getByRole("button", { name: "Start tailoring", hidden: true }));
    expect(await screen.findByText("Fill in your profile")).toBeInTheDocument();
    expect(screen.getByText("Go to your profile")).toBeInTheDocument();
  });

  it("cannot start without a CV", async () => {
    server({ "GET /api/cvs": () => json([]) });
    mount(<TailorDialog job={JOB} onClose={() => {}} />);
    await screen.findByText(/You have no CVs yet/);
    expect(screen.getByRole("button", { name: "Start tailoring", hidden: true })).toBeDisabled();
  });
});

describe("Jobs", () => {
  const base = {
    "GET /api/options": () =>
      json({
        categories: [],
        contract_types: [],
        seniorities: ["junior"],
        user_id: "ali",
        demo: false,
      }),
    "GET /api/generations": () => json([]),
  };

  it("shows the job detail with the tailor action", async () => {
    server({ ...base, "GET /api/jobs": () => json({ jobs: [JOB], next_cursor: null }) });
    mount(<Jobs />);
    expect(await screen.findByRole("heading", { name: "Junior Engineer" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Tailor my CV" })).toBeInTheDocument();
  });

  it("continues through empty filtered pages without a click", async () => {
    let page = 0;
    const calls = server({
      ...base,
      "GET /api/jobs": () => {
        page += 1;
        return page === 1
          ? json({ jobs: [], next_cursor: "more" })
          : json({ jobs: [JOB], next_cursor: null });
      },
    });
    mount(<Jobs />);
    expect(await screen.findByRole("heading", { name: "Junior Engineer" })).toBeInTheDocument();
    await waitFor(() =>
      expect(calls.filter((call) => call.path === "/api/jobs")).toHaveLength(2),
    );
  });

  it("shows the API's message when AWS is unreachable", async () => {
    server({ ...base, "GET /api/jobs": () => json({ detail: "Check your AWS login" }, 503) });
    mount(<Jobs />);
    expect(await screen.findByText("Check your AWS login")).toBeInTheDocument();
  });
});
