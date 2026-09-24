// Список 14 после изменения в карточке/форме: по ответу бэкенда, без повторной загрузки.
import { expect, test } from "vitest";

import { type MonthCache, removeFromMonths, removeItem, upsertItem, upsertMonths } from "./store";
import { makeItem, makeObligationCard, makeTaskCard } from "./test/fakeSource";

const loadedMonth = (items: ReturnType<typeof makeItem>[]) => ({
  items,
  loading: false,
  error: null,
});

test("месяцы: отметка меняет событие на месте, поля карточки в сетку не попадают", () => {
  const months: MonthCache = { "2026-10": loadedMonth([makeItem({ id: 1 })]) };
  const next = upsertMonths(months, makeObligationCard({ id: 1, status: "done" }));
  expect(next["2026-10"].items!.map((i) => [i.id, i.status])).toEqual([[1, "done"]]);
  expect(next["2026-10"].items![0]).not.toHaveProperty("norm");
});

test("месяцы: перенос задачи уводит её из старого месяца в новый, если тот загружен", () => {
  const task = makeItem({ type: "task", id: 3, category: "custom", due_date: "2026-10-05" });
  const months: MonthCache = {
    "2026-10": loadedMonth([task]),
    "2026-11": loadedMonth([]),
    "2026-12": { items: null, loading: false, error: "network" },
  };
  const moved = upsertMonths(months, makeTaskCard({ id: 3, due_date: "2026-11-20" }));
  expect(moved["2026-10"].items).toEqual([]);
  expect(moved["2026-11"].items!.map((i) => i.due_date)).toEqual(["2026-11-20"]);
  expect(moved["2026-12"]).toBe(months["2026-12"]);

  // Новый месяц не загружен — задача просто уходит из старого, загрузится с месяцем.
  const away = upsertMonths(months, makeTaskCard({ id: 3, due_date: "2027-03-01" }));
  expect(away["2026-10"].items).toEqual([]);
  expect(away["2027-03"]).toBeUndefined();
});

test("месяцы: новая задача добавляется в загруженный месяц; удалённая — пропадает", () => {
  const months: MonthCache = { "2026-10": loadedMonth([makeItem({ id: 1 })]) };
  const added = upsertMonths(months, makeTaskCard({ id: 9, due_date: "2026-10-10" }));
  expect(added["2026-10"].items!.map((i) => i.id)).toEqual([1, 9]);
  expect(removeFromMonths(added, "task", 9)["2026-10"].items!.map((i) => i.id)).toEqual([1]);
  expect(removeFromMonths(added, "obligation", 9)["2026-10"].items).toHaveLength(2);
});

const TODAY = "2026-09-23";

test("отмеченное событие меняется на месте, статус — из ответа бэкенда", () => {
  const items = [makeItem({ id: 1 }), makeItem({ id: 2, title: "Другое" })];
  const next = upsertItem(items, makeObligationCard({ id: 1, status: "done" }), TODAY);
  expect(next.map((i) => [i.id, i.status])).toEqual([
    [1, "done"],
    [2, "upcoming"],
  ]);
  // В список не попадают поля карточки.
  expect(next[0]).not.toHaveProperty("norm");
});

test("новая задача добавляется, только если попадает в окно 90 дней", () => {
  const inWindow = upsertItem([], makeTaskCard({ id: 9, due_date: "2026-09-24" }), TODAY);
  expect(inWindow.map((i) => i.id)).toEqual([9]);
  expect(upsertItem([], makeTaskCard({ id: 9, due_date: "2027-01-10" }), TODAY)).toEqual([]);
});

test("задачу перенесли за окно — строка уходит; удалённая — пропадает", () => {
  const items = [makeItem({ type: "task", id: 3, category: "custom" })];
  expect(upsertItem(items, makeTaskCard({ id: 3, due_date: "2027-03-01" }), TODAY)).toEqual([]);
  expect(removeItem(items, "task", 3)).toEqual([]);
  expect(removeItem(items, "obligation", 3)).toHaveLength(1);
});

test("просроченное событие остаётся в списке после отметки", () => {
  const items = [makeItem({ id: 1, due_date: "2026-09-01", status: "overdue" })];
  const next = upsertItem(
    items,
    makeObligationCard({ id: 1, due_date: "2026-09-01", status: "done" }),
    TODAY,
  );
  expect(next).toHaveLength(1);
  expect(next[0].status).toBe("done");
});
