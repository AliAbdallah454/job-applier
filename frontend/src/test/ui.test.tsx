import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
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
import { SystemPrompts } from "../pages/SystemPrompts";

// PDF.js cannot draw in jsdom; PdfPreview has its own tests, and this keeps what pages depend on.
vi.mock("../components/PdfPreview", () => ({
  PdfPreview: ({
    title,
    url,
    onLocate,
  }: {
    title: string;
    url: string;
    onLocate: (position: { page: number; x: number; y: number }, text: string) => void;
  }) => (
    <>
      <iframe title={title} src={url} />
      <button type="button" onClick={() => onLocate({ page: 1, x: 200, y: 100 }, "")}>
        Double-click the CV
      </button>
    </>
  ),
}));

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
  language: "en",
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
  it("starts a generation with the chosen CV and stays on the page", async () => {
    const calls = server({
      "GET /api/cvs": () => json([CV]),
      "GET /api/system-prompts": () => json([]),
      "POST /api/generations": () => json({ generation_id: "g1", status: "queued" }, 201),
    });
    const onClose = vi.fn();
    mount(<TailorDialog job={JOB} onClose={onClose} />);
    await screen.findByText("cv.tex");
    await userEvent.click(screen.getByRole("button", { name: "Start tailoring", hidden: true }));
    await waitFor(() => expect(onClose).toHaveBeenCalled());
    expect(screen.queryByText("Status page")).not.toBeInTheDocument();
    const post = calls.find((call) => call.method === "POST");
    expect(JSON.parse(post!.body)).toEqual({
      job_id: "job-1",
      cv_id: CV.cv_id,
      language: "en",
    });
  });

  it("sends the chosen model, defaulting to the first", async () => {
    const calls = server({
      "GET /api/cvs": () => json([CV]),
      "GET /api/system-prompts": () => json([]),
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
    await waitFor(() => expect(calls.some((call) => call.method === "POST")).toBe(true));
    const post = calls.find((call) => call.method === "POST");
    expect(JSON.parse(post!.body).model_id).toBe("sonnet");
  });

  it("sends the chosen language and says the changes stay in English", async () => {
    const calls = server({
      "GET /api/cvs": () => json([CV]),
      "GET /api/system-prompts": () => json([]),
      "GET /api/options": () =>
        json({
          models: [],
          languages: [
            { id: "en", label: "English" },
            { id: "fr", label: "French" },
          ],
        }),
      "POST /api/generations": () => json({ generation_id: "g1", status: "queued" }, 201),
    });
    mount(<TailorDialog job={JOB} onClose={() => {}} />);
    const picker = await screen.findByRole("combobox", { name: /Which language/, hidden: true });
    expect(picker).toHaveValue("en");
    expect(screen.getByText(/changes stays in English/)).toBeInTheDocument();
    await screen.findByText("cv.tex");
    await userEvent.selectOptions(picker, "fr");
    await userEvent.click(screen.getByRole("button", { name: "Start tailoring", hidden: true }));
    await waitFor(() => expect(calls.some((call) => call.method === "POST")).toBe(true));
    const post = calls.find((call) => call.method === "POST");
    expect(JSON.parse(post!.body).language).toBe("fr");
  });

  it("links to the profile when the API says one is missing", async () => {
    server({
      "GET /api/cvs": () => json([CV]),
      "GET /api/system-prompts": () => json([]),
      "POST /api/generations": () => json({ detail: "Fill in your profile" }, 409),
    });
    mount(<TailorDialog job={JOB} onClose={() => {}} />);
    await screen.findByText("cv.tex");
    await userEvent.click(screen.getByRole("button", { name: "Start tailoring", hidden: true }));
    expect(await screen.findByText("Fill in your profile")).toBeInTheDocument();
    expect(screen.getByText("Go to your profile")).toBeInTheDocument();
  });

  const PROMPTS = [
    { prompt_id: "p1", name: "Backend", text: "Plain tone", is_default: false },
    { prompt_id: "p2", name: "Data", text: "Lead with SQL", is_default: true },
  ];

  async function startWith(prompts: typeof PROMPTS, choice?: string) {
    const calls = server({
      "GET /api/cvs": () => json([CV]),
      "GET /api/system-prompts": () => json(prompts),
      "POST /api/generations": () => json({ generation_id: "g1", status: "queued" }, 201),
    });
    mount(<TailorDialog job={JOB} onClose={() => {}} />);
    await screen.findByText("cv.tex");
    if (prompts.length) {
      const picker = await screen.findByRole("combobox", {
        name: "Which system prompt?",
        hidden: true,
      });
      if (choice !== undefined) await userEvent.selectOptions(picker, choice);
    }
    await userEvent.click(screen.getByRole("button", { name: "Start tailoring", hidden: true }));
    await waitFor(() => expect(calls.some((call) => call.method === "POST")).toBe(true));
    return JSON.parse(calls.find((call) => call.method === "POST")!.body);
  }

  it("selects the default system prompt", async () => {
    expect((await startWith(PROMPTS)).system_prompt_id).toBe("p2");
  });

  it("marks the default system prompt in the list", async () => {
    server({
      "GET /api/cvs": () => json([CV]),
      "GET /api/system-prompts": () => json(PROMPTS),
    });
    mount(<TailorDialog job={JOB} onClose={() => {}} />);
    const picker = await screen.findByRole("combobox", {
      name: "Which system prompt?",
      hidden: true,
    });
    expect(picker).toHaveValue("p2");
    expect(within(picker).getByText("Data (default)")).toBeInTheDocument();
  });

  it("sends another system prompt when one is picked", async () => {
    expect((await startWith(PROMPTS, "p1")).system_prompt_id).toBe("p1");
  });

  it("can tailor without the default system prompt", async () => {
    expect(await startWith(PROMPTS, "")).not.toHaveProperty("system_prompt_id");
  });

  it("uses no system prompt when none is the default", async () => {
    const none = PROMPTS.map((prompt) => ({ ...prompt, is_default: false }));
    expect(await startWith(none)).not.toHaveProperty("system_prompt_id");
  });

  it("does not ask about system prompts when there are none", async () => {
    await startWith([]);
    expect(screen.queryByRole("combobox", { name: "Which system prompt?", hidden: true })).toBeNull();
  });

  it("cannot start until the system prompts have loaded", async () => {
    server({ "GET /api/cvs": () => json([CV]) });
    mount(<TailorDialog job={JOB} onClose={() => {}} />);
    await screen.findByText("cv.tex");
    await screen.findByRole("alert", { hidden: true });
    expect(screen.getByRole("button", { name: "Start tailoring", hidden: true })).toBeDisabled();
  });

  it("cannot start without a CV", async () => {
    server({ "GET /api/cvs": () => json([]) });
    mount(<TailorDialog job={JOB} onClose={() => {}} />);
    await screen.findByText(/You have no CVs yet/);
    expect(screen.getByRole("button", { name: "Start tailoring", hidden: true })).toBeDisabled();
  });
});

