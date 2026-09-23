// Экран 14: четыре состояния (загрузка, пусто, ошибка, данные).
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, test, vi } from "vitest";

import type { Navigation } from "../router";
import { NavigationContext } from "../router";
import { makeItem, PROFILE } from "../test/fakeSource";
import { texts } from "../texts";
import { type CalendarState, ListScreen } from "./ListScreen";

const TODAY = "2026-09-23";

function renderList(calendar: CalendarState, onRetry = vi.fn()) {
  const nav: Navigation = {
    route: { name: "list" },
    depth: 1,
    push: vi.fn(),
    back: vi.fn(),
    switchTab: vi.fn(),
    home: vi.fn(),
  };
  render(
    <NavigationContext.Provider value={nav}>
      <ListScreen profile={PROFILE} today={TODAY} calendar={calendar} onRetry={onRetry} />
    </NavigationContext.Provider>,
  );
  return { nav, onRetry };
}

const ITEMS = [
  makeItem({ id: 1, title: "Декларация", category: "reports", due_date: "2026-10-28" }),
  makeItem({ id: 2, title: "Аванс", due_date: "2026-09-20", status: "overdue" }),
  makeItem({
    id: 3,
    type: "task",
    title: "Аренда",
    category: "custom",
    due_date: "2026-09-25",
    status: "done",
  }),
  makeItem({ id: 4, title: "Взнос", category: "contributions", due_date: "2026-09-24" }),
];

test("загрузка: шапка с профилем видна сразу, на месте списка скелетон", () => {
  renderList({ items: null, loading: true, error: null });
  expect(screen.getByText("ИП · УСН 6% · без сотрудников")).toBeInTheDocument();
  expect(screen.getByTestId("skeleton").children).toHaveLength(3);
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
});

test("пусто: заголовок, пояснение, «+ Задача» и «Открыть месяц»", async () => {
  const { nav } = renderList({ items: [], loading: false, error: null });
  expect(screen.getByText(texts.list.emptyTitle)).toBeInTheDocument();
  expect(screen.getByText(texts.list.emptyText)).toBeInTheDocument();
  expect(screen.getAllByRole("button", { name: texts.list.addTask })).toHaveLength(1);
  await userEvent.click(screen.getByRole("button", { name: texts.list.emptyMonth }));
  expect(nav.switchTab).toHaveBeenCalledWith("month");
});

test("ошибка без данных: плашка common.error и «Повторить»", async () => {
  const { onRetry } = renderList({ items: null, loading: false, error: "network" });
  const alert = screen.getByRole("alert");
  expect(alert).toHaveTextContent(texts.common.error);
  await userEvent.click(within(alert).getByRole("button", { name: texts.common.retry }));
  expect(onRetry).toHaveBeenCalledOnce();
  expect(screen.queryByText(texts.list.emptyTitle)).not.toBeInTheDocument();
});

test("ошибка при загруженном списке: список остаётся под плашкой", () => {
  renderList({ items: ITEMS, loading: false, error: "timeout" });
  expect(screen.getByRole("alert")).toHaveTextContent(texts.common.error);
  expect(screen.getByText("Декларация")).toBeInTheDocument();
});

test("данные: секции по порядку, просроченные сверху, статусы словами", () => {
  renderList({ items: ITEMS, loading: false, error: null });
  const rows = screen.getAllByRole("button").filter((b) => b.classList.contains("row"));
  expect(rows.map((r) => r.querySelector(".row-title")?.textContent)).toEqual([
    "Аванс",
    "Взнос",
    "Аренда",
    "Декларация",
  ]);
  expect(
    screen.getAllByText(
      new RegExp(
        `^(${texts.list.sectionOverdue}|${texts.list.sectionWeek}|${texts.list.sectionLater})$`,
      ),
    ),
  ).toHaveLength(4); // три заголовка секций + статус «Просрочено» у строки
  expect(screen.queryByText(texts.list.sectionToday)).not.toBeInTheDocument();

  const overdue = rows[0];
  expect(overdue).toHaveTextContent("20 сент");
  expect(overdue).toHaveTextContent(texts.list.statusOverdue);
  expect(overdue).toHaveTextContent(texts.category.taxes);
  // Выполненное — в конце своей секции, с галочкой и подписью для скринридера.
  expect(within(rows[2]).getByLabelText(texts.status.done)).toBeInTheDocument();
  expect(rows[2]).toHaveTextContent(texts.category.custom);
});

test("строка ведёт на карточку, «+ Задача» — на форму", async () => {
  const { nav } = renderList({ items: ITEMS, loading: false, error: null });
  await userEvent.click(screen.getByText("Декларация"));
  expect(nav.push).toHaveBeenCalledWith({
    name: "card",
    itemType: "obligation",
    id: 1,
    source: "list",
  });
  await userEvent.click(screen.getByRole("button", { name: texts.list.addTask }));
  expect(nav.push).toHaveBeenCalledWith({ name: "task", draft: false });
});

test("переключатель «Список | Месяц»: активная вкладка отмечена", () => {
  renderList({ items: [], loading: false, error: null });
  expect(screen.getByRole("button", { name: texts.list.tabList })).toHaveAttribute(
    "aria-pressed",
    "true",
  );
  expect(screen.getByRole("button", { name: texts.list.tabMonth })).toHaveAttribute(
    "aria-pressed",
    "false",
  );
});
