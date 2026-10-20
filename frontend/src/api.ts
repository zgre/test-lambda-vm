import { accessToken, getConfig } from "./auth";

export interface Project {
  id: string;
  name: string;
  color: string;
  archived: boolean;
  createdAt: string;
}

export interface Entry {
  id: string;
  projectId: string;
  description: string;
  startedAt: string;
  endedAt: string | null;
  seconds: number;
}

export interface Summary {
  totalSeconds: number;
  byProject: { projectId: string; seconds: number }[];
  byDay: { date: string; seconds: number }[];
}

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const res = await fetch(`${getConfig().apiUrl}${path}`, {
    method,
    headers: {
      authorization: `Bearer ${await accessToken()}`,
      ...(body !== undefined ? { "content-type": "application/json" } : {}),
    },
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  if (res.status === 204) return undefined as T;
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new ApiError(res.status, data.error ?? data.message ?? `Request failed (${res.status})`);
  return data as T;
}

const range = (from: string, to: string) => `from=${encodeURIComponent(from)}&to=${encodeURIComponent(to)}`;

export const api = {
  listProjects: () => request<Project[]>("GET", "/projects"),
  createProject: (name: string, color: string) => request<Project>("POST", "/projects", { name, color }),
  updateProject: (id: string, changes: Partial<Pick<Project, "name" | "color" | "archived">>) =>
    request<Project>("PATCH", `/projects/${id}`, changes),
  deleteProject: (id: string) => request<void>("DELETE", `/projects/${id}`),

  listEntries: (from: string, to: string) => request<Entry[]>("GET", `/entries?${range(from, to)}`),
  createEntry: (e: Pick<Entry, "projectId" | "description" | "startedAt"> & { endedAt: string }) =>
    request<Entry>("POST", "/entries", e),
  updateEntry: (id: string, changes: Partial<Pick<Entry, "projectId" | "description" | "startedAt" | "endedAt">>) =>
    request<Entry>("PATCH", `/entries/${id}`, changes),
  deleteEntry: (id: string) => request<void>("DELETE", `/entries/${id}`),

  getTimer: () => request<{ running: Entry | null }>("GET", "/timer"),
  startTimer: (projectId: string, description: string) =>
    request<{ running: Entry }>("POST", "/timer/start", { projectId, description }),
  stopTimer: () => request<Entry>("POST", "/timer/stop"),

  summary: (from: string, to: string) => request<Summary>("GET", `/reports/summary?${range(from, to)}`),
};
