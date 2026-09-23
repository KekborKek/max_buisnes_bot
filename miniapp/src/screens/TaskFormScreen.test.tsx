// Экран 17: валидация у поля, создание, правка, черновик из бота, ошибка сохранения, «Отмена».
import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, test, vi } from "vitest";

import { ApiError } from "../data/http";
import { type Navigation, NavigationContext } from "../router";
import { ToastProvider } from "../shell/Toast";
import { type CalendarStore, CalendarStoreContext, itemKey } from "../store";
import { fakeSource, makeTaskCard } from "../test/fakeSource";
import { texts } from "../texts";
import type { ItemCard, TaskDraft } from "../types";
import { TaskFormScreen, validate } from "./TaskFormScreen";

const TODAY = "2026-09-23";

function renderForm(
  opts: { draft?: TaskDraft | null; taskId?: number; cards?: Record<string, ItemCard> } = {},
) {
  const fake = fakeSource();
  const nav: Navigation = {
    route: { name: "task", draft: Boolean(opts.draft), taskId: opts.taskId },
    depth: 2,
    push: vi.fn(),
    back: vi.fn(),
    switchTab: vi.fn(),
    home: vi.fn(),
  };
  const store: CalendarStore = {
    items: [],
    cards: opts.cards ?? {},
    upsert: vi.fn(),
    remove: vi.fn(),
  };
  render(
    <ToastProvider>
      <NavigationContext.Provider value={nav}>
        <CalendarStoreContext.Provider value={store}>
          <TaskFormScreen
            source={fake.source}
            today={TODAY}
            draft={opts.draft ?? null}
            taskId={opts.taskId}
          />
        </CalendarStoreContext.Provider>
      </NavigationContext.Provider>
    </ToastProvider>,
  );
  return { ...fake, nav, store };
}

const nameField = () => screen.getByLabelText(texts.form.name);
const dateField = () => screen.getByLabelText(texts.form.date);
const saveButton = () => screen.getByRole("button", { name: texts.form.save });

test("validate: название обязательно, дата — не раньше сегодняшней", () => {
  expect(validate({ title: "Аренда", date: TODAY }, TODAY)).toEqual({ title: null, date: null });
  expect(validate({ title: "   ", date: "2026-09-24" }, TODAY).title).toBe(texts.form.nameRequired);
  expect(validate({ title: "Аренда", date: "2026-09-22" }, TODAY).date).toBe(texts.form.datePast);
  expect(validate({ title: "Аренда", date: "" }, TODAY).date).toBe(texts.form.dateRequired);
});

test("новая задача: по умолчанию завтра, «За 1 день», 10:00; «Сохранить» неактивна", () => {
  renderForm();
  expect(screen.getByText(texts.form.titleNew)).toBeInTheDocument();
  expect(nameField()).toHaveValue("");
  expect(nameField()).toHaveAttribute("placeholder", texts.form.namePlaceholder);
  expect(dateField()).toHaveValue("2026-09-24");
  expect(dateField()).toHaveAttribute("min", TODAY);
  expect(screen.getByRole("radio", { name: texts.form.remind1 })).toBeChecked();
  expect(screen.getByRole("radio", { name: texts.form.hour(10) })).toBeChecked();
  expect(screen.getAllByRole("radio")).toHaveLength(7);
  expect(saveButton()).toBeDisabled();
  // Подсказка не пугает сразу — только после ввода.
  expect(screen.queryByText(texts.form.nameRequired)).not.toBeInTheDocument();
});

test("пустое название: подсказка у поля, «Сохранить» неактивна", async () => {
  renderForm();
  await userEvent.type(nameField(), "А");
  expect(saveButton()).toBeEnabled();
  await userEvent.clear(nameField());
  const hint = screen.getByText(texts.form.nameRequired);
  expect(nameField()).toHaveAttribute("aria-describedby", hint.id);
  expect(nameField()).toHaveAttribute("aria-invalid", "true");
  expect(saveButton()).toBeDisabled();
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
});

test("дата в прошлом: datePast у поля, «Сохранить» неактивна", async () => {
  renderForm();
  await userEvent.type(nameField(), "Аренда");
  await userEvent.clear(dateField());
  await userEvent.type(dateField(), "2026-09-01");
  const hint = screen.getByText(texts.form.datePast);
  expect(dateField()).toHaveAttribute("aria-describedby", hint.id);
  expect(saveButton()).toBeDisabled();
});

test("название до 60 символов, счётчик — после 50", async () => {
  renderForm();
  await userEvent.type(nameField(), "а".repeat(50));
  expect(screen.queryByText("10")).not.toBeInTheDocument();
  await userEvent.type(nameField(), "б".repeat(15));
  expect(nameField()).toHaveValue("а".repeat(50) + "б".repeat(10));
  expect(screen.getByText("0")).toBeInTheDocument();
});

