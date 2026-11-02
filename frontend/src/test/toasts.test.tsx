import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { notify } from "../api/notices";
import { useAnswerInterest, useDeleteCv, useStartGeneration } from "../api/queries";
import { Toasts } from "../components/Toasts";

function json(body: unknown, status = 200) {
  return new Response(body === null ? null : JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function mount(element: React.ReactNode) {
  return render(
    <QueryClientProvider client={new QueryClient()}>
      <Toasts />
      {element}
    </QueryClientProvider>,
  );
}

function DeleteCv() {
  const remove = useDeleteCv();
  return <button onClick={() => remove.mutate("cv-1")}>Delete</button>;
}

function StartGeneration() {
  const start = useStartGeneration();
  return (
    <button onClick={() => start.mutate({ job_id: "j", cv_id: "c", language: "en" })}>Start</button>
  );
}

function Answer({ applied }: { applied: boolean }) {
  const answer = useAnswerInterest("job-9");
  return <button onClick={() => answer.mutate(applied)}>Answer</button>;
}

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("Toasts", () => {
  it("shows a notice for a few seconds", () => {
    vi.useFakeTimers();
    render(<Toasts />);

    act(() => notify("Profile saved"));
    expect(screen.getByRole("status")).toHaveTextContent("Profile saved");

    act(() => {
      vi.advanceTimersByTime(3500);
    });
    expect(screen.getByRole("status")).toBeEmptyDOMElement();
  });

  it("keeps only the latest few", () => {
    render(<Toasts />);
    act(() => ["one", "two", "three", "four"].forEach(notify));

    expect(screen.queryByText("one")).not.toBeInTheDocument();
    expect(screen.getByText("four")).toBeInTheDocument();
  });

  it("confirms an action the API accepted", async () => {
    vi.stubGlobal("fetch", async () => json(null, 204));
    mount(<DeleteCv />);
    await userEvent.click(screen.getByRole("button", { name: "Delete" }));
    expect(await screen.findByText("CV deleted")).toBeInTheDocument();
  });

  it("confirms nothing when the API refuses", async () => {
    const refused = vi.fn(async () => json({ detail: "No" }, 500));
    vi.stubGlobal("fetch", refused);
    mount(<DeleteCv />);
    await userEvent.click(screen.getByRole("button", { name: "Delete" }));
    await waitFor(() => expect(refused).toHaveBeenCalled());
    await act(async () => {});
    expect(screen.getByRole("status")).toBeEmptyDOMElement();
  });

  it.each([
    ["queued", "Tailoring started"],
    ["ready", "Already tailored with these settings"],
    ["failed", "These settings already failed"],
  ])("words a %s generation by what the API returned", async (status, message) => {
    vi.stubGlobal("fetch", async () => json({ generation_id: "g1", status }, 201));
    mount(<StartGeneration />);
    await userEvent.click(screen.getByRole("button", { name: "Start" }));
    expect(await screen.findByText(message)).toBeInTheDocument();
  });

  it.each([
    [true, "Marked as applied"],
    [false, "Removed from the list"],
  ])("words the answer to did you apply (%s)", async (applied, message) => {
    vi.stubGlobal("fetch", async () => (applied ? json({}) : json(null, 204)));
    mount(<Answer applied={applied} />);
    await userEvent.click(screen.getByRole("button", { name: "Answer" }));
    expect(await screen.findByText(message)).toBeInTheDocument();
  });
});
