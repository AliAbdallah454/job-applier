import {
  useInfiniteQuery,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";

import {
  api,
  type CandidateProfile,
  ApiError,
  fileUrl,
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
    mutationFn: (body: { job_id: string; cv_id: string; model_id?: string }) =>
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

export function useCvSource(id: string) {
  return useQuery({
    queryKey: ["cv-source", id],
    queryFn: async () => {
      const response = await fetch(fileUrl(id, "cv_tex"));
      if (!response.ok) throw toApiError(response.status, await response.json());
      return response.text();
    },
  });
}

export function useEditCv(id: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (cvLatex: string) =>
      unwrap(
        api.POST("/api/generations/{generation_id}/edit", {
          params: { path: { generation_id: id } },
          body: { cv_latex: cvLatex },
        }),
      ),
    onSuccess: (saved, cvLatex) => {
      // Already known, so the page moves to the new version without a loading state.
      client.setQueryData(["generation", saved.generation_id], saved);
      client.setQueryData(["cv-source", saved.generation_id], cvLatex);
      return client.invalidateQueries({ queryKey: ["generations"] });
    },
  });
}
