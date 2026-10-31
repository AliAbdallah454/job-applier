import { useQueryClient } from "@tanstack/react-query";
import { useEffect } from "react";

import { useMailStatus, useSyncMail } from "../api/queries";
import { Button, ErrorNote, formatDate } from "./ui";

const SETUP = {
  table: "The mail table is not deployed yet. Deploy the Application stack, then restart the API.",
  credentials:
    "Connect your mailbox: add MAIL_ADDRESS and MAIL_APP_PASSWORD to .env, then restart the API.",
};

/** Mailbox state and the sync button, shared by the pages that show what came from mail. */
export function MailBar() {
  const client = useQueryClient();
  const status = useMailStatus();
  const sync = useSyncMail();
  const data = status.data;
  const syncing = data?.syncing || sync.isPending;

  // A sync also runs in the background, so a new finish time is what brings new results in.
  useEffect(() => {
    if (!data?.last_synced_at && !data?.last_error) return;
    void client.invalidateQueries({ queryKey: ["applications"] });
    void client.invalidateQueries({ queryKey: ["job-alerts"] });
    void client.invalidateQueries({ queryKey: ["interests"] });
  }, [client, data?.last_synced_at, data?.last_error]);

  if (data?.problem) return <p className="mt-4 text-sm text-muted">{SETUP[data.problem]}</p>;

  return (
    <div className="mt-4 flex flex-col gap-2">
      <div className="flex flex-wrap items-center gap-3">
        <Button disabled={!data || syncing} onClick={() => sync.mutate()}>
          {syncing ? "Reading your mail…" : "Check mail now"}
        </Button>
        <span aria-live="polite" className="text-sm text-muted">
          {data?.last_synced_at
            ? `Last checked ${formatDate(data.last_synced_at)}`
            : "Not checked yet"}
        </span>
      </div>
      {data?.last_error && <p className="border-l-2 border-ink pl-3 text-sm">{data.last_error}</p>}
      <ErrorNote error={status.error ?? sync.error} />
    </div>
  );
}
