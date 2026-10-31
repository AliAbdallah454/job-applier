import { downloadUrl, type Generation } from "../api/client";
import { useNoteJobOpened } from "../api/queries";
import { ACTION, DownloadIcon, LeaveIcon } from "./ui";

const DOWNLOADS = [
  ["cv_pdf", "CV"],
  ["motivation_letter_pdf", "Motivation letter"],
  ["cover_letter_pdf", "Cover letter"],
];

/** Each button appears once its file or link exists, so a running generation shows none yet. */
export function GenerationLinks({
  generation,
  posting = true,
}: {
  generation: Generation;
  posting?: boolean;
}) {
  const { generation_id: id, job_title: title, job_url: url } = generation;
  const noteOpened = useNoteJobOpened(generation.job_id);
  return (
    <span className="flex flex-wrap gap-1.5">
      {DOWNLOADS.filter(([name]) => generation.files.includes(name)).map(([name, text]) => (
        <a
          key={name}
          href={downloadUrl(id, name, generation.updated_at)}
          aria-label={`Download ${text.toLowerCase()} for ${title}`}
          className={ACTION}
        >
          <DownloadIcon />
          {text}
        </a>
      ))}
      {posting && url && (
        <a
          href={url}
          target="_blank"
          rel="noreferrer"
          aria-label={`Job posting for ${title}`}
          className={ACTION}
          {...noteOpened}
        >
          Job posting
          <LeaveIcon />
        </a>
      )}
    </span>
  );
}
