import type { ExperimentsResponse, MaintenanceDetail, PlayerTrace } from "./types";

async function requestJson<T>(url: string): Promise<T> {
  const response = await fetch(url, { headers: { Accept: "application/json" } });
  if (!response.ok) {
    const text = await response.text();
    throw new Error(`${response.status} ${response.statusText}: ${text.slice(0, 500)}`);
  }
  const payload = (await response.json()) as T & { error?: string };
  if (payload && typeof payload === "object" && payload.error) {
    throw new Error(String(payload.error));
  }
  return payload as T;
}

export function fetchExperiments(): Promise<ExperimentsResponse> {
  return requestJson<ExperimentsResponse>("/api/maintenance/experiments");
}

export function fetchExperimentDetail(id: string): Promise<MaintenanceDetail> {
  return requestJson<MaintenanceDetail>(`/api/maintenance/experiment?id=${encodeURIComponent(id)}&projection=compact`);
}

export function fetchPlayerTrace(id: string, options: { taskId?: string; phases?: string[]; compact?: boolean } = {}): Promise<PlayerTrace> {
  const params = new URLSearchParams({ id });
  if (options.taskId) params.set("task_id", options.taskId);
  if (options.phases?.length) params.set("phase", options.phases.join(","));
  if (options.compact) params.set("scope", "compact");
  return requestJson<PlayerTrace>(`/api/maintenance/player?${params.toString()}`);
}
