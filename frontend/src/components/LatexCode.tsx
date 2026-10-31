const STYLES = {
  comment: "text-muted",
  environment: "text-tex-environment",
  command: "text-tex-command",
  math: "text-tex-math",
  bracket: "text-tex-bracket",
};
type Kind = keyof typeof STYLES;

// Math stays on one line, so a lone $ can't colour the rest of the document.
const TOKEN =
  /(?<comment>%.*)|(?<environment>(?<=\\(?:begin|end)\{)[a-zA-Z]+\*?)|(?<command>\\(?:[a-zA-Z@]+\*?|[^]))|(?<math>\$\$[^$\n]*\$\$|\$(?:[^$\\\n]|\\.)+\$)|(?<bracket>[{}[\]])/gu;

/** Splits LaTeX into coloured pieces and plain text; joined, they are the source exactly. */
export function latexTokens(source: string) {
  const tokens: { kind: Kind | null; text: string }[] = [];
  let end = 0;
  for (const match of source.matchAll(TOKEN)) {
    if (match.index > end) tokens.push({ kind: null, text: source.slice(end, match.index) });
    const groups = Object.entries(match.groups ?? {});
    const kind = groups.find(([, value]) => value !== undefined)?.[0] as Kind;
    tokens.push({ kind, text: match[0] });
    end = match.index + match[0].length;
  }
  if (end < source.length) tokens.push({ kind: null, text: source.slice(end) });
  return tokens;
}

export function LatexCode({ text }: { text: string }) {
  return latexTokens(text).map(({ kind, text: piece }, index) =>
    kind ? (
      <span key={index} className={STYLES[kind]}>
        {piece}
      </span>
    ) : (
      piece
    ),
  );
}
