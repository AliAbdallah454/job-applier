import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Link, MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { Generation, Job } from "../api/client";
import { EditDiff } from "../components/EditDiff";
import { TailorDialog } from "../components/TailorDialog";
import { formatDate } from "../components/ui";
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

type Routes = Record<string, () => Response | Promise<Response>>;

function server(routes: Routes) {
  const calls: { method: string; path: string; search: string; body: string }[] = [];
  vi.stubGlobal("fetch", async (input: Request | string) => {
    const request = typeof input === "string" ? new Request(new URL(input, location.href)) : input;
    const { pathname, search } = new URL(request.url);
    const body = await request.clone().text();
    calls.push({ method: request.method, path: pathname, search, body });
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
    const state = encodeURIComponent(GENERATION.updated_at);
    expect(cv).toHaveAttribute("href", `/api/generations/g1/files/cv_pdf?download=true&v=${state}`);
    expect(
      screen.getByRole("link", { name: "Download motivation letter for Junior Engineer" }),
    ).toHaveAttribute(
      "href",
      `/api/generations/g1/files/motivation_letter_pdf?download=true&v=${state}`,
    );
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
    previous_versions: [],
    starts_from_edit: false,
  };

  function Place() {
    return (
      <nav>
        <output aria-label="Current page">{useLocation().pathname}</output>
        <Link to="/generations/g1">To g1</Link>
        <Link to="/generations/g2">To g2</Link>
      </nav>
    );
  }

  function testClient() {
    return new QueryClient({ defaultOptions: { queries: { retry: false } } });
  }

  function open(generation: unknown, routes: Routes = {}, client = testClient()) {
    const calls = server({
      "GET /api/generations/g1": () => json(generation),
      "GET /api/generations/g1/files/cv_tex": () => new Response("\\section{Old}"),
      ...routes,
    });
    render(
      <QueryClientProvider client={client}>
        <MemoryRouter initialEntries={["/generations/g1"]}>
          <Place />
          <Routes>
            <Route path="/generations/:id" element={<GenerationPage />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    );
    return calls;
  }

  async function typeDraft(user: ReturnType<typeof userEvent.setup>) {
    await user.click(await screen.findByRole("tab", { name: "Edit LaTeX" }));
    const editor = await screen.findByLabelText("CV LaTeX source");
    await waitFor(() => expect(editor).toHaveValue("\\section{Old}"));
    await user.clear(editor);
    await user.type(editor, "new text");
    return editor;
  }

  async function saveEdit(user: ReturnType<typeof userEvent.setup>) {
    const editor = await typeDraft(user);
    await user.click(screen.getByRole("button", { name: "Save changes" }));
    return editor;
  }

  /** What the browser does when the user comes back to the tab. */
  function comeBackToTheTab() {
    act(() => void window.dispatchEvent(new Event("visibilitychange")));
  }

  function settle() {
    return act(() => new Promise((resolve) => setTimeout(resolve, 20)));
  }

  function postedStates(calls: ReturnType<typeof server>) {
    return calls
      .filter((call) => call.method === "POST")
      .map((call) => JSON.parse(call.body).updated_at);
  }

  it("cannot save an unchanged source", async () => {
    const user = userEvent.setup();
    open(detail);
    await user.click(await screen.findByRole("tab", { name: "Edit LaTeX" }));
    await waitFor(() => expect(screen.getByLabelText("CV LaTeX source")).toHaveValue("\\section{Old}"));
    expect(screen.getByRole("button", { name: "Save changes" })).toBeDisabled();
  });

  const SAVED_AT = "2026-01-02T03:04:05.678+00:00";
  const saved = {
    ...detail,
    updated_at: SAVED_AT,
    previous_versions: [{ number: 1, saved_at: detail.updated_at }],
  };

  it("updates the CV in place and stays on the Edit LaTeX tab", async () => {
    const user = userEvent.setup();
    const calls = open(detail, { "POST /api/generations/g1/edit": () => json(saved) });
    const preview = await screen.findByTitle("CV preview");
    expect(preview).toHaveAttribute(
      "src",
      `/api/generations/g1/files/cv_pdf?v=${encodeURIComponent(detail.updated_at)}`,
    );
    await saveEdit(user);
    expect(await screen.findByText(/edited by you/)).toBeInTheDocument();
    expect(calls.find((call) => call.method === "POST")?.body).toBe(
      JSON.stringify({ cv_latex: "new text", updated_at: detail.updated_at }),
    );

    expect(screen.getByLabelText("Current page")).toHaveTextContent("/generations/g1");
    expect(screen.getByTitle("CV preview")).toHaveAttribute(
      "src",
      `/api/generations/g1/files/cv_pdf?v=${encodeURIComponent(SAVED_AT)}`,
    );
    expect(screen.getByRole("link", { name: "Download cv for Junior Engineer" })).toHaveAttribute(
      "href",
      `/api/generations/g1/files/cv_pdf?download=true&v=${encodeURIComponent(SAVED_AT)}`,
    );
    expect(screen.getByRole("tab", { name: "Edit LaTeX" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByLabelText("CV LaTeX source")).toHaveValue("new text");
    await waitFor(() => expect(screen.getByRole("button", { name: "Save changes" })).toBeDisabled());
    expect(
      calls.filter((call) => call.method === "GET" && call.path === "/api/generations/g1"),
    ).toHaveLength(1);
  });

  it("asks for the CV source of the generation's current state", async () => {
    const user = userEvent.setup();
    const calls = open({ ...detail, updated_at: SAVED_AT });
    await user.click(await screen.findByRole("tab", { name: "Edit LaTeX" }));
    await waitFor(() =>
      expect(screen.getByLabelText("CV LaTeX source")).toHaveValue("\\section{Old}"),
    );
    expect(calls.find((call) => call.path.endsWith("/cv_tex"))?.search).toBe(
      `?v=${encodeURIComponent(SAVED_AT)}`,
    );
  });

  it("keeps the draft and says why when another save got there first", async () => {
    const user = userEvent.setup();
    open(detail, {
      "POST /api/generations/g1/edit": () =>
        json({ detail: "This CV was changed elsewhere. Save again to replace it." }, 409),
    });
    const editor = await saveEdit(user);
    expect(await screen.findByRole("alert")).toHaveTextContent("changed elsewhere");
    expect(editor).toHaveValue("new text");
    expect(screen.getByRole("button", { name: "Save changes" })).toBeEnabled();
    expect(screen.getByLabelText("Current page")).toHaveTextContent("/generations/g1");
  });

  it("shows the CV that got there first after a 409, then saves over it", async () => {
    const user = userEvent.setup();
    let current: unknown = detail;
    let posts = 0;
    const calls = open(detail, {
      "GET /api/generations/g1": () => json(current),
      "POST /api/generations/g1/edit": () => {
        posts += 1;
        if (posts > 1) return json({ ...saved, updated_at: "2026-01-03T00:00:00+00:00" });
        current = saved;
        return json({ detail: "This CV was changed elsewhere. Save again to replace it." }, 409);
      },
    });
    await saveEdit(user);
    expect(await screen.findByRole("alert")).toHaveTextContent("changed elsewhere");
    expect(screen.getByTitle("CV preview")).toHaveAttribute(
      "src",
      `/api/generations/g1/files/cv_pdf?v=${encodeURIComponent(SAVED_AT)}`,
    );
    expect(
      screen.getByRole("heading", { name: "Previous versions", hidden: true }),
    ).toBeInTheDocument();
    expect(await screen.findByLabelText("CV LaTeX source")).toHaveValue("new text");

    await waitFor(() => expect(screen.getByRole("button", { name: "Save changes" })).toBeEnabled());
    await user.click(screen.getByRole("button", { name: "Save changes" }));
    await waitFor(() => expect(postedStates(calls)).toEqual([detail.updated_at, SAVED_AT]));
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("sends the state a draft started from, even when a newer one has arrived", async () => {
    const user = userEvent.setup();
    let current: unknown = detail;
    const calls = open(detail, {
      "GET /api/generations/g1": () => json(current),
      "POST /api/generations/g1/edit": () =>
        json({ detail: "This CV was changed elsewhere. Save again to replace it." }, 409),
    });
    await typeDraft(user);
    current = saved;
    comeBackToTheTab();
    await waitFor(() => expect(screen.getByText(/edited by you/)).toBeInTheDocument());
    await waitFor(() => expect(screen.getByRole("button", { name: "Save changes" })).toBeEnabled());
    await user.click(screen.getByRole("button", { name: "Save changes" }));
    await screen.findByRole("alert");
    expect(postedStates(calls)).toEqual([detail.updated_at]);
  });

  it("does not let a read from before the save bring the old CV back", async () => {
    const user = userEvent.setup();
    let answer = (_: Response) => {};
    let slow = false;
    open(detail, {
      "GET /api/generations/g1": () =>
        slow ? new Promise<Response>((resolve) => (answer = resolve)) : json(detail),
      "POST /api/generations/g1/edit": () => json(saved),
    });
    await screen.findByTitle("CV preview");
    slow = true;
    comeBackToTheTab();
    await saveEdit(user);
    await screen.findByText(/edited by you/);

    answer(json(detail));
    await settle();
    expect(screen.getByText(/edited by you/)).toBeInTheDocument();
    expect(screen.getByLabelText("CV LaTeX source")).toHaveValue("new text");
  });

  it("follows a newer CV after a save, because the draft is gone", async () => {
    const user = userEvent.setup();
    let current = saved;
    open(detail, {
      "GET /api/generations/g1": () => json(current),
      "GET /api/generations/g1/files/cv_tex": () =>
        new Response(current === saved ? "\\section{Old}" : "\\section{Newer}"),
      "POST /api/generations/g1/edit": () => json(saved),
    });
    await saveEdit(user);
    await screen.findByText(/edited by you/);
    await settle();
    current = { ...saved, updated_at: "2026-01-03T00:00:00+00:00" };
    comeBackToTheTab();
    await waitFor(() =>
      expect(screen.getByLabelText("CV LaTeX source")).toHaveValue("\\section{Newer}"),
    );
  });

  it("refreshes the list of generations after a save", async () => {
    const user = userEvent.setup();
    const client = testClient();
    client.setQueryData(["generations"], [GENERATION]);
    open(detail, { "POST /api/generations/g1/edit": () => json(saved) }, client);
    await saveEdit(user);
    await waitFor(() => expect(client.getQueryState(["generations"])?.isInvalidated).toBe(true));
  });

  it("keeps an unsaved draft when a background refresh fails", async () => {
    const user = userEvent.setup();
    let down = false;
    open(detail, {
      "GET /api/generations/g1": () =>
        down ? json({ detail: "Check your AWS login" }, 503) : json(detail),
    });
    await typeDraft(user);
    down = true;
    comeBackToTheTab();
    await settle();
    expect(screen.getByLabelText("CV LaTeX source")).toHaveValue("new text");
  });

  it("cannot save when the CV source failed to load", async () => {
    const user = userEvent.setup();
    open(detail, {
      "GET /api/generations/g1/files/cv_tex": () => json({ detail: "Check your AWS login" }, 503),
    });
    await user.click(await screen.findByRole("tab", { name: "Edit LaTeX" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Check your AWS login");
    expect(screen.getByRole("button", { name: "Save changes" })).toBeDisabled();
  });

  it("downloads a source of the saved state", async () => {
    open({ ...saved, files: ["cv_tex"] });
    expect(await screen.findByRole("link", { name: "CV .tex" })).toHaveAttribute(
      "href",
      `/api/generations/g1/files/cv_tex?download=true&v=${encodeURIComponent(SAVED_AT)}`,
    );
  });

  it("keeps the draft beside the compile error when an edit fails", async () => {
    const user = userEvent.setup();
    open(detail, {
      "POST /api/generations/g1/edit": () =>
        json({ detail: "LaTeX failed to compile:\n! Undefined control sequence." }, 422),
    });
    const editor = await saveEdit(user);
    expect(await screen.findByRole("alert")).toHaveTextContent("! Undefined control sequence.");
    expect(editor).toHaveValue("new text");
    expect(screen.getByRole("button", { name: "Save changes" })).toBeEnabled();
  });

  it("does not carry a draft over to another generation", async () => {
    const user = userEvent.setup();
    open(detail, {
      "GET /api/generations/g2": () => json({ ...detail, generation_id: "g2", job_title: "Other" }),
      "GET /api/generations/g2/files/cv_tex": () => new Response("\\section{Other}"),
    });
    // Opened once first, because a generation not cached yet replaces the page with a skeleton.
    await user.click(await screen.findByRole("link", { name: "To g2" }));
    await screen.findByRole("heading", { name: "Other" });
    await user.click(screen.getByRole("link", { name: "To g1" }));
    await user.click(await screen.findByRole("tab", { name: "Edit LaTeX" }));
    const editor = await screen.findByLabelText("CV LaTeX source");
    await waitFor(() => expect(editor).toHaveValue("\\section{Old}"));
    await user.type(editor, " draft");
    await user.click(screen.getByRole("link", { name: "To g2" }));
    await screen.findByRole("heading", { name: "Other" });
    await waitFor(() =>
      expect(screen.getByLabelText("CV LaTeX source")).toHaveValue("\\section{Other}"),
    );
  });

  it("lists previous versions newest first and names the model's", async () => {
    open({
      ...detail,
      previous_versions: [
        { number: 1, saved_at: "2026-01-02T10:00:00Z" },
        { number: 2, saved_at: "2026-01-03T10:00:00Z" },
      ],
    });
    const heading = await screen.findByRole("heading", { name: "Previous versions" });
    const rows = within(heading.nextElementSibling as HTMLElement).getAllByRole("listitem");
    expect(rows).toHaveLength(2);
    expect(rows[0]).toHaveTextContent("Version 2");
    expect(rows[0]).not.toHaveTextContent("model");
    expect(rows[0]).toHaveTextContent(formatDate("2026-01-03T10:00:00Z"));
    expect(rows[1]).toHaveTextContent("Version 1 (the model's version)");
    expect(rows[1]).toHaveTextContent(formatDate("2026-01-02T10:00:00Z"));
    const pdf = screen.getByRole("link", { name: "Open the PDF of version 2" });
    expect(pdf).toHaveAttribute("href", "/api/generations/g1/versions/2/files/cv_pdf");
    expect(pdf).toHaveAttribute("target", "_blank");
    expect(screen.getByRole("link", { name: "Download the .tex of version 1" })).toHaveAttribute(
      "href",
      "/api/generations/g1/versions/1/files/cv_tex?download=true",
    );
    expect(screen.getByText(/edited by you/)).toBeInTheDocument();
  });

  it("shows no version history or edited label before the first edit", async () => {
    open({ ...detail, explanations: [{ change: "Named FastAPI", reason: "Job asks for it" }] });
    expect(await screen.findByRole("heading", { name: "Edits made" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Previous versions" })).not.toBeInTheDocument();
    expect(screen.queryByText(/edited by you/)).not.toBeInTheDocument();
  });

  it("says the listed edits are the model's once the CV was edited", async () => {
    open({ ...saved, explanations: [{ change: "Named FastAPI", reason: "Job asks for it" }] });
    expect(
      await screen.findByRole("heading", { name: "Edits made by the model" }),
    ).toBeInTheDocument();
  });

  it("marks a row from the old edit flow as edited before it has versions", async () => {
    open({
      ...detail,
      starts_from_edit: true,
      explanations: [{ change: "Named FastAPI", reason: "Job asks for it" }],
    });
    expect(await screen.findByText(/edited by you/)).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Edits made by the model" })).toBeInTheDocument();
  });

  it("does not call the first CV of a row from the old edit flow the model's", async () => {
    open({ ...saved, starts_from_edit: true });
    const heading = await screen.findByRole("heading", { name: "Previous versions" });
    expect(heading.nextElementSibling).toHaveTextContent("Version 1");
    expect(heading.nextElementSibling).not.toHaveTextContent("model");
  });

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
