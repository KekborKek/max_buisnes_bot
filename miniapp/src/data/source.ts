// Источник данных мини-приложения — ЕДИНСТВЕННОЕ место, где выбирается мок или настоящий API.
// T11b: переключить экраны на API = поправить getDataSource() (и, если надо, httpSource).
import type { CalendarItem, ItemCard, ItemType, Me, TaskCard, TaskInput } from "../types";
import { httpSource } from "./http";
import { createMockSource, isMockScenario, type MockScenario } from "./mock";

/**
 * Эндпоинты — docs/spec/data-model.md §3. События item_done / item_undone / task_created /
 * wrong_date_reported пишет бэкенд в этих эндпоинтах; мини-апп их сам не шлёт.
 */
export interface DataSource {
  /** true — ТЕСТОВЫЕ ДАННЫЕ, на экране видна пометка. */
  readonly isMock: boolean;
  me(startParam: string | null): Promise<Me>;
  /** События за период [from; to] плюс все просроченные без отметки. Даты — YYYY-MM-DD. */
  calendar(from: string, to: string): Promise<CalendarItem[]>;
  /** GET /api/items/{type}/{id}. Нет такого (удалили, чужое) — ApiError("client", 404). */
  item(type: ItemType, id: number): Promise<ItemCard>;
  /** POST /api/items/{type}/{id}/done — идемпотентно; ответ — карточка с новым статусом. */
  markDone(type: ItemType, id: number): Promise<ItemCard>;
  /** DELETE /api/items/{type}/{id}/done — ответ — карточка со статусом от бэкенда. */
  undoDone(type: ItemType, id: number): Promise<ItemCard>;
  /** POST /api/obligations/{id}/report — «Неверный срок», 204. */
  reportWrongDate(id: number): Promise<void>;
  /** POST /api/tasks. */
  createTask(input: TaskInput): Promise<TaskCard>;
  /** PATCH /api/tasks/{id}. */
  updateTask(id: number, input: TaskInput): Promise<TaskCard>;
  /** DELETE /api/tasks/{id}, 204. */
  deleteTask(id: number): Promise<void>;
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
