import { fileUrl, type Generation } from "../api/client";
import { ACTION, DownloadIcon, LeaveIcon } from "./ui";

const DOWNLOADS = [
  ["cv_pdf", "CV"],
  ["motivation_letter_pdf", "Motivation letter"],
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
  return (
    <span className="flex flex-wrap gap-1.5">
      {DOWNLOADS.filter(([name]) => generation.files.includes(name)).map(([name, text]) => (
        <a
          key={name}
          href={fileUrl(id, name, true)}
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
        >
          Job posting
          <LeaveIcon />
        </a>
      )}
    </span>
  );
}
