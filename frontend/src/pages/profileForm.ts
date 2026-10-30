export type Draft = Record<string, unknown>;

export type Field = {
  key: string;
  label: string;
  kind?: "text" | "area" | "lines" | "date" | "check" | "links";
  required?: boolean;
};

const DATES: Field[] = [
  { key: "start_date", label: "Start", kind: "date" },
  { key: "end_date", label: "End", kind: "date" },
  { key: "current", label: "Current", kind: "check" },
];

export type Section = { key: string; title: string; item: string; fields: Field[] };

export const SECTIONS: Section[] = [
  {
    key: "experiences",
    title: "Experience",
    item: "experience",
    fields: [
      { key: "organization", label: "Organization", required: true },
      { key: "role", label: "Role", required: true },
      { key: "location", label: "Location" },
      ...DATES,
      { key: "description", label: "Description", kind: "area", required: true },
      { key: "responsibilities", label: "Responsibilities, one per line", kind: "lines" },
      { key: "achievements", label: "Achievements, one per line", kind: "lines" },
      { key: "technologies", label: "Technologies, one per line", kind: "lines" },
      { key: "links", label: "Links", kind: "links" },
    ],
  },
  {
    key: "projects",
    title: "Projects",
    item: "project",
    fields: [
      { key: "name", label: "Name", required: true },
      ...DATES,
      { key: "description", label: "Description", kind: "area", required: true },
      { key: "contribution", label: "Your contribution", kind: "area" },
      { key: "outcomes", label: "Outcomes, one per line", kind: "lines" },
      { key: "technologies", label: "Technologies, one per line", kind: "lines" },
      { key: "links", label: "Links", kind: "links" },
    ],
  },
  {
    key: "education",
    title: "Education",
    item: "education",
    fields: [
      { key: "institution", label: "Institution", required: true },
      { key: "qualification", label: "Qualification", required: true },
      { key: "field_of_study", label: "Field of study" },
      ...DATES,
      { key: "description", label: "Description", kind: "area" },
      { key: "coursework", label: "Coursework, one per line", kind: "lines" },
      { key: "achievements", label: "Achievements, one per line", kind: "lines" },
    ],
  },
  {
    key: "skills",
    title: "Skills",
    item: "skill",
    fields: [
      { key: "name", label: "Name", required: true },
      { key: "proficiency", label: "Proficiency" },
      { key: "description", label: "Description" },
    ],
  },
  {
    key: "languages",
    title: "Languages",
    item: "language",
    fields: [
      { key: "name", label: "Name", required: true },
      { key: "proficiency", label: "Proficiency" },
    ],
  },
  {
    key: "certifications",
    title: "Certifications",
    item: "certification",
    fields: [
      { key: "name", label: "Name", required: true },
      { key: "issuer", label: "Issuer", required: true },
      { key: "issued_at", label: "Issued", kind: "date" },
      { key: "credential_url", label: "Credential URL" },
      { key: "description", label: "Description", kind: "area" },
    ],
  },
];

/** Forms hold "" and blank lines while typing; the API expects null and no empty entries. */
export function clean(value: unknown): unknown {
  if (typeof value === "string") return value.trim() === "" ? null : value;
  if (Array.isArray(value)) {
    return value.map(clean).filter((item) => item !== null);
  }
  if (value && typeof value === "object") {
    return Object.fromEntries(Object.entries(value).map(([key, item]) => [key, clean(item)]));
  }
  return value;
}

export function setIn(draft: Draft, path: (string | number)[], value: unknown): Draft {
  const [head, ...rest] = path;
  const copy = (Array.isArray(draft) ? [...draft] : { ...draft }) as Draft;
  copy[head] = rest.length
    ? setIn((copy[head] ?? (typeof rest[0] === "number" ? [] : {})) as Draft, rest, value)
    : value;
  return copy;
}
