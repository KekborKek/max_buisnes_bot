// Экран 15: четыре состояния (загрузка, пусто, ошибка, данные), пустой день, границы листания.
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { expect, test, vi } from "vitest";

import type { Navigation } from "../router";
import { NavigationContext } from "../router";
import type { MonthCache } from "../store";
import { makeItem } from "../test/fakeSource";
import { texts } from "../texts";
import type { CalendarItem } from "../types";
import { MonthScreen } from "./MonthScreen";

const TODAY = "2026-10-14";

function Harness({
  months,
  onLoad,
  onRetry,
}: {
  months: MonthCache;
  onLoad: (month: string) => void;
  onRetry: (month: string) => void;
}) {
  const [selected, setSelected] = useState<string | null>(null);
  return (
    <MonthScreen
      today={TODAY}
      months={months}
      selected={selected}
      onSelect={setSelected}
      onLoad={onLoad}
      onRetry={onRetry}
    />
  );
}

function renderMonth(months: MonthCache = {}) {
  const nav: Navigation = {
    route: { name: "month" },
    depth: 1,
    push: vi.fn(),
    back: vi.fn(),
    switchTab: vi.fn(),
    home: vi.fn(),
  };
  const onLoad = vi.fn();
  const onRetry = vi.fn();
  const view = render(
    <NavigationContext.Provider value={nav}>
      <Harness months={months} onLoad={onLoad} onRetry={onRetry} />
    </NavigationContext.Provider>,
  );
  const navButtons = () =>
    Array.from(view.container.querySelectorAll<HTMLButtonElement>(".month-nav button"));
  return {
    nav,
    onLoad,
    onRetry,
    container: view.container,
    prev: () => navButtons()[0],
    next: () => navButtons()[1],
  };
}

const loaded = (items: CalendarItem[]): MonthCache => ({
  "2026-10": { items, loading: false, error: null },
});
const day = (label: RegExp | string) =>
  screen.getByRole("button", { name: typeof label === "string" ? new RegExp(`^${label}`) : label });
const dayButtons = (container: HTMLElement) => container.querySelectorAll(".month-day");

const ITEMS = [
  makeItem({ id: 1, title: "Декларация", category: "reports", due_date: "2026-10-28" }),
  makeItem({ id: 2, title: "Аванс", category: "taxes", due_date: "2026-10-28", status: "done" }),
  makeItem({ id: 3, title: "Взнос", category: "contributions", due_date: "2026-10-14" }),
];

test("загрузка: шапка и сетка сразу, точек нет, под сеткой скелетон", () => {
  const { onLoad, container } = renderMonth();
  expect(onLoad).toHaveBeenCalledWith("2026-10");
  expect(screen.getByRole("button", { name: texts.list.profileButton })).toBeInTheDocument();
  expect(screen.getByText("Октябрь 2026")).toBeInTheDocument();
  for (const w of texts.month.weekdays) expect(screen.getByText(w)).toBeInTheDocument();
  expect(dayButtons(container)).toHaveLength(31);
  expect(container.querySelectorAll(".month-day .dot")).toHaveLength(0);
  expect(screen.getByTestId("skeleton")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: texts.list.tabMonth })).toHaveAttribute(
    "aria-pressed",
    "true",
  );
});

test("шапка: кнопка «Профиль» ведёт на экран 19", async () => {
  const { nav } = renderMonth();
  await userEvent.click(screen.getByRole("button", { name: texts.list.profileButton }));
  expect(nav.push).toHaveBeenCalledWith({ name: "profile" });
});

test("месяц пуст: monthEmpty в предложном падеже, «+ Задача» одна и ведёт на форму с датой сегодня", async () => {
  // Просроченное из другого месяца (бэкенд добавляет такие к периоду) месяц не наполняет.
  const { nav } = renderMonth(
    loaded([makeItem({ due_date: "2026-09-01", status: "overdue", title: "Старое" })]),
  );
  expect(screen.getByText(texts.month.monthEmpty("октябре"))).toBeInTheDocument();
  expect(screen.getByText("В октябре обязательных сроков нет")).toBeInTheDocument();
  expect(screen.queryByText("Старое")).not.toBeInTheDocument();
  expect(screen.queryByText(texts.month.dayEmpty)).not.toBeInTheDocument();
  // Кнопка не дублируется: в пустом месяце нижней панели bottom-bar нет.
  expect(screen.getAllByRole("button", { name: texts.month.addTask })).toHaveLength(1);
  await userEvent.click(screen.getByRole("button", { name: texts.month.addTask }));
  expect(nav.push).toHaveBeenCalledWith({
    name: "task",
    draft: false,
    date: TODAY,
    from: "month",
  });
});

test("месяц с событиями: «+ Задача» в нижней панели всегда, дата — выбранный (сегодняшний) день", async () => {
  const { nav } = renderMonth(loaded(ITEMS));
  // Ровно одна кнопка «+ Задача» — в нижней панели, не в пустом блоке (события есть).
  expect(screen.getAllByRole("button", { name: texts.month.addTask })).toHaveLength(1);
  await userEvent.click(screen.getByRole("button", { name: texts.month.addTask }));
  expect(nav.push).toHaveBeenLastCalledWith({
    name: "task",
    draft: false,
    date: TODAY,
    from: "month",
  });
});

