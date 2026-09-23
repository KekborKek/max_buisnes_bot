// Управляемый источник данных для тестов: промисы разрешает сам тест.
import { vi } from "vitest";

import type { DataSource } from "../data/source";
import type { CalendarItem, ItemCard, Me, ObligationCard, Profile, TaskCard } from "../types";

export const PROFILE: Profile = {
  income_band: "lt10",
  regime: "usn6",
  has_employees: false,
  timezone: "Europe/Moscow",
  nds_payer: false,
  calendar_built_at: null,
};

export function makeMe(hasProfile = true): Me {
  return {
    user_id: 1,
    first_name: "Тест",
    is_dev: true,
    has_profile: hasProfile,
    profile: hasProfile ? PROFILE : null,
  };
}

export function makeItem(overrides: Partial<CalendarItem> = {}): CalendarItem {
  return {
    type: "obligation",
    id: 1,
    title: "Событие",
    category: "taxes",
    due_date: "2026-10-28",
    original_date: "2026-10-28",
    status: "upcoming",
    done_at: null,
    ...overrides,
  };
}

export function makeObligationCard(overrides: Partial<ObligationCard> = {}): ObligationCard {
  return {
    ...(makeItem() as ObligationCard),
    type: "obligation",
    norm: "Норма, ст. 1",
    source_url: "https://example.com/source",
    howto_steps: ["Шаг первый.", "Шаг второй.", "Шаг третий."],
    howto_link: { label: "Ссылка", url: "https://example.com/howto" },
    penalty_text: "Последствия пропуска.",
    last_checked_at: "2026-09-20",
    ...overrides,
  };
}

export function makeTaskCard(overrides: Partial<TaskCard> = {}): TaskCard {
  return {
    ...makeItem({ type: "task", id: 3, title: "Аренда", due_date: "2026-11-05" }),
    type: "task",
    category: "custom",
    remind_offset_days: 1,
    remind_hour: 10,
    ...overrides,
  };
}

interface Deferred<T> {
  promise: Promise<T>;
  resolve(value: T): void;
  reject(error: unknown): void;
}

function deferred<T>(): Deferred<T> {
  let resolve!: (value: T) => void;
  let reject!: (error: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

/** Каждый вызов метода создаёт отложенный ответ; тест разрешает последний. */
export function fakeSource() {
  const calls = {
    me: [] as Deferred<Me>[],
    calendar: [] as Deferred<CalendarItem[]>[],
    item: [] as Deferred<ItemCard>[],
    markDone: [] as Deferred<ItemCard>[],
    undoDone: [] as Deferred<ItemCard>[],
    reportWrongDate: [] as Deferred<void>[],
    createTask: [] as Deferred<TaskCard>[],
    updateTask: [] as Deferred<TaskCard>[],
    deleteTask: [] as Deferred<void>[],
  };
  function pending<T>(list: Deferred<T>[]): Promise<T> {
    const d = deferred<T>();
    list.push(d);
    return d.promise;
  }
  const source = {
    isMock: false,
    me: vi.fn<DataSource["me"]>(() => pending(calls.me)),
    calendar: vi.fn<DataSource["calendar"]>(() => pending(calls.calendar)),
    item: vi.fn<DataSource["item"]>(() => pending(calls.item)),
    markDone: vi.fn<DataSource["markDone"]>(() => pending(calls.markDone)),
    undoDone: vi.fn<DataSource["undoDone"]>(() => pending(calls.undoDone)),
    reportWrongDate: vi.fn<DataSource["reportWrongDate"]>(() => pending(calls.reportWrongDate)),
    createTask: vi.fn<DataSource["createTask"]>(() => pending(calls.createTask)),
    updateTask: vi.fn<DataSource["updateTask"]>(() => pending(calls.updateTask)),
    deleteTask: vi.fn<DataSource["deleteTask"]>(() => pending(calls.deleteTask)),
    track: vi.fn<DataSource["track"]>(() => Promise.resolve()),
  } satisfies DataSource;
  const last = <T>(list: Deferred<T>[]) => list[list.length - 1];
  return {
    source,
    lastMe: () => last(calls.me),
    lastCalendar: () => last(calls.calendar),
    last: <K extends keyof typeof calls>(name: K) => last(calls[name] as Deferred<unknown>[]),
  };
}
