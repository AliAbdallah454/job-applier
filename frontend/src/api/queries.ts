import {
  useInfiniteQuery,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";

import {
  api,
  type ApplicationStatus,
  type CandidateProfile,
  type Document,
  type Language,
  ApiError,
  previewUrl,
  type Status,
  toApiError,
  unwrap,
} from "./client";

export type JobQuery = {
  tech: string;
  seniority: string;
  category: string;
  contract: string;
  q: string;
};

export function useOptions() {
  return useQuery({
    queryKey: ["options"],
    queryFn: () => unwrap(api.GET("/api/options")),
    staleTime: Infinity,
  });
}

export function useJobs(query: JobQuery) {
  return useInfiniteQuery({
    queryKey: ["jobs", query],
    queryFn: ({ pageParam }) =>
      unwrap(
        api.GET("/api/jobs", {
          params: { query: { ...query, cursor: pageParam ?? undefined } },
        }),
      ),
    initialPageParam: null as string | null,
    getNextPageParam: (page) => page.next_cursor,
  });
}

export function useCvs() {
  return useQuery({ queryKey: ["cvs"], queryFn: () => unwrap(api.GET("/api/cvs")) });
}

export function useUploadCv() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (file: File) => {
      const body = new FormData();
      body.append("file", file);
      return unwrap(
        api.POST("/api/cvs", {
          body: body as never,
          bodySerializer: (value) => value as never,
        }),
      );
    },
    onSuccess: () => client.invalidateQueries({ queryKey: ["cvs"] }),
  });
}

export function useDeleteCv() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (cvId: string) =>
      unwrap(api.DELETE("/api/cvs/{cv_id}", { params: { path: { cv_id: cvId } } })),
    onSuccess: () => client.invalidateQueries({ queryKey: ["cvs"] }),
  });
}

export function useProfile() {
  return useQuery({
    queryKey: ["profile"],
    queryFn: async () => {
      try {
        return await unwrap(api.GET("/api/profile"));
      } catch (error) {
        if (error instanceof ApiError && error.status === 404) return null;
        throw error;
      }
    },
  });
}

export function useSaveProfile() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body: CandidateProfile) => unwrap(api.PUT("/api/profile", { body })),
    onSuccess: (saved) => client.setQueryData(["profile"], saved),
  });
}

export function isActive(status: Status) {
  return status === "queued" || status === "processing";
}

export function useGenerations() {
  return useQuery({
    queryKey: ["generations"],
    queryFn: () => unwrap(api.GET("/api/generations")),
    refetchInterval: (query) =>
      query.state.data?.some((item) => isActive(item.status)) ? 4000 : false,
  });
}

/** Failed runs are polled slowly because SQS can redeliver them to a worker on its own. */
export function pollInterval(status: Status | undefined): number | false {
  if (status && isActive(status)) return 4000;
  return status === "failed" ? 30000 : false;
}

export function useGeneration(id: string) {
  return useQuery({
    queryKey: ["generation", id],
    queryFn: () =>
      unwrap(api.GET("/api/generations/{generation_id}", { params: { path: { generation_id: id } } })),
    refetchInterval: (query) => pollInterval(query.state.data?.status),
  });
}

export function useStartGeneration() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body: { job_id: string; cv_id: string; model_id?: string; language: Language }) =>
      unwrap(api.POST("/api/generations", { body })),
    onSuccess: () => client.invalidateQueries({ queryKey: ["generations"] }),
  });
}

export function useRetry(id: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: () =>
      unwrap(
        api.POST("/api/generations/{generation_id}/retry", {
          params: { path: { generation_id: id } },
        }),
      ),
    onSuccess: () => client.invalidateQueries({ queryKey: ["generation", id] }),
  });
}

export function useRegenerate(id: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: () =>
      unwrap(
        api.POST("/api/generations/{generation_id}/regenerate", {
          params: { path: { generation_id: id } },
        }),
      ),
    onSuccess: () => client.invalidateQueries({ queryKey: ["generations"] }),
  });
}

export function useSource(id: string, document: Document, revision: number) {
  return useQuery({
    queryKey: ["source", id, document, revision],
    queryFn: async () => {
      const response = await fetch(previewUrl(id, `${document}_tex`, String(revision)));
      if (!response.ok) throw toApiError(response.status, await response.json());
      return response.text();
    },
    staleTime: Infinity, // The key names one saved state of the document.
  });
}

export function useFindSourceLine(id: string) {
  return useMutation({
    mutationFn: (position: { page: number; x: number; y: number }) =>
      unwrap(
        api.GET("/api/generations/{generation_id}/source-line", {
          params: { path: { generation_id: id }, query: position },
        }),
      ),
  });
}

