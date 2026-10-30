import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { Generation, Job } from "../api/client";
import { EditDiff } from "../components/EditDiff";
import { TailorDialog } from "../components/TailorDialog";
import { GenerationPage } from "../pages/Generation";
import { History } from "../pages/History";
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

const GENERATION: Generation = {
  generation_id: "g1",
  job_id: "job-1",
  job_title: "Junior Engineer",
  company: "Example",
  job_url: "https://example.com/job",
  model_id: "model",
  status: "ready",
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
  review_required: true,
  files: ["cv_pdf", "motivation_letter_pdf"],
};

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
  it("marks added and removed words", () => {
    const { container } = render(
      <EditDiff change={{ before: "wrote tests", after: "wrote automated tests" }} />,
    );
    expect(container.querySelector("ins")).toHaveTextContent("automated");
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

  it("sends the chosen model, defaulting to the first", async () => {
    const calls = server({
      "GET /api/cvs": () => json([CV]),
      "GET /api/options": () =>
        json({
          models: [
            { id: "nova", label: "Nova" },
            { id: "sonnet", label: "Sonnet" },
          ],
        }),
      "POST /api/generations": () => json({ generation_id: "g1", status: "queued" }, 201),
    });
    mount(<TailorDialog job={JOB} onClose={() => {}} />);
    const picker = await screen.findByRole("combobox", { name: "Which model?", hidden: true });
    expect(picker).toHaveValue("nova");
    await screen.findByText("cv.tex");
    await userEvent.selectOptions(picker, "sonnet");
    await userEvent.click(screen.getByRole("button", { name: "Start tailoring", hidden: true }));
    await screen.findByText("Status page");
    const post = calls.find((call) => call.method === "POST");
    expect(JSON.parse(post!.body).model_id).toBe("sonnet");
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

describe("History", () => {
  it("offers both downloads and the posting without opening a generation", async () => {
    server({ "GET /api/generations": () => json([GENERATION]) });
    mount(<History />);
    const cv = await screen.findByRole("link", { name: "Download cv for Junior Engineer" });
    expect(cv).toHaveAttribute("href", "/api/generations/g1/files/cv_pdf?download=true");
    expect(
      screen.getByRole("link", { name: "Download motivation letter for Junior Engineer" }),
    ).toHaveAttribute("href", "/api/generations/g1/files/motivation_letter_pdf?download=true");
    expect(screen.getByRole("link", { name: "Job posting for Junior Engineer" })).toHaveAttribute(
      "href",
      "https://example.com/job",
    );
  });

  it("hides what does not exist yet", async () => {
    const waiting = { ...GENERATION, status: "processing", files: [], job_url: null };
    server({ "GET /api/generations": () => json([waiting]) });
    mount(<History />);
    await screen.findByText("Junior Engineer");
    expect(screen.queryByRole("link", { name: /Download/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /Job posting/ })).not.toBeInTheDocument();
  });

  it("filters by status and by company", async () => {
    const failed = { ...GENERATION, generation_id: "g2", job_title: "Data", status: "failed" };
    server({ "GET /api/generations": () => json([GENERATION, { ...failed, company: "Other" }]) });
    mount(<History />);
    await screen.findByText("Data");
    await userEvent.click(screen.getByRole("button", { name: /Failed/ }));
    expect(screen.queryByText("Junior Engineer")).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /All/ }));
    await userEvent.type(screen.getByLabelText("Filter by job or company"), "other");
    expect(screen.queryByText("Junior Engineer")).not.toBeInTheDocument();
    expect(screen.getByText("Data")).toBeInTheDocument();
  });
});

describe("GenerationPage", () => {
  const detail = {
    ...GENERATION,
    retryable: false,
    keywords: [],
    changes: [],
    explanations: [],
  };

  function open(generation: unknown) {
    server({ "GET /api/generations/g1": () => json(generation) });
    return render(
      <QueryClientProvider client={new QueryClient()}>
        <MemoryRouter initialEntries={["/generations/g1"]}>
          <Routes>
            <Route path="/generations/:id" element={<GenerationPage />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    );
  }

  it("does not celebrate when an old generation is reopened", async () => {
    open(detail);
    await screen.findByRole("heading", { name: "Junior Engineer" });
    expect(screen.queryByText("Fits you perfectly!")).not.toBeInTheDocument();
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

  it("offers the downloads of an earlier tailored CV but not a second posting link", async () => {
    server({
      ...base,
      "GET /api/jobs": () => json({ jobs: [JOB], next_cursor: null }),
      "GET /api/generations": () => json([GENERATION]),
    });
    mount(<Jobs />);
    expect(
      await screen.findByRole("link", { name: "Download cv for Junior Engineer" }),
    ).toBeInTheDocument();
    expect(screen.getAllByRole("link", { name: /posting/i })).toHaveLength(1);
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
