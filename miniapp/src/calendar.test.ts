import { expect, test } from "vitest";

import {
  addDays,
  addMonths,
  dateIn,
  dayCategories,
  formatCardDate,
  formatDate,
  formatNumericDate,
  formatShortDate,
  groupSections,
  itemsByDay,
  monthDiff,
  monthGrid,
  monthNumber,
  monthRange,
  todayIn,
  weekEnd,
} from "./calendar";
import { makeItem } from "./test/fakeSource";

test("addMonths и monthDiff переходят через год", () => {
  expect(addMonths("2026-12", 1)).toBe("2027-01");
  expect(addMonths("2026-01", -1)).toBe("2025-12");
  expect(addMonths("2026-09", 12)).toBe("2027-09");
  expect(addMonths("2026-09", -12)).toBe("2025-09");
  expect(monthDiff("2026-10", "2027-01")).toBe(3);
  expect(monthDiff("2026-10", "2025-10")).toBe(-12);
  expect(monthNumber("2026-10")).toBe(9);
});

test("monthRange — первое и последнее число, февраль високосного года", () => {
  expect(monthRange("2026-10")).toEqual({ from: "2026-10-01", to: "2026-10-31" });
  expect(monthRange("2027-02")).toEqual({ from: "2027-02-01", to: "2027-02-28" });
  expect(monthRange("2028-02")).toEqual({ from: "2028-02-01", to: "2028-02-29" });
  expect(monthRange("2026-12")).toEqual({ from: "2026-12-01", to: "2026-12-31" });
});

test("monthGrid начинается с понедельника: пустые клетки до 1-го числа", () => {
  const october = monthGrid("2026-10"); // 1 октября 2026 — четверг
  expect(october.slice(0, 4)).toEqual([null, null, null, "2026-10-01"]);
  expect(october).toHaveLength(3 + 31);
  expect(october.at(-1)).toBe("2026-10-31");
  expect(monthGrid("2026-06")[0]).toBe("2026-06-01"); // понедельник — без пустых клеток
  expect(monthGrid("2026-11").indexOf("2026-11-01")).toBe(6); // воскресенье
  expect(monthGrid("2028-02").at(-1)).toBe("2028-02-29");
});

test("itemsByDay: только свой месяц, выполненные в конце дня", () => {
  const days = itemsByDay(
    [
      makeItem({ id: 1, due_date: "2026-10-28", status: "done" }),
      makeItem({ id: 2, due_date: "2026-10-28" }),
      makeItem({ id: 3, due_date: "2026-10-05" }),
      // Просроченное из другого месяца: бэкенд добавляет такие к любому периоду.
      makeItem({ id: 4, due_date: "2026-09-01", status: "overdue" }),
    ],
    "2026-10",
  );
  expect([...days.keys()].sort()).toEqual(["2026-10-05", "2026-10-28"]);
  expect(days.get("2026-10-28")!.map((i) => i.id)).toEqual([2, 1]);
});

test("dayCategories: одна точка на категорию, не больше трёх, порядок product.md", () => {
  expect(
    dayCategories([
      makeItem({ category: "reports" }),
      makeItem({ category: "taxes" }),
      makeItem({ category: "taxes" }),
    ]),
  ).toEqual(["taxes", "reports"]);
  expect(
    dayCategories([
      makeItem({ category: "custom" }),
      makeItem({ category: "reports" }),
      makeItem({ category: "contributions" }),
      makeItem({ category: "taxes" }),
    ]),
  ).toEqual(["taxes", "contributions", "reports"]);
  expect(dayCategories([])).toEqual([]);
});

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

test("formatCardDate: «28 октября, среда», год — только если не текущий", () => {
  expect(formatCardDate("2026-10-28", "2026-09-23")).toBe("28 октября, среда");
  expect(formatCardDate("2027-01-05", "2026-09-23")).toBe("5 января 2027, вторник");
  expect(formatDate("2026-05-05", "2026-09-23")).toBe("5 мая");
  expect(formatNumericDate("2026-09-20")).toBe("20.09.2026");
  // Отметку поставили в 23:30 по Москве — во Владивостоке это уже следующий день.
  expect(dateIn("Asia/Vladivostok", "2026-10-27T20:30:00Z")).toBe("2026-10-28");
});
