"use client";

import { useQuery } from "@tanstack/react-query";
import { apiGet, ApiError } from "./client";
import type { ConnectionProvider, EnvSection, Knowledge, LiveSnapshot, MissionDetail, StatusInfo, VoiceCatalogue } from "./types";

export const LIVE_POLL_MS = 1000;
export const STATUS_POLL_MS = 15000;

/** Shared live snapshot. Classic and HUD both read this cache, so switching modes keeps state. */
export function useLive() {
  return useQuery<LiveSnapshot, ApiError>({
    queryKey: ["live"],
    queryFn: () => apiGet<LiveSnapshot>("/api/live", 5000),
    refetchInterval: LIVE_POLL_MS, // TanStack pauses polling while the tab is hidden
    retry: false,
  });
}

export function useStatus() {
  return useQuery<StatusInfo, ApiError>({
    queryKey: ["status"],
    queryFn: () => apiGet<StatusInfo>("/api/status"),
    refetchInterval: STATUS_POLL_MS,
    retry: false,
  });
}

export type LinkState = "online" | "offline" | "disconnected" | "connecting";

/** online: agent running. offline: server reachable but agent down. disconnected: server unreachable. */
export function useLinkState(): LinkState {
  const { data, error, isPending } = useLive();
  if (isPending) return "connecting";
  if (error) return "disconnected";
  return data?.online ? "online" : "offline";
}

export function useKnowledge() {
  return useQuery<Knowledge, ApiError>({
    queryKey: ["knowledge"],
    queryFn: () => apiGet<Knowledge>("/api/knowledge", 15000),
    refetchInterval: 30000,
    retry: false,
  });
}

/** Detail for one mission. The backend answers 404 with an error for unknown ids; refresh only while it runs. */
export function useMission(id: string | null, running: boolean) {
  return useQuery<MissionDetail, ApiError>({
    queryKey: ["mission", id],
    enabled: !!id,
    queryFn: async () => {
      const d = await apiGet<MissionDetail>(`/api/missions?id=${encodeURIComponent(id!)}`, 15000);
      if (!d.mission || d.mission.id !== id) throw new ApiError("invalid", "The server returned a different mission than requested.");
      return d;
    },
    refetchInterval: running ? 4000 : false,
    retry: false,
  });
}

export function useConnections() {
  return useQuery<{ providers: ConnectionProvider[] }, ApiError>({
    queryKey: ["connections"],
    queryFn: () => apiGet("/api/connections", 15000),
    retry: false,
  });
}

export function useEnv() {
  return useQuery<{ sections: EnvSection[] }, ApiError>({
    queryKey: ["env"],
    queryFn: () => apiGet("/api/env", 15000),
    retry: false,
    refetchOnWindowFocus: false,
  });
}

export function useVoices() {
  return useQuery<VoiceCatalogue, ApiError>({
    queryKey: ["voices"],
    queryFn: () => apiGet<VoiceCatalogue>("/api/voices", 15000),
    retry: false,
  });
}