import { describe, expect, it } from "vitest";

import { fileUrl, toApiError } from "../api/client";
import { pollInterval } from "../api/queries";
import { descriptionBlocks } from "../pages/jobText";
import { clean, setIn } from "../pages/profileForm";

describe("toApiError", () => {
  it("keeps a plain detail message", () => {
    expect(toApiError(409, { detail: "Fill in your profile" }).message).toBe(
      "Fill in your profile",
    );
  });

  it("maps validation issues to field paths without the body prefix", () => {
    const error = toApiError(422, {
      detail: [{ loc: ["body", "experiences", 0, "role"], msg: "Field required" }],
    });
    expect(error.fields).toEqual({ "experiences.0.role": "Field required" });
  });

  it("explains an unreachable API", () => {
    expect(toApiError(500, undefined).message).toContain("Is the API running");
  });
});

describe("pollInterval", () => {
  it("polls only while a generation can still change", () => {
    expect(pollInterval("queued")).toBe(4000);
    expect(pollInterval("processing")).toBe(4000);
    expect(pollInterval("failed")).toBe(30000);
    expect(pollInterval("ready")).toBe(false);
    expect(pollInterval(undefined)).toBe(false);
  });
});

describe("profile form helpers", () => {
  it("turns blanks into null and drops empty lines", () => {
    expect(clean({ email: " ", lines: ["a", "", "b"], current: false, nested: { x: "" } })).toEqual({
      email: null,
      lines: ["a", "b"],
      current: false,
      nested: { x: null },
    });
  });

  it("sets nested values without mutating the original", () => {
    const original = { items: [{ name: "a" }] };
    const updated = setIn(original, ["items", 0, "name"], "b");
    expect(updated).toEqual({ items: [{ name: "b" }] });
    expect(original.items[0].name).toBe("a");
  });
});

describe("fileUrl", () => {
  it("adds the download flag only when asked", () => {
    expect(fileUrl("abc", "cv_pdf")).toBe("/api/generations/abc/files/cv_pdf");
    expect(fileUrl("abc", "cv_tex", true)).toBe("/api/generations/abc/files/cv_tex?download=true");
  });
});

describe("descriptionBlocks", () => {
  const kinds = (text: string) => descriptionBlocks(text).map((block) => `${block.kind}:${block.text}`);

  it("finds sections in a flattened posting, with or without a colon", () => {
    expect(
      kinds("We build things. What You'll Do Ship code. Requirements: Python. Benefits Free lunch."),
    ).toEqual([
      "paragraph:We build things.",
      "heading:What You'll Do",
      "paragraph:Ship code.",
      "heading:Requirements",
      "paragraph:Python.",
      "heading:Benefits",
      "paragraph:Free lunch.",
    ]);
  });

  it("finds a colon section after a list with no full stop", () => {
    expect(kinds("Learn quality and security What we look for: You graduate soon.")).toEqual([
      "paragraph:Learn quality and security",
      "heading:What we look for",
      "paragraph:You graduate soon.",
    ]);
  });

  it("leaves section words inside a sentence alone", () => {
    const text = "Talk to our Benefits Team about Requirements today.";
    expect(kinds(text)).toEqual([`paragraph:${text}`]);
  });

  it("splits long prose but not on abbreviations", () => {
    const blocks = descriptionBlocks("Use tools, e.g. Python. Two. Three. Four.");
    expect(blocks.map((block) => block.text)).toEqual(["Use tools, e.g. Python. Two. Three.", "Four."]);
  });

  it("keeps line breaks a board already gave us, and short questions as headings", () => {
    expect(kinds("Qui sommes-nous ? Une équipe.\n\nSecond part.")).toEqual([
      "heading:Qui sommes-nous ?",
      "paragraph:Une équipe.",
      "paragraph:Second part.",
    ]);
  });

  it("turns flattened bullets into items, without splitting after a preposition", () => {
    const items = [
      "Prepare hardware components and spare parts required for all of the repair activities",
      "Identify and scan components in the relevant systems used by the operations team",
      "Carry out assembly work with Python and other tools following approved procedures",
      "Support repair workflows involving servers, storage systems and network equipment",
      "Perform visual inspections and basic procedural checks on every shift you work",
    ];
    expect(descriptionBlocks(items.join(" "))).toEqual(
      items.map((text) => ({ kind: "item", text })),
    );
  });

  it("does not split on French abbreviations", () => {
    expect(kinds("Sous outils (ex. Tableau, PowerBI).")).toHaveLength(1);
  });

  it("joins single line breaks so they cannot cut a line short", () => {
    expect(kinds("We build\n  useful things\nevery day.\n\nSecond part.")).toEqual([
      "paragraph:We build useful things every day.",
      "paragraph:Second part.",
    ]);
  });

  it("tidies stray spaces and handles nothing", () => {
    expect(kinds("At Coinbase , we build .NET tools.")).toEqual([
      "paragraph:At Coinbase, we build .NET tools.",
    ]);
    expect(descriptionBlocks(null)).toEqual([]);
  });
});
