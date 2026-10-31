import { describe, expect, it } from "vitest";

import { refineLine } from "../components/sourceLine";

const SOURCE = [
  "\\resumeSubheading",
  "  {Software Engineer}{2024}",
  "  {Example Labs}{Paris}",
  "\\resumeItem{Built a \\textbf{FastAPI} service for search}",
  "\\resumeItem{Reduced deploy time with Docker}",
].join("\n");

describe("refineLine", () => {
  it("moves from where the macro ended to the line holding the text", () => {
    expect(refineLine(SOURCE, 3, "Software Engineer")).toBe(2);
  });

  it("still matches text with LaTeX commands inside it", () => {
    expect(refineLine(SOURCE, 5, "Built a FastAPI service for search")).toBe(4);
  });

  it("keeps the SyncTeX line when the words are not nearby", () => {
    expect(refineLine(SOURCE, 3, "Something from another section")).toBe(3);
    expect(refineLine(SOURCE, 3, "")).toBe(3);
  });

  it("does not look further than the window", () => {
    const far = `Matching words here${"\nfiller".repeat(20)}`;
    expect(refineLine(far, 20, "Matching words here")).toBe(20);
  });
});
