// Экран 17: валидация у поля, создание, правка, черновик из бота, ошибка сохранения, «Отмена»,
// задачи задним числом (D32, #108).
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
  opts: {
    draft?: TaskDraft | null;
    taskId?: number;
    cards?: Record<string, ItemCard>;
    initialDate?: string;
    onCreatedFromMonth?: (dueDate: string) => void;
  } = {},
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
  const { unmount } = render(
    <ToastProvider>
      <NavigationContext.Provider value={nav}>
        <CalendarStoreContext.Provider value={store}>
          <TaskFormScreen
            source={fake.source}
            today={TODAY}
            draft={opts.draft ?? null}
            taskId={opts.taskId}
            initialDate={opts.initialDate}
            onCreatedFromMonth={opts.onCreatedFromMonth}
          />
        </CalendarStoreContext.Provider>
      </NavigationContext.Provider>
    </ToastProvider>,
  );
  return { ...fake, nav, store, unmount };
}

const nameField = () => screen.getByLabelText(texts.form.name);
const dateField = () => screen.getByLabelText(texts.form.date);
const timeField = () => screen.getByLabelText(texts.form.time);
const saveButton = () => screen.getByRole("button", { name: texts.form.save });
const doneSwitch = () => screen.queryByRole("switch", { name: texts.form.alreadyDone });

test("validate: название и дата обязательны, дата в прошлом — можно, время — пока поле видно", () => {
  expect(validate({ title: "Аренда", date: TODAY, time: "10:00" }, TODAY)).toEqual({
    title: null,
    date: null,
    time: null,
  });
  expect(validate({ title: "   ", date: "2026-09-24", time: "10:00" }, TODAY).title).toBe(
    texts.form.nameRequired,
  );
  // Дата в прошлом (D32): ошибки нет, а время не проверяется — поле скрыто.
  expect(validate({ title: "Аренда", date: "2026-09-22", time: "" }, TODAY)).toEqual({
    title: null,
    date: null,
    time: null,
  });
  expect(validate({ title: "Аренда", date: "", time: "10:00" }, TODAY).date).toBe(
    texts.form.dateRequired,
  );
  expect(validate({ title: "Аренда", date: "2026-09-24", time: "" }, TODAY).time).toBe(
    texts.form.timeRequired,
  );
});

test("новая задача: по умолчанию завтра, «За 1 день», 10:00; «Сохранить» неактивна", () => {
  renderForm();
  expect(screen.getByText(texts.form.titleNew)).toBeInTheDocument();
  expect(nameField()).toHaveValue("");
  expect(nameField()).toHaveAttribute("placeholder", texts.form.namePlaceholder);
  expect(dateField()).toHaveValue("2026-09-24");
  // Ограничения снизу нет: задачу можно внести задним числом (D32).
  expect(dateField()).not.toHaveAttribute("min");
  expect(screen.queryByText(texts.form.pastNoRemind)).not.toBeInTheDocument();
  expect(doneSwitch()).not.toBeInTheDocument();
  expect(screen.getByRole("radio", { name: texts.form.remind1 })).toBeChecked();
  expect(timeField()).toHaveValue("10:00");
  expect(screen.getAllByRole("radio")).toHaveLength(4);
  expect(saveButton()).toBeDisabled();
  // Подсказка не пугает сразу — только после ввода.
  expect(screen.queryByText(texts.form.nameRequired)).not.toBeInTheDocument();
});