test("создание за два нажатия и одно поле: POST, поля заблокированы, экран 14 + «добавлена»", async () => {
  const { source, last, nav, store } = renderForm();
  await userEvent.type(nameField(), "  Оплатить аренду ");
  await userEvent.click(saveButton());
  expect(source.createTask).toHaveBeenCalledWith({
    title: "Оплатить аренду",
    due_date: "2026-09-24",
    remind_offset_days: 1,
    remind_hour: 10,
  });
  expect(nameField()).toBeDisabled();
  expect(dateField()).toBeDisabled();
  expect(screen.getByRole("button", { name: texts.form.cancel })).toBeDisabled();

  const created = makeTaskCard({ id: 9, title: "Оплатить аренду", due_date: "2026-09-24" });
  await act(async () => last("createTask").resolve(created));
  expect(store.upsert).toHaveBeenCalledWith(created);
  expect(nav.home).toHaveBeenCalledOnce();
  expect(screen.getByText(texts.form.created)).toBeInTheDocument();
  // task_created пишет бэкенд в POST /api/tasks.
  expect(source.track).not.toHaveBeenCalledWith("task_created", expect.anything());
});

test("выбранные напоминание и время уходят в запрос", async () => {
  const { source } = renderForm();
  await userEvent.type(nameField(), "Аренда");
  await userEvent.click(screen.getByRole("radio", { name: texts.form.remind7 }));
  await userEvent.click(screen.getByRole("radio", { name: texts.form.hour(18) }));
  await userEvent.click(saveButton());
  expect(source.createTask).toHaveBeenCalledWith(
    expect.objectContaining({ remind_offset_days: 7, remind_hour: 18 }),
  );
});

test("ошибка сохранения: форма не закрывается, введённое на месте, «Повторить» работает", async () => {
  const { source, last, nav } = renderForm();
  await userEvent.type(nameField(), "Аренда");
  await userEvent.click(screen.getByRole("radio", { name: texts.form.remind3 }));
  await userEvent.click(saveButton());
  await act(async () => last("createTask").reject(new ApiError("network")));

  expect(screen.getByRole("alert")).toHaveTextContent(texts.common.error);
  expect(nameField()).toHaveValue("Аренда");
  expect(nameField()).toBeEnabled();
  expect(screen.getByRole("radio", { name: texts.form.remind3 })).toBeChecked();
  expect(nav.home).not.toHaveBeenCalled();
  expect(source.track).toHaveBeenCalledWith("error", { where: "task_form", kind: "network" });

  await userEvent.click(screen.getByRole("button", { name: texts.common.retry }));
  expect(source.createTask).toHaveBeenCalledTimes(2);
  await act(async () => last("createTask").resolve(makeTaskCard()));
  expect(nav.home).toHaveBeenCalledOnce();
});

test("черновик из чата подставляется в поля", () => {
  renderForm({ draft: { title: "Оплатить аренду", due_date: "2026-11-05" } });
  expect(nameField()).toHaveValue("Оплатить аренду");
  expect(dateField()).toHaveValue("2026-11-05");
  expect(saveButton()).toBeEnabled();
});

test("черновик без даты («Выбрать дату»): название из чата, дата — завтра", () => {
  renderForm({ draft: { title: "Сдать отчёт", due_date: null } });
  expect(nameField()).toHaveValue("Сдать отчёт");
  expect(dateField()).toHaveValue("2026-09-24");
});

test("правка: поля из карточки, PATCH, назад на 16 + «Задача изменена»", async () => {
  const task = makeTaskCard({ id: 3, title: "Аренда", remind_offset_days: 3, remind_hour: 18 });
  const { source, last, nav, store } = renderForm({
    taskId: 3,
    cards: { [itemKey("task", 3)]: task },
  });
  expect(screen.getByText(texts.form.titleEdit)).toBeInTheDocument();
  expect(nameField()).toHaveValue("Аренда");
  expect(dateField()).toHaveValue("2026-11-05");
  expect(screen.getByRole("radio", { name: texts.form.remind3 })).toBeChecked();
  expect(screen.getByRole("radio", { name: texts.form.hour(18) })).toBeChecked();

  await userEvent.clear(dateField());
  await userEvent.type(dateField(), "2026-11-10");
  await userEvent.click(saveButton());
  expect(source.updateTask).toHaveBeenCalledWith(3, {
    title: "Аренда",
    due_date: "2026-11-10",
    remind_offset_days: 3,
    remind_hour: 18,
  });
  const updated = { ...task, due_date: "2026-11-10" };
  await act(async () => last("updateTask").resolve(updated));
  expect(store.upsert).toHaveBeenCalledWith(updated);
  expect(nav.back).toHaveBeenCalledOnce();
  expect(screen.getByText(texts.form.updated)).toBeInTheDocument();
});

test("«Отмена»: ничего не ввели — сразу назад", async () => {
  const { nav } = renderForm();
  await userEvent.click(screen.getByRole("button", { name: texts.form.cancel }));
  expect(nav.back).toHaveBeenCalledOnce();
});

test("«Отмена» после ввода: сначала «Удалить черновик?», второе нажатие — назад", async () => {
  const { nav } = renderForm();
  await userEvent.type(nameField(), "Аренда");
  await userEvent.click(screen.getByRole("button", { name: texts.form.cancel }));
  expect(nav.back).not.toHaveBeenCalled();
  await userEvent.click(screen.getByRole("button", { name: texts.form.discardConfirm }));
  expect(nav.back).toHaveBeenCalledOnce();
});
