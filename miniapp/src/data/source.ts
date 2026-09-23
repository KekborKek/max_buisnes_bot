// Источник данных мини-приложения — ЕДИНСТВЕННОЕ место, где выбирается мок или настоящий API.
// T11b: переключить экраны на API = поправить getDataSource() (и, если надо, httpSource).
import type { CalendarItem, Me } from "../types";
import { httpSource } from "./http";
import { createMockSource, isMockScenario, type MockScenario } from "./mock";

export interface DataSource {
  /** true — ТЕСТОВЫЕ ДАННЫЕ, на экране видна пометка. */
  readonly isMock: boolean;
  me(startParam: string | null): Promise<Me>;
  /** События за период [from; to] плюс все просроченные без отметки. Даты — YYYY-MM-DD. */
  calendar(from: string, to: string): Promise<CalendarItem[]>;
  track(name: string, props?: Record<string, unknown>): Promise<void>;
}

/**
 * Моки включаются только явно:
 * - `?mock=<сценарий>` в dev-сборке (`make dev-miniapp`);
 * - `VITE_MOCKS=1` при сборке — сценарий из `?mock=`, по умолчанию `list`.
 */
export function readMockScenario(
  search: string,
  env: { DEV: boolean; VITE_MOCKS?: string },
): MockScenario | null {
  const fromQuery = new URLSearchParams(search).get("mock");
  const allowed = env.DEV || env.VITE_MOCKS === "1";
  if (!allowed) return null;
  if (fromQuery && isMockScenario(fromQuery)) return fromQuery;
  return env.VITE_MOCKS === "1" ? "list" : null;
}

export function getDataSource(): DataSource {
  const scenario = readMockScenario(window.location.search, import.meta.env);
  return scenario ? createMockSource(scenario) : httpSource;
}
