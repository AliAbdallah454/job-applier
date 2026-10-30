import { useEffect, useState } from "react";

import { ApiError, type CandidateProfile } from "../api/client";
import { useOptions, useProfile, useSaveProfile } from "../api/queries";
import { Button, Empty, ErrorNote, Eyebrow, label } from "../components/ui";
import { clean, type Draft, type Field, SECTIONS, setIn } from "./profileForm";

const INPUT = "w-full border-b border-rule bg-paper py-1.5 text-sm outline-none focus:border-ink";
const ARRANGEMENTS = ["onsite", "hybrid", "remote"];

type Path = (string | number)[];
type Edit = (path: Path, value: unknown) => void;

export function Profile() {
  const options = useOptions();
  const profile = useProfile();
  const save = useSaveProfile();
  const [draft, setDraft] = useState<Draft | null>(null);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    if (profile.data !== undefined && options.data && draft === null) {
      setDraft(profile.data ?? { user_id: options.data.user_id, full_name: "" });
    }
  }, [profile.data, options.data, draft]);

  if (profile.error) {
    return (
      <div className="p-6">
        <ErrorNote error={profile.error} />
      </div>
    );
  }
  if (!draft) return <Empty>Loading…</Empty>;

  const fields = save.error instanceof ApiError ? save.error.fields : {};
  const edit: Edit = (path, value) => {
    setSaved(false);
    setDraft((current) => setIn(current ?? {}, path, value));
  };
  const submit = () =>
    save.mutate(clean(draft) as CandidateProfile, {
      onSuccess: (result) => {
        setDraft(result);
        setSaved(true);
      },
    });

  return (
    <form
      className="mx-auto w-full max-w-3xl px-4 pb-24 sm:px-6"
      onSubmit={(event) => {
        event.preventDefault();
        submit();
      }}
    >
      <h1 className="pt-6 text-3xl font-bold tracking-tight">Profile</h1>
      <p className="mt-1 max-w-prose text-sm text-muted">
        Extra facts the model may use when tailoring. Your CV stays the starting point.
      </p>

      <Group title="Contact">
        <div className="grid gap-4 sm:grid-cols-2">
          {[
            { key: "full_name", label: "Full name", required: true },
            { key: "email", label: "Email" },
            { key: "phone", label: "Phone" },
            { key: "location", label: "Location" },
          ].map((field) => (
            <Input key={field.key} field={field} value={draft} path={[]} edit={edit} errors={fields} />
          ))}
        </div>
        <Input
          field={{ key: "links", label: "Links", kind: "links" }}
          value={draft}
          path={[]}
          edit={edit}
          errors={fields}
        />
      </Group>

      <Group title="Background">
        <Input
          field={{ key: "background", label: "Summary", kind: "area" }}
          value={draft}
          path={[]}
          edit={edit}
          errors={fields}
        />
      </Group>

      {SECTIONS.map((section) => {
        const entries = (draft[section.key] ?? []) as Draft[];
        return (
          <Group key={section.key} title={section.title}>
            {entries.map((entry, index) => (
              <div key={index} className="rise flex flex-col gap-4 border-t border-rule pt-4">
                <div className="grid gap-4 sm:grid-cols-2">
                  {section.fields.map((field) => (
                    <div
                      key={field.key}
                      className={
                        ["area", "lines", "links"].includes(field.kind ?? "") ? "sm:col-span-2" : ""
                      }
                    >
                      <Input
                        field={field}
                        value={entry}
                        path={[section.key, index]}
                        edit={edit}
                        errors={fields}
                      />
                    </div>
                  ))}
                </div>
                {fields[`${section.key}.${index}`] && (
                  <p className="text-xs">{fields[`${section.key}.${index}`]}</p>
                )}
                <button
                  type="button"
                  className="self-start text-xs text-muted underline underline-offset-4"
                  onClick={() =>
                    edit(
                      [section.key],
                      entries.filter((_, position) => position !== index),
                    )
                  }
                >
                  Remove this {section.item}
                </button>
              </div>
            ))}
            <Button
              className="self-start"
              onClick={() => edit([section.key], [...entries, {}])}
            >
              Add {section.item}
            </Button>
          </Group>
        );
      })}

      <Group title="Job preferences">
        <Input
          field={{ key: "roles", label: "Roles, one per line", kind: "lines" }}
          value={(draft.preferences ?? {}) as Draft}
          path={["preferences"]}
          edit={edit}
          errors={fields}
        />
        <Choices name="categories" title="Categories" options={options.data?.categories} draft={draft} edit={edit} />
        <Choices name="seniorities" title="Levels" options={options.data?.seniorities} draft={draft} edit={edit} />
        <Choices name="contract_types" title="Contracts" options={options.data?.contract_types} draft={draft} edit={edit} />
        <Choices name="work_arrangements" title="Work arrangement" options={ARRANGEMENTS} draft={draft} edit={edit} />
        {[
          { key: "locations", label: "Locations, one per line", kind: "lines" as const },
          { key: "availability", label: "Availability" },
          { key: "notes", label: "Notes", kind: "area" as const },
        ].map((field) => (
          <Input
            key={field.key}
            field={field}
            value={(draft.preferences ?? {}) as Draft}
            path={["preferences"]}
            edit={edit}
            errors={fields}
          />
        ))}
      </Group>

      <Group title="Anything else">
        <Input
          field={{ key: "additional_information", label: "Additional information", kind: "area" }}
          value={draft}
          path={[]}
          edit={edit}
          errors={fields}
        />
      </Group>

      <div className="fixed inset-x-0 bottom-0 border-t border-ink bg-paper">
        <div className="mx-auto flex max-w-3xl items-center justify-between gap-4 px-4 py-3 sm:px-6">
          <div className="min-w-0">
            {save.error ? (
              <ErrorNote error={save.error} />
            ) : (
              <p className="font-mono text-[11px] text-muted" aria-live="polite">
                {saved ? "Saved" : `Version ${String(draft.version ?? "new")}`}
              </p>
            )}
          </div>
          <Button primary type="submit" disabled={save.isPending}>
            {save.isPending ? "Saving…" : "Save profile"}
          </Button>
        </div>
      </div>
    </form>
  );
}

