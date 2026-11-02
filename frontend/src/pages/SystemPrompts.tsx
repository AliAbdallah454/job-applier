import { type FormEvent, useState } from "react";

import type { SavedSystemPrompt } from "../api/client";
import { useDeleteSystemPrompt, useSaveSystemPrompt, useSystemPrompts } from "../api/queries";
import { Button, Empty, ErrorNote, Eyebrow } from "../components/ui";

function PromptForm({ prompt, onDone }: { prompt?: SavedSystemPrompt; onDone: () => void }) {
  const save = useSaveSystemPrompt();
  const [name, setName] = useState(prompt?.name ?? "");
  const [text, setText] = useState(prompt?.text ?? "");
  const [isDefault, setIsDefault] = useState(prompt?.is_default ?? false);
  const submit = (event: FormEvent) => {
    event.preventDefault();
    save.mutate(
      { id: prompt?.prompt_id, name, text, is_default: isDefault },
      { onSuccess: onDone },
    );
  };

  return (
    <form onSubmit={submit} className="border border-rule p-4">
      <label className="block text-sm font-medium">
        Name
        <input
          required
          maxLength={80}
          value={name}
          onChange={(event) => setName(event.target.value)}
          placeholder="For example: Backend roles"
          className="mt-2 block w-full border border-rule bg-wash p-2 font-normal outline-none focus:border-ink"
        />
      </label>
      <label className="mt-4 block text-sm font-medium">
        What should the model always keep in mind?
        <textarea
          required
          rows={6}
          maxLength={10000}
          value={text}
          onChange={(event) => setText(event.target.value)}
          placeholder="For example: keep a plain, direct tone, and lead with my cloud work."
          className="mt-2 block w-full border border-rule bg-wash p-2 font-normal outline-none focus:border-ink"
        />
      </label>
      <label className="mt-3 flex cursor-pointer items-center gap-2 text-sm">
        <input
          type="checkbox"
          className="accent-current"
          checked={isDefault}
          onChange={(event) => setIsDefault(event.target.checked)}
        />
        Select this one by default when tailoring
      </label>
      <div className="mt-3 empty:hidden">
        <ErrorNote error={save.error} />
      </div>
      <div className="mt-4 flex justify-end gap-2">
        {prompt && <Button onClick={onDone}>Cancel</Button>}
        <Button primary type="submit" disabled={save.isPending}>
          {save.isPending ? "Saving…" : prompt ? "Save changes" : "Add system prompt"}
        </Button>
      </div>
    </form>
  );
}

export function SystemPrompts() {
  const prompts = useSystemPrompts();
  const save = useSaveSystemPrompt();
  const remove = useDeleteSystemPrompt();
  const [formKey, setFormKey] = useState(0);
  const [editing, setEditing] = useState<string | null>(null);
  const [confirming, setConfirming] = useState<string | null>(null);
  const setDefault = (prompt: SavedSystemPrompt, isDefault: boolean) =>
    save.mutate({
      id: prompt.prompt_id,
      name: prompt.name,
      text: prompt.text,
      is_default: isDefault,
    });

  return (
    <div className="mx-auto w-full max-w-3xl p-4 sm:p-6">
      <h1 className="text-3xl font-bold tracking-tight">System prompts</h1>
      <p className="mt-1 max-w-prose text-sm text-muted">
        Saved instructions for the model, such as your tone or what to emphasise. You pick one each
        time you tailor a CV, and your default is already selected. Your CV and profile stay the only
        source of facts, so a prompt cannot make the model claim anything they do not support.
      </p>
      <div className="mt-5">
        <PromptForm key={formKey} onDone={() => setFormKey((key) => key + 1)} />
      </div>
      <div className="mt-3 empty:hidden">
        <ErrorNote error={prompts.error ?? save.error ?? remove.error} />
      </div>
      <ul className="mt-4">
        {prompts.data?.map((prompt) => (
          <li key={prompt.prompt_id} className="rise border-t border-rule py-3">
            {editing === prompt.prompt_id ? (
              <PromptForm prompt={prompt} onDone={() => setEditing(null)} />
            ) : (
              <>
                <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2">
                  <span className="flex min-w-0 items-center gap-2">
                    <span className="truncate text-sm font-medium">{prompt.name}</span>
                    {prompt.is_default && <Eyebrow>Default</Eyebrow>}
                  </span>
                  {confirming === prompt.prompt_id ? (
                    <span className="flex gap-2">
                      <Button onClick={() => setConfirming(null)}>Keep</Button>
                      <Button primary onClick={() => remove.mutate(prompt.prompt_id)}>
                        Delete
                      </Button>
                    </span>
                  ) : (
                    <span className="flex flex-wrap gap-2">
                      <Button
                        disabled={save.isPending}
                        onClick={() => setDefault(prompt, !prompt.is_default)}
                      >
                        {prompt.is_default ? "Remove default" : "Make default"}
                      </Button>
                      <Button onClick={() => setEditing(prompt.prompt_id)}>Edit</Button>
                      <Button onClick={() => setConfirming(prompt.prompt_id)}>Delete</Button>
                    </span>
                  )}
                </div>
                <p className="mt-2 line-clamp-3 text-sm break-words whitespace-pre-wrap text-muted">
                  {prompt.text}
                </p>
              </>
            )}
          </li>
        ))}
      </ul>
      {prompts.data?.length === 0 && <Empty>No system prompts yet.</Empty>}
    </div>
  );
}
