// Управляемый источник данных для тестов: промисы разрешает сам тест.
import { vi } from "vitest";

import type { DataSource } from "../data/source";
import type { CalendarItem, Me, Profile } from "../types";

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

/** Каждый вызов me()/calendar() создаёт отложенный ответ; тест разрешает последний. */
export function fakeSource() {
  const meCalls: Deferred<Me>[] = [];
  const calendarCalls: Deferred<CalendarItem[]>[] = [];
  const source = {
    isMock: false,
    me: vi.fn(() => {
      const d = deferred<Me>();
      meCalls.push(d);
      return d.promise;
    }),
    calendar: vi.fn(() => {
      const d = deferred<CalendarItem[]>();
      calendarCalls.push(d);
      return d.promise;
    }),
    track: vi.fn(() => Promise.resolve()),
  } satisfies DataSource;
  return {
    source,
    lastMe: () => meCalls[meCalls.length - 1],
    lastCalendar: () => calendarCalls[calendarCalls.length - 1],
  };
}
