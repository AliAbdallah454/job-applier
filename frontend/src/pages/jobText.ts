/**
 * Scrapers flatten most postings to one line, and changing that would change the classification
 * hash of every job. So the reading layout is rebuilt here, from the words alone.
 */
export type Block = { kind: "heading" | "paragraph" | "item"; text: string };

const SECTIONS = [
  "About (?:the |this )?(?:role|job|position|team|company|opportunity|us)",
  "Role Overview",
  "Job Description",
  "The Role",
  "Who (?:you are|we are)",
  "What you(?:'|’)?(?:ll)? (?:do|bring|need|have|learn|get|gain)",
  "What we (?:look for|offer)",
  "What we(?:'|’)?(?: a)?re looking for",
  "Why join us",
  "(?:Key |Main |Your )?Responsibilities",
  "(?:Minimum |Basic |Required |Preferred |Desired )?(?:Qualifications|Requirements)",
  "Required Skills(?: and Experience)?",
  "Nice to have",
  "Bonus Points",
  "Benefits",
  "Perks",
  "Compensation",
  "Your profile",
  "Votre profil",
  "Profil recherché",
  "(?:Vos |Tes )?missions",
];
const SECTION = new RegExp(`(^|\\s)(${SECTIONS.join("|")})(\\s?:)?(?=\\s|$)`, "giu");
const UPPER = /^\p{Lu}/u;
const NOT_THE_END = /(?:\b(?:e\.g|i\.e|ex|cf|vs|Inc|Ltd|Jr|Sr|Mr|Ms|Dr|St|No)|\b\p{Lu})\.$/u;
const SENTENCE_END = /(?<=[.!?…])\s+(?=[\p{Lu}\d"“(])/u;
const LEAD_IN = /^(\S.{0,60}?)\s?:\s+(.+)$/su;
const MAX_PARAGRAPH = 320;
// Flattened bullets have no punctuation: "repair activities Identify and scan parts Carry out".
const RUN_ON = 350;
const BULLET_BREAK =
  /(?<=[\p{Ll})\d])(?<!\b(?:a|an|and|as|at|by|for|from|in|like|of|on|or|our|the|to|using|with|your))\s+(?=\p{Lu}\p{Ll}+[ ,]\p{Ll})/gu;

/** Puts known section names on their own line; without a colon they must start a sentence. */
function markSections(text: string) {
  return text.replace(SECTION, (match, space: string, name: string, colon: string, at: number) => {
    const before = text.slice(0, at).trimEnd();
    const next = text.slice(at + match.length).trimStart();
    const starts = before === "" || /[.!?:…]$/.test(before);
    if (!UPPER.test(name) || !(colon || (starts && UPPER.test(next)))) return match;
    return `${space}\n${name}:\n`;
  });
}

function sentences(line: string) {
  const parts: string[] = [];
  for (const part of line.split(new RegExp(SENTENCE_END, "gu"))) {
    if (parts.length && NOT_THE_END.test(parts[parts.length - 1])) {
      parts[parts.length - 1] += ` ${part}`;
    } else {
      parts.push(part);
    }
  }
  return parts;
}

function isHeading(text: string, maxWords = 7) {
  return UPPER.test(text) && !/[.,;]/.test(text) && text.split(/\s+/).length <= maxWords;
}

export function descriptionBlocks(description: string | null | undefined): Block[] {
  // A single line break is the board's own wrapping; only a blank line separates paragraphs.
  const tidy = (description ?? "")
    .replace(/​/g, "")
    .replace(/ +([,.])(?=\s|$)/g, "$1")
    .replace(/\s*\n\s*\n\s*/g, "\u0000")
    .replace(/\s*\n\s*/g, " ")
    .replaceAll("\u0000", "\n");
  const blocks: Block[] = [];
  let paragraph: string[] = [];
  const flush = () => {
    if (paragraph.length) blocks.push({ kind: "paragraph", text: paragraph.join(" ") });
    paragraph = [];
  };
  const heading = (text: string) => {
    flush();
    blocks.push({ kind: "heading", text });
  };
  const add = (text: string) => {
    const items = text.length > RUN_ON ? text.split(BULLET_BREAK) : [];
    if (items.length < 3) {
      paragraph.push(text);
    } else {
      flush();
      blocks.push(...items.map((item) => ({ kind: "item" as const, text: item })));
    }
  };

  for (const line of markSections(tidy).split("\n")) {
    for (const sentence of sentences(line.trim()).filter(Boolean)) {
      const lead = sentence.match(LEAD_IN);
      if (sentence.endsWith(":") && isHeading(sentence.slice(0, -1).trim())) {
        heading(sentence.slice(0, -1).trim());
      } else if (sentence.endsWith("?") && isHeading(sentence, 5)) {
        heading(sentence);
      } else if (lead && isHeading(lead[1], 5)) {
        heading(lead[1]);
        add(lead[2]);
      } else {
        add(sentence);
      }
      if (paragraph.length >= 3 || paragraph.join(" ").length > MAX_PARAGRAPH) flush();
    }
    flush();
  }
  return blocks;
}
