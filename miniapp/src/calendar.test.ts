import { expect, test } from "vitest";

import { addDays, formatShortDate, groupSections, todayIn, weekEnd } from "./calendar";
import { makeItem } from "./test/fakeSource";

test("todayIn считает «сегодня» в поясе пользователя", () => {
  const now = new Date("2026-10-27T20:30:00Z"); // в Москве 23:30, во Владивостоке уже 28-е
  expect(todayIn("Europe/Moscow", now)).toBe("2026-10-27");
  expect(todayIn("Asia/Vladivostok", now)).toBe("2026-10-28");
  expect(todayIn("Not/AZone", now)).toBe("2026-10-27");
});

test("addDays переходит через месяц и год", () => {
  expect(addDays("2026-12-30", 3)).toBe("2027-01-02");
  expect(addDays("2026-10-28", 90)).toBe("2027-01-26");
});

test("weekEnd — ближайшее воскресенье включительно", () => {
  expect(weekEnd("2026-09-23")).toBe("2026-09-27"); // среда
  expect(weekEnd("2026-09-27")).toBe("2026-09-27"); // воскресенье
  expect(weekEnd("2026-09-28")).toBe("2026-10-04"); // понедельник
});

test("секции идут по порядку, просроченные выше остальных, пустых секций нет", () => {
  const today = "2026-09-23";
  const sections = groupSections(
    [
      makeItem({ id: 1, due_date: "2026-10-28" }),
      makeItem({ id: 2, due_date: "2026-09-27" }),
      makeItem({ id: 3, due_date: "2026-09-23", status: "today" }),
      makeItem({ id: 4, due_date: "2026-09-01", status: "overdue" }),
    ],
    today,
  );
  expect(sections.map((s) => [s.key, s.items.map((i) => i.id)])).toEqual([
    ["overdue", [4]],
    ["today", [3]],
    ["week", [2]],
    ["later", [1]],
  ]);
  expect(groupSections([makeItem({ due_date: "2026-10-28" })], today).map((s) => s.key)).toEqual([
    "later",
  ]);
});

test("выполненные остаются в своей секции и опускаются в её конец", () => {
  const today = "2026-09-23";
  const sections = groupSections(
    [
      makeItem({ id: 1, due_date: "2026-09-24", status: "done" }),
      makeItem({ id: 2, due_date: "2026-09-26" }),
      makeItem({ id: 3, due_date: "2026-09-10", status: "done" }),
      makeItem({ id: 4, due_date: "2026-09-15", status: "overdue" }),
    ],
    today,
  );
  expect(sections.map((s) => [s.key, s.items.map((i) => i.id)])).toEqual([
    ["overdue", [4, 3]],
    ["week", [2, 1]],
  ]);
});

test("статус бэкенда важнее даты: мини-апп его не пересчитывает", () => {
  // Бэкенд считает «сегодня» в поясе пользователя — у фронта может быть другой день.
  const sections = groupSections(
    [makeItem({ due_date: "2026-09-24", status: "today" })],
    "2026-09-23",
  );
  expect(sections[0].key).toBe("today");
});

test("formatShortDate: «28 окт», год — только если не текущий", () => {
  expect(formatShortDate("2026-10-28", "2026-09-23")).toBe("28 окт");
  expect(formatShortDate("2026-05-05", "2026-09-23")).toBe("5 мая");
  expect(formatShortDate("2027-01-05", "2026-09-23")).toBe("5 янв 2027");
});
