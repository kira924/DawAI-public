import type { ApiErrorBody } from "./types";

const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL ?? "").replace(/\/$/, "");

export class ApiError extends Error {
  readonly status: number;
  readonly details: ApiErrorBody["detail"];

  constructor(status: number, details: ApiErrorBody["detail"]) {
    const message = typeof details === "string" ? details : `Request failed with status ${status}`;
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.details = details;
  }
}

export async function requestJson<T>(
  path: string,
  init: RequestInit = {},
  accessToken?: string,
): Promise<T> {
  const headers = new Headers(init.headers);
  if (init.body && !(init.body instanceof FormData) && !(init.body instanceof URLSearchParams)) {
    headers.set("Content-Type", "application/json");
  }
  if (accessToken) {
    headers.set("Authorization", `Bearer ${accessToken}`);
  }

  const response = await fetch(`${API_BASE_URL}${path}`, {
    ...init,
    headers,
  });

  if (!response.ok) {
    const body = await response.json().catch(() => ({})) as ApiErrorBody;
    throw new ApiError(response.status, body.detail);
  }

  if (response.status === 204) {
    return undefined as T;
  }
  return (await response.json()) as T;
}

export function normalizeApiError(error: unknown): string {
  if (error instanceof ApiError) {
    if (Array.isArray(error.details)) {
      return error.details.map((item) => item.msg).filter(Boolean).join(". ");
    }
    return error.message;
  }
  if (error instanceof TypeError) {
    return "network_unavailable";
  }
  return "unexpected_error";
}
