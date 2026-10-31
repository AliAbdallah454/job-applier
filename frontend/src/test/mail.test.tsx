import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { AlertJob, Application } from "../api/client";
import { Applications } from "../pages/Applications";
import { JobEmails } from "../pages/JobEmails";

type Routes = Record<string, (body: string) => Response>;

const OK_STATUS = { problem: null, syncing: false, last_synced_at: "2026-10-01T10:00:00Z" };
const APPLICATION: Application = {
  application_id: "a1",
  company: "Acme",
  role: "Engineer",
  status: "interview",
  updated_at: "2026-10-02T10:00:00Z",
  generation_id: "g1",
  events: [
    { status: "applied", subject: "Thanks for applying", received_at: "2026-10-01T10:00:00Z" },
    { status: "interview", subject: "Let us talk", received_at: "2026-10-02T10:00:00Z" },
  ],
};
const JOB: AlertJob = {
  job_id: "linkedin-1000000001",
  title: "Backend Engineer",
  company: "Acme",
  location: "Paris",
  url: "https://www.linkedin.com/jobs/view/1000000001",
  has_description: false,
};
const ALERT = {
  alert_id: "x",
  subject: "3 new jobs for you",
  received_at: "2026-10-01T10:00:00Z",
  jobs: [JOB],
};

