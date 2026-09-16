// Клиент API мини-приложения. Контракт — openapi.yaml в корне репозитория.
import { getInitData } from "./bridge";

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const res = await fetch(path, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      "X-Max-Init-Data": getInitData(),
      ...(init.headers ?? {}),
    },
  });
  if (!res.ok) throw new ApiError(res.status, await res.text());
  return (res.status === 204 ? undefined : await res.json()) as T;
}

export interface Me {
  user_id: number;
  first_name: string | null;
  is_dev: boolean;
}

export const api = {
  me: () => request<Me>("/api/me"),
  track: (name: string, props: Record<string, unknown> = {}) =>
    request<void>("/api/events", { method: "POST", body: JSON.stringify({ name, props }) }),
};
