import { describe, expect, it } from "vitest";

import { fileUrl, toApiError } from "../api/client";
import { pollInterval } from "../api/queries";
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