test("«+ Задача» открывает форму с выбранным днём — и будущим, и прошедшим (D32, #108)", async () => {
  const { nav, prev } = renderMonth(loaded(ITEMS));
  // Будущий день этого месяца.
  await userEvent.click(day("28 октября, Налоги, Отчётность"));
  await userEvent.click(screen.getByRole("button", { name: texts.month.addTask }));
  expect(nav.push).toHaveBeenLastCalledWith({
    name: "task",
    draft: false,
    date: "2026-10-28",
    from: "month",
  });

  // Прошедший день этого месяца — эта же дата, не завтра: задача задним числом.
  await userEvent.click(day("3 октября"));
  await userEvent.click(screen.getByRole("button", { name: texts.month.addTask }));
  expect(nav.push).toHaveBeenLastCalledWith({
    name: "task",
    draft: false,
    date: "2026-10-03",
    from: "month",
  });

  // Ушли в прошлый месяц (сентябрь, весь он раньше «сегодня» 2026-10-14) — выбор 1-го числа.
  await userEvent.click(prev());
  await userEvent.click(screen.getByRole("button", { name: texts.month.addTask }));
  expect(nav.push).toHaveBeenLastCalledWith({
    name: "task",
    draft: false,
    date: "2026-09-01",
    from: "month",
  });
});

test("день пуст: dayEmpty, сетка с точками остаётся", async () => {
  const { container } = renderMonth(loaded(ITEMS));
  await userEvent.click(day("3 октября"));
  expect(screen.getByText(texts.month.dayEmpty)).toBeInTheDocument();
  expect(container.querySelectorAll(".month-day .dot").length).toBeGreaterThan(0);
  expect(screen.queryByText(/обязательных сроков нет/)).not.toBeInTheDocument();
});

test("ошибка без данных: плашка и «Повторить», сетка остаётся", async () => {
  const { onRetry, container } = renderMonth({
    "2026-10": { items: null, loading: false, error: "network" },
  });
  const alert = screen.getByRole("alert");
  expect(alert).toHaveTextContent(texts.common.error);
  expect(dayButtons(container)).toHaveLength(31);
  expect(screen.queryByTestId("skeleton")).not.toBeInTheDocument();
  await userEvent.click(within(alert).getByRole("button", { name: texts.common.retry }));
  expect(onRetry).toHaveBeenCalledWith("2026-10");
});

test("ошибка при загруженном месяце: события и точки остаются под плашкой", () => {
  renderMonth({ "2026-10": { items: ITEMS, loading: false, error: "timeout" } });
  expect(screen.getByRole("alert")).toHaveTextContent(texts.common.error);
  expect(screen.getByText("Взнос")).toBeInTheDocument();
});

test("данные: точки по категориям, сегодня выбрано, выбор дня меняет список", async () => {
  const { container } = renderMonth(loaded(ITEMS));
  const today = day("14 октября");
  expect(today).toHaveAttribute("aria-pressed", "true");
  expect(today).toHaveAttribute("aria-current", "date");
  expect(today).toHaveClass("month-day--today", "month-day--selected");
  expect(screen.getByText("Взнос")).toBeInTheDocument();

  const busy = day("28 октября, Налоги, Отчётность");
  const dots = Array.from(busy.querySelectorAll(".dot")).map((d) =>
    d.getAttribute("data-category"),
  );
  expect(dots).toEqual(["taxes", "reports"]);
  expect(day("13 октября").querySelectorAll(".dot")).toHaveLength(0);
  expect(container.querySelectorAll(".month-day--selected")).toHaveLength(1);

  await userEvent.click(busy);
  expect(busy).toHaveAttribute("aria-pressed", "true");
  expect(busy).toHaveClass("month-day--selected");
  expect(day("14 октября")).toHaveAttribute("aria-pressed", "false");
  const rows = screen.getAllByRole("button").filter((b) => b.classList.contains("row"));
  // Выполненное — в конце дня, с галочкой, как на экране 14.
  expect(rows.map((r) => r.querySelector(".row-title")?.textContent)).toEqual([
    "Декларация",
    "Аванс",
  ]);
  expect(within(rows[1]).getByLabelText(texts.status.done)).toBeInTheDocument();
  expect(screen.queryByText("Взнос")).not.toBeInTheDocument();
});

test("строка события открывает ту же карточку 16, что из списка, с source=month", async () => {
  const { nav } = renderMonth(loaded(ITEMS));
  await userEvent.click(screen.getByText("Взнос"));
  expect(nav.push).toHaveBeenCalledWith({
    name: "card",
    itemType: "obligation",
    id: 3,
    source: "month",
  });
});

test("листание: 12 месяцев вперёд и назад, дальше стрелка неактивна; «Сегодня» возвращает", async () => {
  const { prev, next, onLoad } = renderMonth(loaded(ITEMS));
  expect(next()).toHaveAccessibleName("Ноябрь 2026");
  expect(prev()).toHaveAccessibleName("Сентябрь 2026");

  for (let i = 0; i < 12; i++) await userEvent.click(next());
  expect(screen.getByText("Октябрь 2027")).toBeInTheDocument();
  expect(next()).toBeDisabled();
  expect(prev()).toBeEnabled();
  expect(onLoad).toHaveBeenCalledWith("2027-10");
  // В чужом месяце выбрано 1-е число.
  expect(day("1 октября 2027")).toHaveAttribute("aria-pressed", "true");

  await userEvent.click(screen.getByRole("button", { name: texts.month.today }));
  expect(screen.getByText("Октябрь 2026")).toBeInTheDocument();
  expect(day("14 октября")).toHaveAttribute("aria-pressed", "true");

  for (let i = 0; i < 12; i++) await userEvent.click(prev());
  expect(screen.getByText("Октябрь 2025")).toBeInTheDocument();
  expect(prev()).toBeDisabled();
  expect(next()).toBeEnabled();
  expect(onLoad).toHaveBeenCalledWith("2025-10");
});