function Group({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="flex flex-col gap-4 pt-10">
      <h2 className="text-lg font-semibold tracking-tight">{title}</h2>
      {children}
    </section>
  );
}

function Input({
  field,
  value,
  path,
  edit,
  errors,
}: {
  field: Field;
  value: Draft;
  path: Path;
  edit: Edit;
  errors: Record<string, string>;
}) {
  const full = [...path, field.key];
  const id = `profile-${full.join("-")}`;
  const error = errors[full.join(".")];
  const current = value[field.key];

  if (field.kind === "check") {
    return (
      <label className="flex items-center gap-2 self-end py-1.5 text-sm">
        <input
          id={id}
          type="checkbox"
          className="accent-current"
          checked={Boolean(current)}
          onChange={(event) => edit(full, event.target.checked)}
        />
        {field.label}
      </label>
    );
  }
  if (field.kind === "links") {
    return <Links value={(current ?? []) as Draft[]} path={full} edit={edit} errors={errors} />;
  }

  const shared = {
    id,
    className: `${INPUT} ${error ? "border-ink" : ""}`,
    "aria-invalid": Boolean(error),
  };
  return (
    <div>
      <label htmlFor={id}>
        <Eyebrow>
          {field.label}
          {field.required ? " *" : ""}
        </Eyebrow>
      </label>
      {field.kind === "lines" ? (
        <textarea
          {...shared}
          rows={3}
          value={((current ?? []) as string[]).join("\n")}
          onChange={(event) => edit(full, event.target.value.split("\n"))}
        />
      ) : field.kind === "area" ? (
        <textarea
          {...shared}
          rows={3}
          value={(current ?? "") as string}
          onChange={(event) => edit(full, event.target.value)}
        />
      ) : (
        <input
          {...shared}
          placeholder={field.kind === "date" ? "YYYY-MM" : undefined}
          value={(current ?? "") as string}
          onChange={(event) => edit(full, event.target.value)}
        />
      )}
      {error && <p className="mt-1 text-xs">{error}</p>}
    </div>
  );
}

function Links({
  value,
  path,
  edit,
  errors,
}: {
  value: Draft[];
  path: Path;
  edit: Edit;
  errors: Record<string, string>;
}) {
  return (
    <div className="flex flex-col gap-2">
      <Eyebrow>Links</Eyebrow>
      {value.map((_, index) => (
        <div key={index} className="grid grid-cols-[1fr_2fr_auto] items-end gap-3">
          <Input
            field={{ key: "label", label: "Label", required: true }}
            value={value[index]}
            path={[...path, index]}
            edit={edit}
            errors={errors}
          />
          <Input
            field={{ key: "url", label: "URL", required: true }}
            value={value[index]}
            path={[...path, index]}
            edit={edit}
            errors={errors}
          />
          <button
            type="button"
            className="pb-2 text-xs text-muted underline underline-offset-4"
            onClick={() =>
              edit(
                path,
                value.filter((__, position) => position !== index),
              )
            }
          >
            Remove
          </button>
        </div>
      ))}
      <button
        type="button"
        className="self-start text-xs underline underline-offset-4"
        onClick={() => edit(path, [...value, {}])}
      >
        Add link
      </button>
    </div>
  );
}

function Choices({
  name,
  title,
  options,
  draft,
  edit,
}: {
  name: string;
  title: string;
  options?: string[];
  draft: Draft;
  edit: Edit;
}) {
  const chosen = (((draft.preferences ?? {}) as Draft)[name] ?? []) as string[];
  return (
    <div>
      <Eyebrow>{title}</Eyebrow>
      <div className="mt-2 flex flex-wrap gap-1">
        {options?.map((option) => {
          const active = chosen.includes(option);
          return (
            <button
              key={option}
              type="button"
              aria-pressed={active}
              onClick={() =>
                edit(
                  ["preferences", name],
                  active ? chosen.filter((item) => item !== option) : [...chosen, option],
                )
              }
              className={`border px-2 py-0.5 text-xs transition-colors ${
                active
                  ? "border-ink bg-ink text-paper"
                  : "border-rule text-muted hover:border-ink hover:text-ink"
              }`}
            >
              {label(option)}
            </button>
          );
        })}
      </div>
    </div>
  );
}
