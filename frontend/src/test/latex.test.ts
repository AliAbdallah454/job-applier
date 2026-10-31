import { describe, expect, it } from "vitest";

import { latexTokens } from "../components/LatexCode";

const kinds = (source: string) =>
  latexTokens(source)
    .filter((token) => token.kind)
    .map((token) => [token.kind, token.text]);

describe("latexTokens", () => {
  it("keeps every character of the source", () => {
    const source = "\\documentclass[11pt]{article}\n% note\n\\begin{itemize*}\n  \\item 100\\% $x^2$\n";
    expect(
      latexTokens(source)
        .map((token) => token.text)
        .join(""),
    ).toBe(source);
  });

  it("finds commands, brackets and environment names", () => {
    expect(kinds("\\begin{itemize}")).toEqual([
      ["command", "\\begin"],
      ["bracket", "{"],
      ["environment", "itemize"],
      ["bracket", "}"],
    ]);
  });

  it("leaves the text of an argument plain", () => {
    expect(latexTokens("\\section{Experience}")[2]).toEqual({ kind: null, text: "Experience" });
  });

  it("ends a comment at the line break", () => {
    expect(kinds("a % note {x}\n\\b")).toEqual([
      ["comment", "% note {x}"],
      ["command", "\\b"],
    ]);
  });

  it("does not treat an escaped percent or dollar as a comment or math", () => {
    expect(kinds("50\\% of \\$5 and \\$6")).toEqual([
      ["command", "\\%"],
      ["command", "\\$"],
      ["command", "\\$"],
    ]);
  });

  it("starts a comment after a line break command", () => {
    expect(kinds("\\\\% note")).toEqual([
      ["command", "\\\\"],
      ["comment", "% note"],
    ]);
  });

  it("colours math on one line and ignores a lone dollar", () => {
    expect(kinds("$x^2$ and $$y$$")).toEqual([
      ["math", "$x^2$"],
      ["math", "$$y$$"],
    ]);
    expect(kinds("a $ b\nc $ d")).toEqual([]);
  });
});