function json(body: unknown, status = 200) {
  return new Response(body === null ? null : JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function server(routes: Routes) {
  const calls: { method: string; path: string; body: string }[] = [];
  vi.stubGlobal("fetch", async (input: Request | string) => {
    const request = typeof input === "string" ? new Request(new URL(input, location.href)) : input;
    const { pathname } = new URL(request.url);
    const body = await request.clone().text();
    calls.push({ method: request.method, path: pathname, body });
    const route = routes[`${request.method} ${pathname}`];
    return route ? route(body) : json({ detail: "Not found" }, 404);
  });
  return calls;
}

function mount(element: React.ReactNode) {
  return render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter>{element}</MemoryRouter>
    </QueryClientProvider>,
  );
}

afterEach(() => vi.unstubAllGlobals());

describe("Applications", () => {
  const routes = {
    "GET /api/mail/status": () => json(OK_STATUS),
    "GET /api/applications": () => json([APPLICATION]),
  };

  it("shows each application's status, history and tailored CV", async () => {
    server(routes);
    mount(<Applications />);
    expect(await screen.findByRole("combobox", { name: "Status of Acme, Engineer" })).toHaveValue(
      "interview",
    );
    const history = document.querySelectorAll("ol li");
    expect(history[0]).toHaveTextContent("Let us talk");
    expect(history[1]).toHaveTextContent("Thanks for applying");
    expect(screen.getByRole("link", { name: "Tailored CV" })).toHaveAttribute(
      "href",
      "/generations/g1",
    );
  });

  it("lets you correct a status and remove an entry", async () => {
    const calls = server({
      ...routes,
      "PATCH /api/applications/a1": () => json({ ...APPLICATION, status: "offer" }),
      "DELETE /api/applications/a1": () => json(null, 204),
    });
    mount(<Applications />);
    await userEvent.selectOptions(
      await screen.findByRole("combobox", { name: "Status of Acme, Engineer" }),
      "offer",
    );
    await waitFor(() =>
      expect(calls.find((call) => call.method === "PATCH")?.body).toBe('{"status":"offer"}'),
    );
    await userEvent.click(screen.getByRole("button", { name: "Remove Acme, Engineer" }));
    await waitFor(() => expect(calls.some((call) => call.method === "DELETE")).toBe(true));
  });

  const INTEREST = {
    job_id: "job-9",
    title: "Data Engineer",
    company: "Beta",
    url: "https://job.test",
    opened_at: "2026-10-01T10:00:00Z",
  };

  it("asks about opened jobs and records the answer either way", async () => {
    const calls = server({
      ...routes,
      "GET /api/applications/interests": () => json([INTEREST]),
      "POST /api/applications/interests/job-9/applied": () => json(APPLICATION),
      "DELETE /api/applications/interests/job-9": () => json(null, 204),
    });
    mount(<Applications />);
    await screen.findByRole("heading", { name: "Did you apply?" });
    await userEvent.click(
      screen.getByRole("button", { name: "I applied to Data Engineer at Beta" }),
    );
    await waitFor(() =>
      expect(calls.some((call) => call.path.endsWith("/job-9/applied"))).toBe(true),
    );
    await userEvent.click(
      screen.getByRole("button", { name: "I did not apply to Data Engineer at Beta" }),
    );
    await waitFor(() => expect(calls.some((call) => call.method === "DELETE")).toBe(true));
  });

  it("asks nothing when no opened job is waiting", async () => {
    server({ ...routes, "GET /api/applications/interests": () => json([]) });
    mount(<Applications />);
    await screen.findByText("Acme");
    expect(screen.queryByRole("heading", { name: "Did you apply?" })).not.toBeInTheDocument();
  });

  it("links an application to the job post an email matched", async () => {
    server({
      ...routes,
      "GET /api/applications": () =>
        json([{ ...APPLICATION, generation_id: null, job_url: "https://job.test" }]),
    });
    mount(<Applications />);
    expect(await screen.findByRole("link", { name: "Job post" })).toHaveAttribute(
      "href",
      "https://job.test",
    );
    expect(screen.queryByRole("link", { name: "Tailored CV" })).not.toBeInTheDocument();
  });

  it("filters by status", async () => {
    server(routes);
    mount(<Applications />);
    await screen.findByText("Acme");
    await userEvent.click(screen.getByRole("button", { name: /^Rejected/ }));
    expect(screen.queryByText("Acme")).not.toBeInTheDocument();
    expect(screen.getByText("Nothing matches.")).toBeInTheDocument();
  });

  it("says how to connect a mailbox and offers no sync until it is set up", async () => {
    server({
      "GET /api/mail/status": () => json({ ...OK_STATUS, problem: "credentials" }),
      "GET /api/applications": () => json([]),
    });
    mount(<Applications />);
    expect(await screen.findByText(/MAIL_APP_PASSWORD/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Check mail now" })).not.toBeInTheDocument();
  });

  it("starts a sync and shows why the last one stopped", async () => {
    const calls = server({
      "GET /api/mail/status": () => json({ ...OK_STATUS, last_error: "The mailbox refused" }),
      "POST /api/mail/sync": () => json(OK_STATUS, 202),
      "GET /api/applications": () => json([]),
    });
    mount(<Applications />);
    expect(await screen.findByText("The mailbox refused")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Check mail now" }));
    await waitFor(() => expect(calls.some((call) => call.method === "POST")).toBe(true));
    expect(await screen.findByRole("button", { name: "Reading your mail…" })).toBeDisabled();
  });

  it("brings in new results when a background sync finishes", async () => {
    let status = OK_STATUS;
    const calls = server({
      "GET /api/mail/status": () => json(status),
      "GET /api/applications": () => json([]),
    });
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={client}>
        <MemoryRouter>
          <Applications />
        </MemoryRouter>
      </QueryClientProvider>,
    );
    await screen.findByText(/Last checked/);
    const before = calls.filter((call) => call.path === "/api/applications").length;
    status = { ...OK_STATUS, last_synced_at: "2026-10-03T10:00:00Z" };
    await client.invalidateQueries({ queryKey: ["mail-status"] });
    await waitFor(() =>
      expect(calls.filter((call) => call.path === "/api/applications").length).toBeGreaterThan(
        before,
      ),
    );
  });
});

describe("JobEmails", () => {
  const routes: Routes = {
    "GET /api/mail/status": () => json(OK_STATUS),
    "GET /api/mail/alerts": () => json([ALERT]),
    "GET /api/generations": () => json([]),
    "GET /api/cvs": () => json([]),
    "GET /api/options": () => json({ models: [], languages: [] }),
  };
  const LOAD = "POST /api/mail/jobs/1000000001/description";

  it("groups jobs under their email and links to LinkedIn", async () => {
    server(routes);
    mount(<JobEmails />);
    const group = (await screen.findByRole("heading", { name: "3 new jobs for you" })).closest(
      "section",
    ) as HTMLElement;
    expect(within(group).getByText("Backend Engineer")).toBeInTheDocument();
    expect(within(group).getByText("Acme / Paris")).toBeInTheDocument();
    expect(
      within(group).getByRole("link", { name: "Open Backend Engineer on LinkedIn" }),
    ).toHaveAttribute("href", JOB.url);
  });

  it("notes a job as opened only when its link is opened", async () => {
    const calls = server({
      ...routes,
      "POST /api/applications/interests": () => json(null, 204),
    });
    mount(<JobEmails />);
    const link = await screen.findByRole("link", { name: "Open Backend Engineer on LinkedIn" });
    const noted = () => calls.filter((call) => call.path === "/api/applications/interests");
    expect(noted().filter((call) => call.method === "POST")).toHaveLength(0);
    link.addEventListener("click", (event) => event.preventDefault());
    await userEvent.click(link);
    await waitFor(() =>
      expect(noted().find((call) => call.method === "POST")?.body).toBe(
        JSON.stringify({ job_id: JOB.job_id }),
      ),
    );
  });

  it("shows the application of a job that has one", async () => {
    server({
      ...routes,
      "GET /api/applications": () => json([{ ...APPLICATION, job_id: JOB.job_id }]),
    });
    mount(<JobEmails />);
    expect(await screen.findByRole("link", { name: "Application: Interview" })).toHaveAttribute(
      "href",
      "/applications",
    );
  });

  it("reads the job only when tailoring, then opens the tailoring dialog", async () => {
    const calls = server({ ...routes, [LOAD]: () => json({ ...JOB, has_description: true }) });
    mount(<JobEmails />);
    await screen.findByText("Backend Engineer");
    expect(calls.some((call) => call.path.includes("/description"))).toBe(false);
    await userEvent.click(screen.getByRole("button", { name: "Tailor my CV" }));
    expect(await screen.findByText("Which CV?")).toBeInTheDocument();
    expect(calls.filter((call) => call.path.includes("/description"))).toHaveLength(1);
  });

  it("does not read a job again when its description is already saved", async () => {
    const calls = server({
      ...routes,
      "GET /api/mail/alerts": () =>
        json([{ ...ALERT, jobs: [{ ...JOB, has_description: true }] }]),
    });
    mount(<JobEmails />);
    await userEvent.click(await screen.findByRole("button", { name: "Tailor my CV" }));
    expect(await screen.findByText("Which CV?")).toBeInTheDocument();
    expect(calls.some((call) => call.path.includes("/description"))).toBe(false);
  });

  it("asks for the description when LinkedIn refuses, then continues with it", async () => {
    const calls = server({
      ...routes,
      [LOAD]: (body) =>
        body.includes("Pasted")
          ? json({ ...JOB, has_description: true })
          : json({ detail: "LinkedIn would not show this job right now." }, 502),
    });
    mount(<JobEmails />);
    await userEvent.click(await screen.findByRole("button", { name: "Tailor my CV" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("would not show");
    const box = screen.getByLabelText(/paste its description/);
    await userEvent.type(box, "Pasted text");
    await userEvent.click(screen.getByRole("button", { name: "Use this description" }));
    expect(await screen.findByText("Which CV?")).toBeInTheDocument();
    expect(calls.filter((call) => call.path.includes("/description")).at(-1)?.body).toContain(
      "Pasted text",
    );
  });

  it("links to the tailored CV of a job that already has one", async () => {
    server({
      ...routes,
      "GET /api/generations": () => json([{ job_id: JOB.job_id, generation_id: "g9" }]),
    });
    mount(<JobEmails />);
    expect(await screen.findByRole("link", { name: "Tailored CV" })).toHaveAttribute(
      "href",
      "/generations/g9",
    );
  });

  it("says when an email had no readable jobs", async () => {
    server({ ...routes, "GET /api/mail/alerts": () => json([{ ...ALERT, jobs: [] }]) });
    mount(<JobEmails />);
    expect(await screen.findByText(/No jobs could be read/)).toBeInTheDocument();
  });
});