test("пустое время: timeRequired у поля, «Сохранить» неактивна", async () => {
  renderForm();
  await userEvent.type(nameField(), "Аренда");
  expect(saveButton()).toBeEnabled();
  await userEvent.clear(timeField());
  const hint = screen.getByText(texts.form.timeRequired);
  expect(timeField()).toHaveAttribute("aria-describedby", hint.id);
  expect(timeField()).toHaveAttribute("aria-invalid", "true");
  expect(saveButton()).toBeDisabled();
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

test("дата в прошлом: «Напомнить» и «Время» скрыты, подсказка и «Уже выполнено» (выкл.)", async () => {
  renderForm();
  await userEvent.type(nameField(), "Аренда");
  await userEvent.clear(dateField());
  await userEvent.type(dateField(), "2026-09-01");
  expect(screen.getByText(texts.form.pastNoRemind)).toBeInTheDocument();
  expect(screen.queryAllByRole("radio")).toHaveLength(0);
  expect(screen.queryByLabelText(texts.form.time)).not.toBeInTheDocument();
  expect(doneSwitch()).not.toBeChecked();
  expect(saveButton()).toBeEnabled();

  // Вернули будущую дату — блоки на месте, переключателя нет.
  await userEvent.clear(dateField());
  await userEvent.type(dateField(), "2026-09-30");
  expect(screen.queryByText(texts.form.pastNoRemind)).not.toBeInTheDocument();
  expect(screen.getAllByRole("radio")).toHaveLength(4);
  expect(doneSwitch()).not.toBeInTheDocument();
});

test("дата в прошлом без «Уже выполнено»: POST без done", async () => {
  const { source } = renderForm({ initialDate: "2026-09-20" });
  await userEvent.type(nameField(), "Аренда");
  await userEvent.click(saveButton());
  expect(source.createTask).toHaveBeenCalledWith({
    title: "Аренда",
    due_date: "2026-09-20",
    remind_offset_days: 1,
    remind_hour: 10,
    remind_minute: 0,
  });
});

test("«Уже выполнено» уходит в запрос: done=true", async () => {
  const { source, last, nav } = renderForm({ initialDate: "2026-09-20" });
  await userEvent.type(nameField(), "Аренда");
  await userEvent.click(doneSwitch()!);
  expect(doneSwitch()).toBeChecked();
  await userEvent.click(saveButton());
  expect(source.createTask).toHaveBeenCalledWith(
    expect.objectContaining({ due_date: "2026-09-20", done: true }),
  );
  await act(async () =>
    last("createTask").resolve(makeTaskCard({ id: 5, due_date: "2026-09-20", status: "done" })),
  );
  expect(nav.home).toHaveBeenCalledOnce();
});

test("«Уже выполнено» включили, потом дату вернули в будущее — done не уходит", async () => {
  const { source } = renderForm({ initialDate: "2026-09-20" });
  await userEvent.type(nameField(), "Аренда");
  await userEvent.click(doneSwitch()!);
  await userEvent.clear(dateField());
  await userEvent.type(dateField(), "2026-09-30");
  await userEvent.click(saveButton());
  expect(source.createTask).toHaveBeenCalledWith(
    expect.not.objectContaining({ done: expect.anything() }),
  );
});

test("правка задачи в прошлом: подсказка есть, переключателя нет, PATCH без done", async () => {
  const task = makeTaskCard({ id: 3, title: "Аренда", due_date: "2026-09-10", status: "overdue" });
  const { source } = renderForm({ taskId: 3, cards: { [itemKey("task", 3)]: task } });
  expect(screen.getByText(texts.form.pastNoRemind)).toBeInTheDocument();
  expect(screen.queryAllByRole("radio")).toHaveLength(0);
  expect(doneSwitch()).not.toBeInTheDocument();
  await userEvent.clear(dateField());
  await userEvent.type(dateField(), "2026-09-12");
  await userEvent.click(saveButton());
  expect(source.updateTask).toHaveBeenCalledWith(
    3,
    expect.not.objectContaining({ done: expect.anything() }),
  );
  expect(source.updateTask).toHaveBeenCalledWith(
    3,
    expect.objectContaining({ due_date: "2026-09-12" }),
  );
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
    remind_minute: 0,
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

test("выбранные напоминание и время уходят в запрос: 07:45 → remind_hour=7, remind_minute=45", async () => {
  const { source } = renderForm();
  await userEvent.type(nameField(), "Аренда");
  await userEvent.click(screen.getByRole("radio", { name: texts.form.remind7 }));
  await userEvent.clear(timeField());
  await userEvent.type(timeField(), "07:45");
  await userEvent.click(saveButton());
  expect(source.createTask).toHaveBeenCalledWith(
    expect.objectContaining({ remind_offset_days: 7, remind_hour: 7, remind_minute: 45 }),
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

test("initialDate (выбранный день экрана 15) подставляется, если нет черновика", () => {
  renderForm({ initialDate: "2026-10-05" });
  expect(dateField()).toHaveValue("2026-10-05");
});

test("черновик из чата приоритетнее выбранного дня месяца", () => {
  renderForm({ initialDate: "2026-10-05", draft: { title: "Из чата", due_date: "2026-11-05" } });
  expect(dateField()).toHaveValue("2026-11-05");
});

test("сохранение из «Месяца» (onCreatedFromMonth): назад на 15 с датой задачи, не на 14", async () => {
  const onCreatedFromMonth = vi.fn();
  const { last, nav } = renderForm({ initialDate: "2026-10-05", onCreatedFromMonth });
  await userEvent.type(nameField(), "Сверить кассу");
  await userEvent.click(saveButton());
  const created = makeTaskCard({ id: 12, title: "Сверить кассу", due_date: "2026-10-05" });
  await act(async () => last("createTask").resolve(created));
  expect(onCreatedFromMonth).toHaveBeenCalledWith("2026-10-05");
  expect(nav.home).not.toHaveBeenCalled();
  expect(screen.getByText(texts.form.created)).toBeInTheDocument();
});

test("черновик из чата подставляется в поля; без времени — 10:00 и «за 1 день»", () => {
  renderForm({ draft: { title: "Оплатить аренду", due_date: "2026-11-05" } });
  expect(nameField()).toHaveValue("Оплатить аренду");
  expect(dateField()).toHaveValue("2026-11-05");
  expect(timeField()).toHaveValue("10:00");
  expect(screen.getByRole("radio", { name: texts.form.remind1 })).toBeChecked();
  expect(saveButton()).toBeEnabled();
});

test("черновик без даты («Выбрать дату»): название из чата, дата — завтра", () => {
  renderForm({ draft: { title: "Сдать отчёт", due_date: null } });
  expect(nameField()).toHaveValue("Сдать отчёт");
  expect(dateField()).toHaveValue("2026-09-24");
});

test("черновик из чата со временем (15:30): время подставлено, «в день срока» (контракт TIME-BE)", () => {
  renderForm({
    draft: { title: "Сдать отчёт", due_date: "2026-11-05", remind_hour: 15, remind_minute: 30 },
  });
  expect(timeField()).toHaveValue("15:30");
  expect(screen.getByRole("radio", { name: texts.form.remind0 })).toBeChecked();
});

test("правка: поля из карточки (в том числе время), PATCH, назад на 16 + «Задача изменена»", async () => {
  const task = makeTaskCard({
    id: 3,
    title: "Аренда",
    remind_offset_days: 3,
    remind_hour: 18,
    remind_minute: 45,
  });
  const { source, last, nav, store } = renderForm({
    taskId: 3,
    cards: { [itemKey("task", 3)]: task },
  });
  expect(screen.getByText(texts.form.titleEdit)).toBeInTheDocument();
  expect(nameField()).toHaveValue("Аренда");
  expect(dateField()).toHaveValue("2026-11-05");
  expect(screen.getByRole("radio", { name: texts.form.remind3 })).toBeChecked();
  expect(timeField()).toHaveValue("18:45");

  await userEvent.clear(dateField());
  await userEvent.type(dateField(), "2026-11-10");
  await userEvent.click(saveButton());
  expect(source.updateTask).toHaveBeenCalledWith(3, {
    title: "Аренда",
    due_date: "2026-11-10",
    remind_offset_days: 3,
    remind_hour: 18,
    remind_minute: 45,
  });
  const updated = { ...task, due_date: "2026-11-10" };
  await act(async () => last("updateTask").resolve(updated));
  expect(store.upsert).toHaveBeenCalledWith(updated);
  expect(nav.back).toHaveBeenCalledOnce();
  expect(screen.getByText(texts.form.updated)).toBeInTheDocument();
});

test("правка: ушли назад до ответа — экран размонтирован, ответ пришёл, «Назад» не повторяется (FRONT-22)", async () => {
  const task = makeTaskCard({ id: 3, title: "Аренда", remind_offset_days: 3, remind_hour: 18 });
  const { last, nav, store, unmount } = renderForm({
    taskId: 3,
    cards: { [itemKey("task", 3)]: task },
  });
  await userEvent.click(saveButton());
  const request = last("updateTask");
  unmount();
  const updated = { ...task, due_date: "2026-11-05" };
  await act(async () => request.resolve(updated));
  // Данные в общий кэш попадают в любом случае — их не теряем.
  expect(store.upsert).toHaveBeenCalledWith(updated);
  // Но навигация и тост — уже нет: пользователь сам ушёл дальше «Назад» до ответа.
  expect(nav.back).not.toHaveBeenCalled();
});

test("правка: ушли назад до ответа с ошибкой — экран размонтирован, «Назад» не вызывается, ошибка учтена (FRONT-22)", async () => {
  const task = makeTaskCard({ id: 3, title: "Аренда" });
  const { source, last, nav, unmount } = renderForm({
    taskId: 3,
    cards: { [itemKey("task", 3)]: task },
  });
  await userEvent.click(saveButton());
  const request = last("updateTask");
  unmount();
  await act(async () => request.reject(new ApiError("network")));
  // Аналитика ошибки всё равно пишется.
  expect(source.track).toHaveBeenCalledWith("error", { where: "task_form", kind: "network" });
  expect(nav.back).not.toHaveBeenCalled();
});

test("новая задача из «Месяца»: ушли назад до ответа — onCreatedFromMonth и «Домой» не вызываются (FRONT-22)", async () => {
  const onCreatedFromMonth = vi.fn();
  const { last, nav, unmount } = renderForm({ initialDate: "2026-10-05", onCreatedFromMonth });
  await userEvent.type(nameField(), "Сверить кассу");
  await userEvent.click(saveButton());
  const request = last("createTask");
  unmount();
  const created = makeTaskCard({ id: 12, title: "Сверить кассу", due_date: "2026-10-05" });
  await act(async () => request.resolve(created));
  expect(onCreatedFromMonth).not.toHaveBeenCalled();
  expect(nav.home).not.toHaveBeenCalled();
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
