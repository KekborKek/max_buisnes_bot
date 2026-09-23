// Общие данные календаря для экранов 14/15/16/17: список и загруженные карточки.
// Изменение в карточке или форме сразу видно в списке — без повторной загрузки (задача T12).
import { createContext, useContext } from "react";

import { addDays, LIST_WINDOW_DAYS } from "./calendar";
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

export interface CalendarStore {
  /** Список 14 (null — ещё не загружен). Карточка берёт из него заголовок и дату сразу. */
  items: CalendarItem[] | null;
  /** Карточки, загруженные за этот запуск, по itemKey(). */
  cards: Record<string, ItemCard>;
  /** Бэкенд вернул карточку: обновить кэш карточек и список. Функция стабильна. */
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
