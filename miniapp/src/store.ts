// Общие данные календаря для экранов 14/15/16/17: список, месяцы сетки и загруженные карточки.
// Изменение в карточке или форме сразу видно в списке и в сетке — без повторной загрузки.
import { createContext, useCallback, useContext, useRef, useState } from "react";

import { addDays, LIST_WINDOW_DAYS, monthOf, monthRange } from "./calendar";
import { errorKind, type ErrorKind } from "./data/http";
import type { DataSource } from "./data/source";
import type { CalendarItem, ItemCard, ItemType } from "./types";
import { toCalendarItem } from "./types";

export const itemKey = (type: ItemType, id: number) => `${type}_${id}`;

/**
 * Список после изменения события по ответу бэкенда. Уже показанное событие меняется на месте
 * (или уходит, если срок перенесли за окно списка); новое добавляется, если попадает в окно.
 */
export function upsertItem(items: CalendarItem[], card: ItemCard, today: string): CalendarItem[] {
  const next = toCalendarItem(card);
  const windowEnd = addDays(today, LIST_WINDOW_DAYS);
  const inWindow = next.due_date >= today && next.due_date <= windowEnd;
  const index = items.findIndex((i) => i.type === next.type && i.id === next.id);
  if (index === -1) return inWindow ? [...items, next] : items;
  if (next.due_date > windowEnd) return items.filter((_, i) => i !== index);
  return items.map((item, i) => (i === index ? next : item));
}

export function removeItem(items: CalendarItem[], type: ItemType, id: number): CalendarItem[] {
  return items.filter((i) => !(i.type === type && i.id === id));
}

export function findItem(
  items: CalendarItem[] | null,
  type: ItemType,
  id: number,
): CalendarItem | null {
  return items?.find((i) => i.type === type && i.id === id) ?? null;
}

/** Месяц сетки 15: события только этого месяца (null — ещё не загружен). */
export interface MonthState {
  items: CalendarItem[] | null;
  loading: boolean;
  error: ErrorKind | null;
}

/** Загруженные за сессию месяцы по ключу «YYYY-MM». Нет ключа — месяц ещё грузится. */
export type MonthCache = Record<string, MonthState>;

/**
 * Месяцы после изменения события: из всех загруженных месяцев оно уходит, в месяц своего срока
 * (если тот загружен) — встаёт на место или добавляется. Перенос задачи на другой месяц — так же.
 */
export function upsertMonths(months: MonthCache, card: ItemCard): MonthCache {
  const next = toCalendarItem(card);
  const target = monthOf(next.due_date);
  const result: MonthCache = {};
  for (const [key, state] of Object.entries(months)) {
    const items = state.items;
    if (!items) {
      result[key] = state;
      continue;
    }
    const index = items.findIndex((i) => i.type === next.type && i.id === next.id);
    let updated = items;
    if (key === target) {
      updated = index === -1 ? [...items, next] : items.map((it, i) => (i === index ? next : it));
    } else if (index !== -1) {
      updated = items.filter((_, i) => i !== index);
    }
    result[key] = updated === items ? state : { ...state, items: updated };
  }
  return result;
}

export function removeFromMonths(months: MonthCache, type: ItemType, id: number): MonthCache {
  const result: MonthCache = {};
  for (const [key, state] of Object.entries(months)) {
    result[key] = state.items ? { ...state, items: removeItem(state.items, type, id) } : state;
  }
  return result;
}

export interface Months {
  cache: MonthCache;
  /** Загрузить месяц, если его ещё нет в кеше и он не грузится. Функция стабильна. */
  load(month: string): void;
  /** «Повторить» после ошибки. Функция стабильна. */
  retry(month: string): void;
  upsert(card: ItemCard): void;
  remove(type: ItemType, id: number): void;
  /** Календарь пересобран (экран 19): кеш сбрасывается, месяцы грузятся заново. Стабильна. */
  reset(): void;
}

const quiet = (p: Promise<unknown>) => void p.catch(() => undefined);

/**
 * Кеш месяцев сетки на время сессии (docs/screens/15-month.md, «Данные»). Живёт в оболочке,
 * а не в экране: после карточки 16 и «Назад» месяц не загружается заново.
 */
export function useMonths(source: DataSource): Months {
  const [cache, setCache] = useState<MonthCache>({});
  const loaded = useRef(new Set<string>());
  const inFlight = useRef(new Set<string>());
  // Поколение кеша: ответ, запрошенный до reset(), в новый кеш не попадает.
  const generation = useRef(0);

  const request = useCallback(
    (month: string) => {
      if (inFlight.current.has(month)) return;
      inFlight.current.add(month);
      const gen = generation.current;
      const { from, to } = monthRange(month);
      source.calendar(from, to).then(
        (items) => {
          if (gen !== generation.current) return;
          inFlight.current.delete(month);
          loaded.current.add(month);
          const own = items.filter((i) => monthOf(i.due_date) === month);
          setCache((c) => ({ ...c, [month]: { items: own, loading: false, error: null } }));
        },
        (e: unknown) => {
          if (gen !== generation.current) return;
          inFlight.current.delete(month);
          const kind = errorKind(e);
          // Уже загруженные события месяца остаются на экране под плашкой.
          setCache((c) => ({
            ...c,
            [month]: { items: c[month]?.items ?? null, loading: false, error: kind },
          }));
          quiet(source.track("error", { where: "month", kind }));
        },
      );
    },
    [source],
  );

  const load = useCallback(
    (month: string) => {
      if (!loaded.current.has(month)) request(month);
    },
    [request],
  );

  const retry = useCallback(
    (month: string) => {
      setCache((c) => ({
        ...c,
        [month]: { items: c[month]?.items ?? null, loading: true, error: c[month]?.error ?? null },
      }));
      request(month);
    },
    [request],
  );

  const upsert = useCallback((card: ItemCard) => setCache((c) => upsertMonths(c, card)), []);
  const remove = useCallback(
    (type: ItemType, id: number) => setCache((c) => removeFromMonths(c, type, id)),
    [],
  );

  const reset = useCallback(() => {
    generation.current += 1;
    loaded.current.clear();
    inFlight.current.clear();
    setCache({});
  }, []);

  return { cache, load, retry, upsert, remove, reset };
}

export interface CalendarStore {
  /** Список 14 (null — ещё не загружен). Карточка берёт из него заголовок и дату сразу. */
  items: CalendarItem[] | null;
  /** Карточки, загруженные за этот запуск, по itemKey(). */
  cards: Record<string, ItemCard>;
  /** Бэкенд вернул карточку: обновить кэш карточек, список и месяцы. Функция стабильна. */
  upsert(card: ItemCard): void;
  /** Событие удалено или его больше нет. Функция стабильна. */
  remove(type: ItemType, id: number): void;
}

export const CalendarStoreContext = createContext<CalendarStore | null>(null);

export function useCalendarStore(): CalendarStore {
  const store = useContext(CalendarStoreContext);
  if (!store) throw new Error("useCalendarStore вне CalendarStoreContext");
  return store;
}
