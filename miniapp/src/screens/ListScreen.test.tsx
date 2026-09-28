// Экран 14: четыре состояния (загрузка, пусто, ошибка, данные).
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { expect, test, vi } from "vitest";

import type { Navigation } from "../router";
import { NavigationContext } from "../router";
import { makeItem } from "../test/fakeSource";
import { texts } from "../texts";
import { type CalendarState, ListScreen } from "./ListScreen";

const TODAY = "2026-09-23";

/** Развёрнутость «Дальше» живёт в оболочке — здесь её заменяет состояние обёртки. */
function Harness({
  calendar,
  onRetry,
  onExpand,
}: {
  calendar: CalendarState;
  onRetry: () => void;
  onExpand: (hidden: number) => void;
}) {
  const [expanded, setExpanded] = useState(false);
  return (
    <ListScreen
      today={TODAY}
      calendar={calendar}
      onRetry={onRetry}
      laterExpanded={expanded}
      onExpandLater={(hidden) => {
        setExpanded(true);
        onExpand(hidden);
      }}
      onCollapseLater={() => setExpanded(false)}
    />
  );
}

function renderList(calendar: CalendarState, onRetry = vi.fn()) {
  const nav: Navigation = {
    route: { name: "list" },
    depth: 1,
    push: vi.fn(),
    back: vi.fn(),
    switchTab: vi.fn(),
    home: vi.fn(),
  };
  const onExpand = vi.fn();
  render(
    <NavigationContext.Provider value={nav}>
      <Harness calendar={calendar} onRetry={onRetry} onExpand={onExpand} />
    </NavigationContext.Provider>,
  );
  return { nav, onRetry, onExpand };
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

test("загрузка: шапка с кнопкой «Профиль» видна сразу, на месте списка скелетон", () => {
  renderList({ items: null, loading: true, error: null });
  expect(screen.getByRole("button", { name: texts.list.profileButton })).toBeInTheDocument();
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

test("дата другого года — год отдельной строкой в .row-date, не наезжает на название (#105)", () => {
  const items = [
    ...ITEMS,
    makeItem({ id: 5, title: "Взнос ИП за 2026 год", due_date: "2027-01-05" }),
  ];
  renderList({ items, loading: false, error: null });
  const rows = screen.getAllByRole("button").filter((b) => b.classList.contains("row"));
  const futureRow = rows.find(
    (r) => r.querySelector(".row-title")?.textContent === "Взнос ИП за 2026 год",
  );
  expect(futureRow).toBeDefined();

  const dateCell = futureRow!.querySelector(".row-date")!;
  expect(dateCell).toHaveAttribute("aria-label", "5 янв 2027");
  const yearCell = dateCell.querySelector(".row-date__year")!;
  expect(yearCell).toHaveTextContent("2027");
  expect(yearCell).toHaveAttribute("aria-hidden", "true");
  // Название не обрезано наездом даты — оно полностью в DOM (обрезка только через CSS).
  expect(futureRow).toHaveTextContent("Взнос ИП за 2026 год");

  // Строка без года — разметка не меняется: просто текст, без .row-date__year.
  const plainRow = rows.find((r) => r.querySelector(".row-title")?.textContent === "Декларация")!;
  const plainDateCell = plainRow.querySelector(".row-date")!;
  expect(plainDateCell.querySelector(".row-date__year")).toBeNull();
  expect(plainDateCell).not.toHaveAttribute("aria-label");
  expect(plainDateCell.textContent).toBe("28 окт");
});

test("шапка: кнопка «Профиль» с иконкой ведёт на экран 19, строки «ИП · …» нет (29.09)", async () => {
  const { nav } = renderList({ items: [], loading: false, error: null });
  const button = screen.getByRole("button", { name: texts.list.profileButton });
  expect(button).toHaveClass("profile-button");
  expect(button.querySelector("svg")).toHaveAttribute("aria-hidden", "true");
  expect(screen.queryByText(/^ИП · /)).not.toBeInTheDocument();
  await userEvent.click(button);
  expect(nav.push).toHaveBeenCalledWith({ name: "profile" });
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

// --- Секция «Дальше» сворачивается до трёх строк (решение человека 29.09) ----------------------

const rowTitles = () =>
  screen
    .getAllByRole("button")
    .filter((b) => b.classList.contains("row"))
    .map((r) => r.querySelector(".row-title")?.textContent);

/** `n` событий секции «Дальше» (после ближайшего воскресенья 27.09) и одно на этой неделе. */
function laterItems(n: number) {
  return [
    makeItem({ id: 100, title: "На неделе", due_date: "2026-09-24" }),
    ...Array.from({ length: n }, (_, i) =>
      makeItem({
        id: i + 1,
        title: `Дальше ${i + 1}`,
        due_date: `2026-10-${String(i + 1).padStart(2, "0")}`,
      }),
    ),
  ];
}

test("«Дальше»: видно 3, «Показать ещё N» разворачивает до конца, «Свернуть» — обратно", async () => {
  const { onExpand } = renderList({ items: laterItems(7), loading: false, error: null });
  expect(rowTitles()).toEqual(["На неделе", "Дальше 1", "Дальше 2", "Дальше 3"]);
  const more = screen.getByRole("button", { name: texts.list.showMore(4) });
  expect(more).toHaveAttribute("aria-expanded", "false");

  await userEvent.click(more);
  expect(onExpand).toHaveBeenCalledWith(4);
  expect(rowTitles()).toEqual([
    "На неделе",
    ...Array.from({ length: 7 }, (_, i) => `Дальше ${i + 1}`),
  ]);
  const less = screen.getByRole("button", { name: texts.list.showLess });
  expect(less).toHaveAttribute("aria-expanded", "true");
  expect(screen.queryByRole("button", { name: texts.list.showMore(4) })).not.toBeInTheDocument();

  await userEvent.click(less);
  expect(rowTitles()).toEqual(["На неделе", "Дальше 1", "Дальше 2", "Дальше 3"]);
  expect(screen.getByRole("button", { name: texts.list.showMore(4) })).toBeInTheDocument();
  expect(onExpand).toHaveBeenCalledOnce();
});

test("«Дальше» из 3 и меньше строк — без кнопки", () => {
  renderList({ items: laterItems(3), loading: false, error: null });
  expect(rowTitles()).toHaveLength(4);
  expect(screen.queryByRole("button", { name: /^Показать ещё/ })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: texts.list.showLess })).not.toBeInTheDocument();
});

test("остальные секции не сворачиваются: 5 просроченных видны все", () => {
  const overdue = Array.from({ length: 5 }, (_, i) =>
    makeItem({
      id: i + 1,
      title: `Просрочка ${i + 1}`,
      due_date: `2026-09-1${i}`,
      status: "overdue",
    }),
  );
  renderList({ items: overdue, loading: false, error: null });
  expect(rowTitles()).toHaveLength(5);
  expect(screen.queryByRole("button", { name: /^Показать ещё/ })).not.toBeInTheDocument();
});
