// HTTP-клиент API мини-приложения. Контракт — openapi.yaml в корне репозитория.
// Все запросы — с таймаутом; ошибки разделены по видам, чтобы экран показал нужный текст.
import { getInitData, getWebApp } from "../bridge";
import type { CalendarItem, Me } from "../types";
import type { DataSource } from "./source";

/** Вид ошибки: 401 ≠ сеть ≠ таймаут ≠ 5xx. */
export type ErrorKind = "unauthorized" | "network" | "timeout" | "server" | "client";

export class ApiError extends Error {
  constructor(
    public kind: ErrorKind,
    public status: number | null = null,
  ) {
    super(`api_${kind}${status ? `_${status}` : ""}`);
  }
}

export const REQUEST_TIMEOUT_MS = 10_000;

export function errorKind(e: unknown): ErrorKind {
  return e instanceof ApiError ? e.kind : "network";
}

export async function request<T>(
  path: string,
  init: RequestInit = {},
  timeoutMs = REQUEST_TIMEOUT_MS,
): Promise<T> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  let res: Response;
  try {
    res = await fetch(path, {
      ...init,
      signal: controller.signal,
      headers: {
        "Content-Type": "application/json",
        "X-Max-Init-Data": getInitData(),
        ...(init.headers ?? {}),
      },
    });
  } catch {
    throw new ApiError(controller.signal.aborted ? "timeout" : "network");
  } finally {
    clearTimeout(timer);
  }
  if (res.status === 401) throw new ApiError("unauthorized", 401);
  if (res.status >= 500) throw new ApiError("server", res.status);
  if (!res.ok) throw new ApiError("client", res.status);
  if (res.status === 204) return undefined as T;
  try {
    return (await res.json()) as T;
  } catch {
    // Забытый маршрут прокси отвечает 200 с HTML мини-аппа — это не данные.
    throw new ApiError("server", res.status);
  }
}

function query(params: Record<string, string>): string {
  return new URLSearchParams(params).toString();
}

/** Настоящий API. Эндпоинт календаря появится в T10; переключение — в source.ts (T11b). */
export const httpSource: DataSource = {
  isMock: false,
  me: (startParam) =>
    request<Me>(
      // Вне MAX бэкенд в dev-режиме принимает start_param запросом; с подписью — игнорирует.
      !getWebApp() && startParam ? `/api/me?${query({ start_param: startParam })}` : "/api/me",
    ),
  calendar: (from, to) => request<CalendarItem[]>(`/api/calendar?${query({ from, to })}`),
  track: (name, props = {}) =>
    request<void>("/api/events", { method: "POST", body: JSON.stringify({ name, props }) }),
};
