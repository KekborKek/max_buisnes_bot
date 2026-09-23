import { afterEach, expect, test, vi } from "vitest";

import { ApiError, request } from "./http";
import { readMockScenario } from "./source";

afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

async function kindOf(p: Promise<unknown>): Promise<string> {
  try {
    await p;
    return "ok";
  } catch (e) {
    return e instanceof ApiError ? e.kind : "other";
  }
}

function respond(status: number, body = "{}", type = "application/json") {
  vi.stubGlobal(
    "fetch",
    vi.fn(
      async () =>
        new Response(status === 204 ? null : body, { status, headers: { "Content-Type": type } }),
    ),
  );
}

test("401 ≠ ошибка сети ≠ 5xx", async () => {
  respond(401);
  expect(await kindOf(request("/api/me"))).toBe("unauthorized");

  vi.stubGlobal(
    "fetch",
    vi.fn(async () => {
      throw new TypeError("Failed to fetch");
    }),
  );
  expect(await kindOf(request("/api/me"))).toBe("network");

  respond(503);
  expect(await kindOf(request("/api/me"))).toBe("server");

  respond(404);
  expect(await kindOf(request("/api/me"))).toBe("client");
});

test("таймаут отменяет запрос через AbortController", async () => {
  vi.useFakeTimers();
  vi.stubGlobal(
    "fetch",
    vi.fn(
      (_url: string, init: RequestInit) =>
        new Promise((_resolve, reject) => {
          init.signal!.addEventListener("abort", () => reject(new DOMException("", "AbortError")));
        }),
    ),
  );
  const result = kindOf(request("/api/me", {}, 1000));
  await vi.advanceTimersByTimeAsync(1000);
  expect(await result).toBe("timeout");
});

test("HTML вместо JSON (забытый маршрут прокси) — ошибка, а не данные", async () => {
  respond(200, "<!doctype html><html></html>", "text/html");
  expect(await kindOf(request("/api/calendar"))).toBe("server");
});

test("успешный ответ и 204", async () => {
  respond(200, '{"user_id": 1}');
  await expect(request("/api/me")).resolves.toEqual({ user_id: 1 });
  respond(204);
  await expect(request("/api/events")).resolves.toBeUndefined();
});

test("заголовок X-Max-Init-Data уходит в каждом запросе", async () => {
  respond(200);
  await request("/api/me");
  const init = vi.mocked(fetch).mock.calls[0][1] as RequestInit;
  expect((init.headers as Record<string, string>)["X-Max-Init-Data"]).toBe("dev");
});

test("моки включаются только явно", () => {
  expect(readMockScenario("", { DEV: true })).toBeNull();
  expect(readMockScenario("?mock=empty", { DEV: true })).toBe("empty");
  expect(readMockScenario("?mock=nonsense", { DEV: true })).toBeNull();
  expect(readMockScenario("?mock=empty", { DEV: false })).toBeNull();
  expect(readMockScenario("", { DEV: false, VITE_MOCKS: "1" })).toBe("list");
  expect(readMockScenario("?mock=no_profile", { DEV: false, VITE_MOCKS: "1" })).toBe("no_profile");
});
