// Список 14 после изменения в карточке/форме: по ответу бэкенда, без повторной загрузки.
import { expect, test } from "vitest";

import { removeItem, upsertItem } from "./store";
import { makeItem, makeObligationCard, makeTaskCard } from "./test/fakeSource";

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
