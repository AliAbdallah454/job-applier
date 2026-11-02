import { useEffect, useId, useRef, useState } from "react";

import type { SkillCategory } from "../api/client";

type Skills = Record<string, string[]>;

/** Skills that start with the search come first, so "c" offers C before React. */
function matching(skills: string[], search: string) {
  const wanted = search.trim().toLowerCase();
  const found = skills.filter((skill) => skill.toLowerCase().includes(wanted));
  const starts = (skill: string) => skill.toLowerCase().startsWith(wanted);
  return [...found.filter(starts), ...found.filter((skill) => !starts(skill))];
}

function Highlight({ text, search }: { text: string; search: string }) {
  const wanted = search.trim().toLowerCase();
  const start = text.toLowerCase().indexOf(wanted);
  if (!wanted || start < 0) return <>{text}</>;
  const end = start + wanted.length;
  return (
    <>
      {text.slice(0, start)}
      <span className="font-semibold underline underline-offset-2">{text.slice(start, end)}</span>
      {text.slice(end)}
    </>
  );
}

function SkillRow({
  category,
  chosen,
  onChange,
}: {
  category: SkillCategory;
  chosen: string[];
  onChange: (skills: string[]) => void;
}) {
  const listId = useId();
  const list = useRef<HTMLUListElement>(null);
  const [search, setSearch] = useState("");
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const remaining = category.skills.filter((skill) => !chosen.includes(skill));
  const matches = matching(remaining, search);
  const highlighted = Math.min(active, matches.length - 1);

  useEffect(() => {
    list.current?.children[highlighted]?.scrollIntoView?.({ block: "nearest" });
  }, [highlighted, open]);

  const add = (skill: string) => {
    onChange([...chosen, skill]);
    setSearch("");
    setActive(0);
  };
  const keys = (event: React.KeyboardEvent<HTMLInputElement>) => {
    if (event.key === "Enter") {
      event.preventDefault(); // Enter here adds a skill; it must not save the whole profile.
      if (open && matches[highlighted]) add(matches[highlighted]);
    } else if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      const step = event.key === "ArrowDown" ? 1 : -1;
      setActive(open ? (highlighted + step + matches.length) % matches.length || 0 : 0);
      setOpen(true);
    } else if (event.key === "Escape") {
      setOpen(false);
    } else if (event.key === "Backspace" && !search && chosen.length) {
      onChange(chosen.slice(0, -1));
    }
  };

  return (
    <div className="grid gap-x-6 gap-y-2 border-b border-rule py-3 sm:grid-cols-[11rem_1fr]">
      <div className="flex items-baseline justify-between gap-2 sm:pt-1.5">
        <span id={`${listId}-label`} className="text-sm font-medium">
          {category.label}
        </span>
        <span className="font-mono text-[11px] text-muted">{chosen.length || ""}</span>
      </div>
      <div className="relative min-w-0">
        <div className="flex min-h-9 flex-wrap items-center gap-1 border border-transparent p-1 transition-colors hover:border-rule focus-within:border-ink">
          {chosen.map((skill) => (
            <span
              key={skill}
              className="rise inline-flex items-center gap-1 border border-rule bg-wash py-0.5 pr-1 pl-2 text-xs"
            >
              {skill}
              <button
                type="button"
                aria-label={`Remove ${skill}`}
                onClick={() => onChange(chosen.filter((item) => item !== skill))}
                className="flex size-4 items-center justify-center text-muted transition-colors hover:bg-ink hover:text-paper"
              >
                <svg aria-hidden viewBox="0 0 8 8" className="size-2 stroke-current stroke-[1.4]">
                  <path d="M1 1l6 6M7 1 1 7" />
                </svg>
              </button>
            </span>
          ))}
          <input
            role="combobox"
            aria-labelledby={`${listId}-label`}
            aria-expanded={open}
            aria-controls={listId}
            aria-autocomplete="list"
            aria-activedescendant={
              open && matches.length ? `${listId}-${highlighted}` : undefined
            }
            autoComplete="off"
            value={search}
            placeholder={chosen.length ? "Add more" : "Search and add"}
            className="tag-input min-w-24 flex-1 bg-transparent px-1 py-0.5 text-sm placeholder:text-muted"
            onChange={(event) => {
              setSearch(event.target.value);
              setActive(0);
              setOpen(true);
            }}
            onFocus={() => setOpen(true)}
            onClick={() => setOpen(true)}
            onBlur={() => setOpen(false)}
            onKeyDown={keys}
          />
        </div>
        {open && (
          <ul
            ref={list}
            id={listId}
            role="listbox"
            aria-labelledby={`${listId}-label`}
            className="absolute inset-x-0 top-full z-30 max-h-60 overflow-y-auto border border-ink bg-paper py-1"
          >
            {matches.map((skill, index) => (
              <li
                key={skill}
                id={`${listId}-${index}`}
                role="option"
                aria-selected={index === highlighted}
                className={`cursor-pointer px-3 py-1.5 text-sm ${
                  index === highlighted ? "bg-ink text-paper" : ""
                }`}
                onMouseEnter={() => setActive(index)}
                onMouseDown={(event) => event.preventDefault()} // Keeps the input focused.
                onClick={() => add(skill)}
              >
                <Highlight text={skill} search={search} />
              </li>
            ))}
            {matches.length === 0 && (
              <li className="px-3 py-2 text-sm text-muted">
                {remaining.length === 0
                  ? "You have added all of these."
                  : "Not in the list. You can add it under Anything else."}
              </li>
            )}
          </ul>
        )}
      </div>
    </div>
  );
}

export function SkillPicker({
  categories,
  skills,
  onChange,
}: {
  categories: SkillCategory[];
  skills: Skills;
  onChange: (key: string, skills: string[]) => void;
}) {
  const total = categories.reduce((sum, item) => sum + (skills[item.key]?.length ?? 0), 0);
  return (
    <div>
      <p className="text-sm text-muted">
        {total === 1 ? "1 skill" : `${total} skills`} picked. Click a category to choose from its
        list.
      </p>
      <div className="mt-3 border-t border-ink">
        {categories.map((category) => (
          <SkillRow
            key={category.key}
            category={category}
            chosen={skills[category.key] ?? []}
            onChange={(chosen) => onChange(category.key, chosen)}
          />
        ))}
      </div>
    </div>
  );
}
