import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import { SkillPicker } from "../components/SkillPicker";
import { Profile } from "../pages/Profile";

const CATEGORIES = [
  { key: "programming_languages", label: "Programming languages", skills: ["Python", "C", "C++", "Rust"] },
  { key: "databases", label: "Databases", skills: ["PostgreSQL", "Redis"] },
];

function Picker({ initial = {} }: { initial?: Record<string, string[]> }) {
  const [skills, setSkills] = useState(initial);
  return (
    <>
      <SkillPicker
        categories={CATEGORIES}
        skills={skills}
        onChange={(key, chosen) => setSkills({ ...skills, [key]: chosen })}
      />
      <output>{JSON.stringify(skills)}</output>
    </>
  );
}

const languages = () => screen.getByRole("combobox", { name: "Programming languages" });
const chosen = () => JSON.parse(screen.getByRole("status").textContent ?? "{}");

describe("SkillPicker", () => {
  it("offers a category's own list and adds the clicked skill", async () => {
    render(<Picker />);
    await userEvent.click(languages());
    const list = screen.getByRole("listbox");
    expect(within(list).getAllByRole("option").map((item) => item.textContent)).toEqual([
      "Python",
      "C",
      "C++",
      "Rust",
    ]);
    await userEvent.click(within(list).getByText("Rust"));
    expect(chosen()).toEqual({ programming_languages: ["Rust"] });
    expect(within(screen.getByRole("listbox")).queryByText("Rust")).toBeNull();
    expect(screen.getByText("1 skill picked.", { exact: false })).toBeInTheDocument();
  });

  it("only ever adds what is on the list", async () => {
    render(<Picker />);
    await userEvent.type(languages(), "Cobol{Enter}");
    expect(chosen()).toEqual({});
    expect(screen.getByText(/Not in the list/)).toBeInTheDocument();
  });

  it("puts skills that start with the search first", async () => {
    render(<Picker />);
    await userEvent.type(languages(), "c");
    expect(screen.getAllByRole("option").map((item) => item.textContent)).toEqual(["C", "C++"]);
  });

  it("works from the keyboard", async () => {
    render(<Picker />);
    await userEvent.type(languages(), "{ArrowDown}{Enter}");
    expect(chosen()).toEqual({ programming_languages: ["C"] });
    await userEvent.keyboard("{ArrowUp}{Enter}");
    expect(chosen().programming_languages).toEqual(["C", "Rust"]);
    await userEvent.keyboard("{Backspace}");
    expect(chosen().programming_languages).toEqual(["C"]);
    await userEvent.keyboard("{Escape}");
    expect(screen.queryByRole("listbox")).toBeNull();
  });

  it("removes a skill with its button", async () => {
    render(<Picker initial={{ databases: ["Redis", "PostgreSQL"] }} />);
    await userEvent.click(screen.getByRole("button", { name: "Remove Redis" }));
    expect(chosen()).toEqual({ databases: ["PostgreSQL"] });
  });

  it("says when a whole category has been added", async () => {
    render(<Picker initial={{ databases: ["Redis", "PostgreSQL"] }} />);
    await userEvent.click(screen.getByRole("combobox", { name: "Databases" }));
    expect(screen.getByText("You have added all of these.")).toBeInTheDocument();
  });
});

describe("Profile skills", () => {
  afterEach(() => vi.unstubAllGlobals());

  function server() {
    const saved: string[] = [];
    vi.stubGlobal("fetch", async (input: Request) => {
      const { pathname } = new URL(input.url);
      const reply = (body: unknown) =>
        new Response(JSON.stringify(body), { headers: { "Content-Type": "application/json" } });
      if (pathname === "/api/options") {
        return reply({ user_id: "ali", skill_categories: CATEGORIES, categories: [] });
      }
      if (input.method === "PUT") {
        saved.push(await input.clone().text());
        return reply(JSON.parse(saved[saved.length - 1]));
      }
      return reply({
        user_id: "ali",
        full_name: "Ali Example",
        version: 1,
        skills: { databases: ["Redis"] },
      });
    });
    return saved;
  }

  it("saves the picked skills by category, and Enter in the picker does not save", async () => {
    const saved = server();
    render(
      <QueryClientProvider client={new QueryClient()}>
        <MemoryRouter>
          <Profile />
        </MemoryRouter>
      </QueryClientProvider>,
    );
    await screen.findByRole("button", { name: "Remove Redis" });
    await userEvent.type(languages(), "py{Enter}");
    expect(saved).toEqual([]);
    fireEvent.submit(screen.getByRole("button", { name: "Save profile" }).closest("form")!);
    await waitFor(() => expect(saved).toHaveLength(1));
    expect(JSON.parse(saved[0]).skills).toEqual({
      databases: ["Redis"],
      programming_languages: ["Python"],
    });
  });
});
