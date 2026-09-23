import { afterEach, expect, test, vi } from "vitest";

import { initialStack, loadTab, navReducer, parseStartParam, saveTab } from "./router";

afterEach(() => {
  vi.restoreAllMocks();
  localStorage.clear();
});

test("parseStartParam разбирает значения из спеки", () => {
  expect(parseStartParam("item_obligation_1")).toEqual({
    kind: "item",
    itemType: "obligation",
    id: 1,
  });
  expect(parseStartParam("item_task_42")).toEqual({ kind: "item", itemType: "task", id: 42 });
  expect(parseStartParam("task_draft")).toEqual({ kind: "task_draft" });
});

test("parseStartParam игнорирует мусор и чужие диплинки", () => {
  for (const raw of [null, "", "item_event_1", "item_obligation_", "item_task_0", "qr_partner"]) {
    expect(parseStartParam(raw)).toBeNull();
  }
});

test("start_param из бота: 16 или 17 поверх списка 14, иначе — запомненная вкладка", () => {
  expect(initialStack(parseStartParam("item_obligation_1"), "month")).toEqual([
    { name: "list" },
    { name: "card", itemType: "obligation", id: 1, source: "bot" },
  ]);
  expect(initialStack({ kind: "task_draft" }, "month")).toEqual([
    { name: "list" },
    { name: "task", draft: true },
  ]);
  expect(initialStack(null, "month")).toEqual([{ name: "month" }]);
});

test("navReducer: вперёд, назад, назад с главного не уходит, вкладка сбрасывает стек", () => {
  let stack = navReducer([{ name: "list" }], {
    type: "push",
    route: { name: "card", itemType: "task", id: 3, source: "list" },
  });
  expect(stack).toHaveLength(2);
  stack = navReducer(stack, { type: "back" });
  expect(stack).toEqual([{ name: "list" }]);
  expect(navReducer(stack, { type: "back" })).toEqual([{ name: "list" }]);
  expect(navReducer(stack, { type: "tab", tab: "month" })).toEqual([{ name: "month" }]);
});

test("режим «Список | Месяц» запоминается, а без localStorage не падает", () => {
  expect(loadTab()).toBe("list");
  saveTab("month");
  expect(loadTab()).toBe("month");

  vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
    throw new Error("denied");
  });
  vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
    throw new Error("denied");
  });
  expect(() => saveTab("list")).not.toThrow();
  expect(loadTab()).toBe("list");
});