describe("SystemPrompts", () => {
  const PROMPT = { prompt_id: "p1", name: "Backend", text: "Plain tone", is_default: false };

  it("adds a system prompt and clears the form", async () => {
    const calls = server({
      "GET /api/system-prompts": () => json([]),
      "POST /api/system-prompts": () => json(PROMPT, 201),
    });
    mount(<SystemPrompts />);
    expect(await screen.findByText("No system prompts yet.")).toBeInTheDocument();
    const name = screen.getByRole("textbox", { name: "Name" });
    await userEvent.type(name, "Backend");
    await userEvent.type(screen.getByRole("textbox", { name: /keep in mind/ }), "Plain tone");
    await userEvent.click(screen.getByRole("checkbox"));
    await userEvent.click(screen.getByRole("button", { name: "Add system prompt" }));
    await waitFor(() => expect(screen.getByRole("textbox", { name: "Name" })).toHaveValue(""));
    const post = calls.find((call) => call.method === "POST");
    expect(JSON.parse(post!.body)).toEqual({
      name: "Backend",
      text: "Plain tone",
      is_default: true,
    });
  });

  it("makes a system prompt the default without changing it", async () => {
    const calls = server({
      "GET /api/system-prompts": () => json([PROMPT]),
      "PUT /api/system-prompts/p1": () => json({ ...PROMPT, is_default: true }),
    });
    mount(<SystemPrompts />);
    await userEvent.click(await screen.findByRole("button", { name: "Make default" }));
    await waitFor(() => expect(calls.some((call) => call.method === "PUT")).toBe(true));
    const put = calls.find((call) => call.method === "PUT");
    expect(JSON.parse(put!.body)).toEqual({
      name: "Backend",
      text: "Plain tone",
      is_default: true,
    });
  });

  it("shows which one is the default and can remove that", async () => {
    server({ "GET /api/system-prompts": () => json([{ ...PROMPT, is_default: true }]) });
    mount(<SystemPrompts />);
    expect(await screen.findByText("Default")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Remove default" })).toBeInTheDocument();
  });

  it("edits a system prompt in its place", async () => {
    const calls = server({
      "GET /api/system-prompts": () => json([PROMPT]),
      "PUT /api/system-prompts/p1": () => json(PROMPT),
    });
    mount(<SystemPrompts />);
    await userEvent.click(await screen.findByRole("button", { name: "Edit" }));
    const text = screen.getAllByRole("textbox", { name: /keep in mind/ })[1];
    expect(text).toHaveValue("Plain tone");
    await userEvent.type(text, " always");
    await userEvent.click(screen.getByRole("button", { name: "Save changes" }));
    await waitFor(() => expect(screen.queryByRole("button", { name: "Save changes" })).toBeNull());
    const put = calls.find((call) => call.method === "PUT");
    expect(JSON.parse(put!.body).text).toBe("Plain tone always");
  });

  it("asks before deleting", async () => {
    const calls = server({
      "GET /api/system-prompts": () => json([PROMPT]),
      "DELETE /api/system-prompts/p1": () => new Response(null, { status: 204 }),
    });
    mount(<SystemPrompts />);
    await userEvent.click(await screen.findByRole("button", { name: "Delete" }));
    expect(calls.some((call) => call.method === "DELETE")).toBe(false);
    await userEvent.click(screen.getByRole("button", { name: "Delete" }));
    await waitFor(() => expect(calls.some((call) => call.method === "DELETE")).toBe(true));
  });

  it("shows why a prompt was refused", async () => {
    server({
      "GET /api/system-prompts": () => json([]),
      "POST /api/system-prompts": () => json({ detail: "That name is taken" }, 409),
    });
    mount(<SystemPrompts />);
    await userEvent.type(await screen.findByRole("textbox", { name: "Name" }), "Backend");
    await userEvent.type(screen.getByRole("textbox", { name: /keep in mind/ }), "Plain");
    await userEvent.click(screen.getByRole("button", { name: "Add system prompt" }));
    expect(await screen.findByText("That name is taken")).toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "Name" })).toHaveValue("Backend");
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
    revisions: { cv: 0, motivation_letter: 0, cover_letter: 0 },
    starts_from_edit: false,
    edited: false,
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
      "GET /api/generations/g1/files/motivation_letter_tex": () => new Response("\\section{Why}"),
      "GET /api/generations/g1/files/cover_letter_tex": () => new Response("\\section{Fit}"),
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
      .map((call) => JSON.parse(call.body).revision);
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
    edited: true,
    previous_versions: [{ document: "cv", number: 1, saved_at: detail.updated_at, by: "model" }],
    revisions: { ...detail.revisions, cv: 1 },
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
      JSON.stringify({ document: "cv", latex: "new text", revision: 0 }),
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

  it("edits the document shown in the preview and keeps each one's draft", async () => {
    const user = userEvent.setup();
    const calls = open(detail, {
      "POST /api/generations/g1/edit": () =>
        json({ ...detail, revisions: { ...detail.revisions, cover_letter: 1 } }),
    });
    await user.click(await screen.findByRole("tab", { name: "Edit LaTeX" }));
    const cv = await screen.findByLabelText("CV LaTeX source");
    await waitFor(() => expect(cv).toHaveValue("\\section{Old}"));
    await user.clear(cv);
    await user.type(cv, "cv draft");

    await user.click(screen.getByRole("tab", { name: "Cover letter" }));
    expect(screen.getByTitle("Cover letter preview")).toHaveAttribute(
      "src",
      `/api/generations/g1/files/cover_letter_pdf?v=${encodeURIComponent(detail.updated_at)}`,
    );
    const letter = screen.getByLabelText("Cover letter LaTeX source");
    expect(letter).toBeVisible();
    expect(cv).not.toBeVisible();
    await waitFor(() => expect(letter).toHaveValue("\\section{Fit}"));
    await user.clear(letter);
    await user.type(letter, "letter text");
    const buttons = screen.getAllByRole("button", { name: "Save changes" });
    await user.click(buttons[buttons.length - 1]);

    await waitFor(() =>
      expect(calls.find((call) => call.method === "POST")?.body).toBe(
        JSON.stringify({ document: "cover_letter", latex: "letter text", revision: 0 }),
      ),
    );
    await user.click(screen.getByRole("tab", { name: "CV" }));
    expect(screen.getByLabelText("CV LaTeX source")).toHaveValue("cv draft");
  });

  it("lists the previous versions of the document being shown", async () => {
    const user = userEvent.setup();
    open({
      ...detail,
      previous_versions: [
        { document: "motivation_letter", number: 1, saved_at: "2026-01-02T10:00:00Z", by: "model" },
      ],
      revisions: { ...detail.revisions, motivation_letter: 1 },
    });
    await screen.findByTitle("CV preview");
    expect(screen.queryByRole("heading", { name: "Previous versions" })).not.toBeInTheDocument();

    await user.click(screen.getByRole("tab", { name: "Motivation letter" }));
    expect(screen.getByRole("link", { name: "Open the PDF of version 1" })).toHaveAttribute(
      "href",
      "/api/generations/g1/versions/motivation_letter/1/files/motivation_letter_pdf",
    );
  });

  it("offers no cover letter on a generation from before they existed", async () => {
    open({ ...detail, revisions: { cv: 0, motivation_letter: 0 } });
    expect(await screen.findByRole("tab", { name: "Motivation letter" })).toBeInTheDocument();
    expect(screen.queryByRole("tab", { name: "Cover letter" })).not.toBeInTheDocument();
  });

  it("asks for the CV source of the generation's current state", async () => {
    const user = userEvent.setup();
    const calls = open(saved);
    await user.click(await screen.findByRole("tab", { name: "Edit LaTeX" }));
    await waitFor(() =>
      expect(screen.getByLabelText("CV LaTeX source")).toHaveValue("\\section{Old}"),
    );
    expect(calls.find((call) => call.path.endsWith("/cv_tex"))?.search).toBe("?v=1");
  });

  it("keeps the draft and says why when another save got there first", async () => {
    const user = userEvent.setup();
    open(detail, {
      "POST /api/generations/g1/edit": () =>
        json({ detail: "This document was changed elsewhere. Save again to replace it." }, 409),
    });
    const editor = await saveEdit(user);
    expect(await screen.findByRole("alert")).toHaveTextContent("changed elsewhere");
    expect(editor).toHaveValue("new text");
    expect(screen.getByRole("button", { name: "Save changes" })).toBeEnabled();
    expect(screen.getByLabelText("Current page")).toHaveTextContent("/generations/g1");
  });

  it("opens the editor on the line a double-click in the CV points to", async () => {
    const user = userEvent.setup();
    const calls = open(detail, {
      "GET /api/generations/g1/source-line": () => json({ line: 2 }),
      "GET /api/generations/g1/files/cv_tex": () => new Response("first\nsecond\nthird"),
    });
    await user.click(await screen.findByRole("button", { name: "Double-click the CV" }));

    const editor = await screen.findByLabelText("CV LaTeX source");
    await waitFor(() => expect(editor).toHaveFocus());
    expect(screen.getByRole("tab", { name: "Edit LaTeX" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    const textarea = editor as HTMLTextAreaElement;
    expect(textarea.value.slice(textarea.selectionStart, textarea.selectionEnd)).toBe("second");
    expect(calls.find(({ path }) => path.endsWith("/source-line"))?.search).toBe(
      "?page=1&x=200&y=100",
    );
  });

  it("stays on the review and says why when a CV has no position data", async () => {
    const user = userEvent.setup();
    open(detail, {
      "GET /api/generations/g1/source-line": () =>
        json({ detail: "This CV has no position data yet. Save an edit to add it." }, 404),
    });
    await user.click(await screen.findByRole("button", { name: "Double-click the CV" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("no position data");
    expect(screen.getByRole("tab", { name: "Review" })).toHaveAttribute("aria-selected", "true");
  });

  it("shows the CV that got there first after a 409, then saves over it", async () => {
    const user = userEvent.setup();
    let current: unknown = detail;
    let posts = 0;
    const calls = open(detail, {
      "GET /api/generations/g1": () => json(current),
      "POST /api/generations/g1/edit": () => {
        posts += 1;
        if (posts > 1) return json({ ...saved, revisions: { ...detail.revisions, cv: 2 }, updated_at: "2026-01-03T00:00:00+00:00" });
        current = saved;
        return json({ detail: "This document was changed elsewhere. Save again to replace it." }, 409);
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
    await waitFor(() => expect(postedStates(calls)).toEqual([0, 1]));
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("sends the state a draft started from, even when a newer one has arrived", async () => {
    const user = userEvent.setup();
    let current: unknown = detail;
    const calls = open(detail, {
      "GET /api/generations/g1": () => json(current),
      "POST /api/generations/g1/edit": () =>
        json({ detail: "This document was changed elsewhere. Save again to replace it." }, 409),
    });
    await typeDraft(user);
    current = saved;
    comeBackToTheTab();
    await waitFor(() => expect(screen.getByText(/edited by you/)).toBeInTheDocument());
    await waitFor(() => expect(screen.getByRole("button", { name: "Save changes" })).toBeEnabled());
    await user.click(screen.getByRole("button", { name: "Save changes" }));
    await screen.findByRole("alert");
    expect(postedStates(calls)).toEqual([0]);
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
    current = { ...saved, revisions: { ...detail.revisions, cv: 2 }, updated_at: "2026-01-03T00:00:00+00:00" };
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
      edited: true,
      previous_versions: [
        { document: "cv", number: 1, saved_at: "2026-01-02T10:00:00Z", by: "model" },
        { document: "cv", number: 2, saved_at: "2026-01-03T10:00:00Z", by: "you" },
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
    expect(pdf).toHaveAttribute("href", "/api/generations/g1/versions/cv/2/files/cv_pdf");
    expect(pdf).toHaveAttribute("target", "_blank");
    expect(screen.getByRole("link", { name: "Download the .tex of version 1" })).toHaveAttribute(
      "href",
      "/api/generations/g1/versions/cv/1/files/cv_tex?download=true",
    );
    expect(screen.getByText(/edited by you/)).toBeInTheDocument();
  });

  it("names French on a French generation", async () => {
    open({ ...detail, language: "fr" });
    expect(await screen.findByText(/\/ French/)).toBeInTheDocument();
  });

  async function openRegenerate(user: ReturnType<typeof userEvent.setup>) {
    await user.click(await screen.findByRole("button", { name: "Regenerate" }));
    return screen.findByRole("textbox", { name: /fix in this version/, hidden: true });
  }

  it("regenerates this entry with comments, without leaving it", async () => {
    const user = userEvent.setup();
    let current: unknown = detail;
    const calls = open(detail, {
      "GET /api/generations/g1": () => json(current),
      "POST /api/generations/g1/regenerate": () => {
        current = { ...detail, status: "queued" };
        return json(current, 202);
      },
    });
    const box = await openRegenerate(user);
    fireEvent.change(box, { target: { value: "  Lead with AWS  " } });
    const buttons = screen.getAllByRole("button", { name: "Regenerate", hidden: true });
    await user.click(buttons[buttons.length - 1]);
    expect(await screen.findByText(/Waiting for a worker/)).toBeInTheDocument();
    expect(screen.getByLabelText("Current page")).toHaveTextContent("/generations/g1");
    expect(calls.find((call) => call.path.endsWith("/regenerate"))?.body).toBe(
      JSON.stringify({ instructions: "Lead with AWS" }),
    );
    expect(screen.queryByRole("dialog", { hidden: true })).not.toBeInTheDocument();
  });

  it("says a failed regeneration left the documents unchanged", async () => {
    open({
      ...detail,
      regeneration_error: "LaTeX failed",
      files: [...detail.files, "compilation_log"],
    });
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("your CV and letters are unchanged: LaTeX failed");
    expect(within(alert).getByRole("link", { name: "Compile log .log" })).toBeInTheDocument();
    expect(screen.getByTitle("CV preview")).toBeInTheDocument();
  });

  it("says who made each previous version", async () => {
    open({
      ...saved,
      previous_versions: [
        { document: "cv", number: 1, saved_at: "2026-01-01T10:00:00Z", by: "model" },
        { document: "cv", number: 2, saved_at: "2026-01-02T10:00:00Z", by: "you" },
        { document: "cv", number: 3, saved_at: "2026-01-03T10:00:00Z", by: "model" },
      ],
    });
    expect(await screen.findByText(/Version 2 \(your edit\)/)).toBeInTheDocument();
    expect(screen.getByText(/Version 3 \(the model's version\)/)).toBeInTheDocument();
  });

  it("does not call a regenerated entry edited by you", async () => {
    open({ ...saved, edited: false });
    await screen.findByTitle("CV preview");
    expect(screen.queryByText(/edited by you/)).not.toBeInTheDocument();
  });

  it("can regenerate without instructions", async () => {
    const user = userEvent.setup();
    const calls = open(detail, {
      "POST /api/generations/g1/regenerate": () => json({ ...detail, generation_id: "g2" }, 201),
    });
    await openRegenerate(user);
    const buttons = screen.getAllByRole("button", { name: "Regenerate", hidden: true });
    await user.click(buttons[buttons.length - 1]);
    await waitFor(() => expect(calls.some((call) => call.method === "POST")).toBe(true));
    expect(calls.find((call) => call.method === "POST")?.body).toBe("{}");
  });

  it("keeps the dialog and what was typed when regenerating fails", async () => {
    const user = userEvent.setup();
    open(detail, {
      "POST /api/generations/g1/regenerate": () => json({ detail: "Try again later" }, 503),
    });
    const box = await openRegenerate(user);
    fireEvent.change(box, { target: { value: "Shorter" } });
    const buttons = screen.getAllByRole("button", { name: "Regenerate", hidden: true });
    await user.click(buttons[buttons.length - 1]);
    expect(await screen.findByText("Try again later")).toBeInTheDocument();
    expect(box).toHaveValue("Shorter");
  });

  it("shows what was asked for on a regenerated version", async () => {
    open({ ...detail, instructions: "Lead with AWS" });
    expect(await screen.findByText("Lead with AWS")).toBeInTheDocument();
    expect(screen.getByText("Your comments")).toBeInTheDocument();
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
      edited: true,
      explanations: [{ change: "Named FastAPI", reason: "Job asks for it" }],
    });
    expect(await screen.findByText(/edited by you/)).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Edits made by the model" })).toBeInTheDocument();
  });

  it("does not call the first CV of a row from the old edit flow the model's", async () => {
    open({
      ...saved,
      starts_from_edit: true,
      previous_versions: [
        { document: "cv", number: 1, saved_at: detail.updated_at, by: "you" },
      ],
    });
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