export function useEditDocument(id: string, document: Document) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ latex, revision }: { latex: string; revision: number }) =>
      unwrap(
        api.POST("/api/generations/{generation_id}/edit", {
          params: { path: { generation_id: id } },
          body: { document, latex, revision },
        }),
      ),
    onSuccess: async (saved, { latex }) => {
      // A read from before the save would otherwise land afterwards and bring the old CV back.
      await client.cancelQueries({ queryKey: ["generation", id] });
      // Already known, so the page shows the saved CV without a loading state. The source goes
      // first because the generation's new revision is what makes the editor ask for it.
      client.setQueryData(["source", id, document, saved.revisions[document]], latex);
      client.setQueryData(["generation", id], saved);
      return client.invalidateQueries({ queryKey: ["generations"] });
    },
    // After a 409 the page must show the CV that got there first, as saving again replaces it.
    onError: () => client.invalidateQueries({ queryKey: ["generation", id] }),
  });
}

export function useMailStatus() {
  return useQuery({
    queryKey: ["mail-status"],
    queryFn: () => unwrap(api.GET("/api/mail/status")),
    // Syncs also run in the background, so a page left open still notices them.
    refetchInterval: (query) => (query.state.data?.syncing ? 1500 : 60000),
  });
}

export function useSyncMail() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: () => unwrap(api.POST("/api/mail/sync")),
    onSuccess: (status) => client.setQueryData(["mail-status"], { ...status, syncing: true }),
  });
}

export function useApplications() {
  return useQuery({
    queryKey: ["applications"],
    queryFn: () => unwrap(api.GET("/api/applications")),
  });
}

/** The application each tailored CV belongs to, for marking it where the CV is shown. */
export function useApplicationStatuses() {
  const applications = useApplications().data ?? [];
  return new Map(
    applications
      .filter((item) => item.generation_id)
      .map((item) => [item.generation_id as string, item.status]),
  );
}

/** The same, for marking a job wherever it is listed. */
export function useJobApplicationStatuses() {
  const applications = useApplications().data ?? [];
  return new Map(
    applications
      .filter((item) => item.job_id)
      .map((item) => [item.job_id as string, item.status]),
  );
}

/** Link props that note an opened job post; failing to note one must not get in the way. */
export function useNoteJobOpened(jobId: string) {
  const note = useMutation({
    mutationFn: () => api.POST("/api/applications/interests", { body: { job_id: jobId } }),
  });
  const opened = () => note.mutate();
  return { onClick: opened, onAuxClick: opened };
}

export function useInterests() {
  return useQuery({
    queryKey: ["interests"],
    queryFn: () => unwrap(api.GET("/api/applications/interests")),
  });
}

export function useAnswerInterest(jobId: string) {
  const client = useQueryClient();
  const path = { params: { path: { job_id: jobId } } };
  return useMutation({
    mutationFn: async (applied: boolean) => {
      if (applied) {
        await unwrap(api.POST("/api/applications/interests/{job_id}/applied", path));
        return;
      }
      const { error, response } = await api.DELETE("/api/applications/interests/{job_id}", path);
      if (!response.ok) throw toApiError(response.status, error);
    },
    onSuccess: () =>
      Promise.all([
        client.invalidateQueries({ queryKey: ["interests"] }),
        client.invalidateQueries({ queryKey: ["applications"] }),
      ]),
  });
}

export function useChangeApplication() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ id, status }: { id: string; status: ApplicationStatus }) =>
      unwrap(
        api.PATCH("/api/applications/{application_id}", {
          params: { path: { application_id: id } },
          body: { status },
        }),
      ),
    onSuccess: () => client.invalidateQueries({ queryKey: ["applications"] }),
  });
}

export function useRemoveApplication() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: async (id: string) => {
      const { error, response } = await api.DELETE("/api/applications/{application_id}", {
        params: { path: { application_id: id } },
      });
      if (!response.ok) throw toApiError(response.status, error);
    },
    onSuccess: () => client.invalidateQueries({ queryKey: ["applications"] }),
  });
}

export function useJobAlerts() {
  return useQuery({
    queryKey: ["job-alerts"],
    queryFn: () => unwrap(api.GET("/api/mail/alerts")),
  });
}

export function useLoadAlertJob(jobId: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (description?: string) =>
      unwrap(
        api.POST("/api/mail/jobs/{linkedin_id}/description", {
          params: { path: { linkedin_id: jobId.replace(/^linkedin-/, "") } },
          body: { description },
        }),
      ),
    onSuccess: () => client.invalidateQueries({ queryKey: ["job-alerts"] }),
  });
}
