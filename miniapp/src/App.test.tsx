// Оболочка: вход (экран 18 или 14), 401 ≠ сеть, «Повторить», BackButton, start_param, аналитика;
// сквозные сценарии 14 ↔ 16 ↔ 17: список обновляется без перезагрузки.
import { act, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

import App from "./App";
import { ApiError } from "./data/http";
import { fakeSource, makeItem, makeMe, makeObligationCard, makeTaskCard } from "./test/fakeSource";
import { texts } from "./texts";

beforeEach(() => {
  vi.stubEnv("VITE_BOT_URL", "https://max.ru/test_bot");
});

afterEach(() => {
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
  delete window.WebApp;
  window.history.replaceState(null, "", "/");
  localStorage.clear();
});

const FORBIDDEN = /авторизац|сесси|401|сервер/i;

test("профиля нет — экран 18 с кнопкой в чат", async () => {
  const { source, lastMe } = fakeSource();
  render(<App source={source} />);
  expect(document.querySelector("[aria-busy='true']")).toBeInTheDocument();
  await act(async () => lastMe().resolve(makeMe(false)));

  expect(screen.getByText(texts.gate.title)).toBeInTheDocument();
  expect(screen.getByText(texts.gate.text)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: texts.gate.openChat })).toBeInTheDocument();
  expect(source.calendar).not.toHaveBeenCalled();
  expect(source.track).toHaveBeenCalledWith("miniapp_opened", {
    start_param: null,
    has_profile: false,
  });
});

test("401 на /api/me — та же заглушка, без слов об ошибке авторизации", async () => {
  const { source, lastMe } = fakeSource();
  render(<App source={source} />);
  await act(async () => lastMe().reject(new ApiError("unauthorized", 401)));

  expect(screen.getByText(texts.gate.title)).toBeInTheDocument();
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  expect(document.body.textContent).not.toMatch(FORBIDDEN);
});

test("сеть на /api/me — заглушка + плашка; «Повторить» работает без перезапуска", async () => {
  const { source, lastMe, lastCalendar } = fakeSource();
  render(<App source={source} />);
  await act(async () => lastMe().reject(new ApiError("network")));

  expect(screen.getByText(texts.gate.title)).toBeInTheDocument();
  expect(screen.getByRole("alert")).toHaveTextContent(texts.common.error);
  expect(source.track).toHaveBeenCalledWith("error", { where: "me", kind: "network" });

  await userEvent.click(screen.getByRole("button", { name: texts.common.retry }));
  await act(async () => lastMe().resolve(makeMe(true)));
  await act(async () => lastCalendar().resolve([makeItem({ title: "Аванс" })]));
  expect(screen.getByText("Аванс")).toBeInTheDocument();
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
});

test("без VITE_BOT_URL кнопки «Открыть чат» нет, экран не ломается", async () => {
  vi.stubEnv("VITE_BOT_URL", "");
  const { source, lastMe } = fakeSource();
  render(<App source={source} />);
  await act(async () => lastMe().resolve(makeMe(false)));
  expect(screen.getByText(texts.gate.title)).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: texts.gate.openChat })).not.toBeInTheDocument();
});

test("«Открыть чат» в MAX — openMaxLink со ссылкой на бота", async () => {
  const openMaxLink = vi.fn();
  window.WebApp = { initData: "signed", initDataUnsafe: {}, openMaxLink };
  const { source, lastMe } = fakeSource();
  render(<App source={source} />);
  await act(async () => lastMe().resolve(makeMe(false)));
  await userEvent.click(screen.getByRole("button", { name: texts.gate.openChat }));
  expect(openMaxLink).toHaveBeenCalledWith("https://max.ru/test_bot");
});

test("бэкенд недоступен: плашка, «Повторить» загружает список", async () => {
  const { source, lastMe, lastCalendar } = fakeSource();
  render(<App source={source} />);
  await act(async () => lastMe().resolve(makeMe(true)));
  expect(screen.getByTestId("skeleton")).toBeInTheDocument();
  await act(async () => lastCalendar().reject(new ApiError("network")));

  expect(screen.getByRole("alert")).toHaveTextContent(texts.common.error);
  await userEvent.click(screen.getByRole("button", { name: texts.common.retry }));
  await act(async () => lastCalendar().resolve([makeItem({ title: "Аванс" })]));
  expect(screen.getByText("Аванс")).toBeInTheDocument();
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  expect(source.calendar).toHaveBeenCalledTimes(2);
});

test("5xx — общий common.error с «Повторить»; 401 посреди работы — свой текст", async () => {
  const { source, lastMe, lastCalendar } = fakeSource();
  render(<App source={source} />);
  await act(async () => lastMe().resolve(makeMe(true)));
  await act(async () => lastCalendar().reject(new ApiError("server", 502)));
  expect(screen.getByRole("alert")).toHaveTextContent(texts.common.error);
  expect(document.body.textContent).not.toMatch(/TODO/);
  expect(source.track).toHaveBeenCalledWith("error", { where: "calendar", kind: "server" });

  await userEvent.click(screen.getByRole("button", { name: texts.common.retry }));
  await act(async () => lastCalendar().reject(new ApiError("unauthorized", 401)));
  expect(screen.getByRole("alert")).toHaveTextContent(texts.common.reopen);
  expect(screen.getByRole("alert")).not.toHaveTextContent(texts.common.error);
  // Повтор при 401 не поможет — кнопки нет.
  expect(screen.queryByRole("button", { name: texts.common.retry })).not.toBeInTheDocument();
});

test("календарь запрашивается на 90 дней от «сегодня» пользователя", async () => {
  vi.useFakeTimers({ toFake: ["Date"], now: new Date("2026-09-23T09:00:00Z") });
  const { source, lastMe } = fakeSource();
  render(<App source={source} />);
  await act(async () => lastMe().resolve(makeMe(true)));
  expect(source.calendar).toHaveBeenCalledWith("2026-09-23", "2026-12-22");
  vi.useRealTimers();
});

test("переход на второй экран и возврат через BackButton MAX", async () => {
  const back = { show: vi.fn(), hide: vi.fn(), onClick: vi.fn(), offClick: vi.fn() };
  window.WebApp = { initData: "signed", initDataUnsafe: {}, BackButton: back };
  const { source, lastMe, lastCalendar } = fakeSource();
  render(<App source={source} />);
  await act(async () => lastMe().resolve(makeMe(true)));
  await act(async () => lastCalendar().resolve([makeItem({ title: "Аванс" })]));
  expect(back.show).not.toHaveBeenCalled();

  await userEvent.click(screen.getByText("Аванс"));
  expect(screen.queryByRole("button", { name: texts.list.addTask })).not.toBeInTheDocument();
  expect(back.show).toHaveBeenCalledOnce();
  const onBack = back.onClick.mock.calls[0][0] as () => void;

  act(() => onBack());
  expect(screen.getByText("Аванс")).toBeInTheDocument();
  expect(back.offClick).toHaveBeenCalledWith(onBack);
  expect(back.hide).toHaveBeenCalledOnce();
  // Данные не перезагружались.
  expect(source.calendar).toHaveBeenCalledOnce();
});

test("вне MAX BackButton эмулируется кнопкой «Назад»", async () => {
  const { source, lastMe, lastCalendar } = fakeSource();
  render(<App source={source} />);
  await act(async () => lastMe().resolve(makeMe(true)));
  await act(async () => lastCalendar().resolve([]));

  expect(screen.queryByRole("button", { name: texts.nav.back })).not.toBeInTheDocument();
  await userEvent.click(screen.getAllByRole("button", { name: texts.list.addTask })[0]);
  await userEvent.click(screen.getByRole("button", { name: texts.nav.back }));
  expect(screen.getByText(texts.list.emptyTitle)).toBeInTheDocument();
});

test("«Список | Месяц» не перезагружает данные и запоминается", async () => {
  const { source, lastMe, lastCalendar } = fakeSource();
  render(<App source={source} />);
  await act(async () => lastMe().resolve(makeMe(true)));
  await act(async () => lastCalendar().resolve([makeItem({ title: "Аванс" })]));

  await userEvent.click(screen.getByRole("button", { name: texts.list.tabMonth }));
  expect(screen.queryByText("Аванс")).not.toBeInTheDocument();
  expect(localStorage.getItem("calendar.tab")).toBe("month");
  expect(source.track).toHaveBeenCalledWith("month_opened");
  // Месяц грузит свой период один раз — список 14 заново не грузится.
  expect(source.calendar).toHaveBeenCalledTimes(2);
  const [from, to] = source.calendar.mock.calls[1];
  expect(from.slice(8)).toBe("01");
  expect(from.slice(0, 7)).toBe(to.slice(0, 7));
  await act(async () => lastCalendar().resolve([]));

  await userEvent.click(screen.getByRole("button", { name: texts.list.tabList }));
  expect(screen.getByText("Аванс")).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: texts.list.tabMonth }));
  await userEvent.click(screen.getByRole("button", { name: texts.list.tabList }));
  expect(source.calendar).toHaveBeenCalledTimes(2);
  expect(source.track.mock.calls.filter(([name]) => name === "month_opened")).toHaveLength(2);
});

test("?start_param=item_obligation_1 открывает карточку 16, «Назад» — на список 14", async () => {
  window.history.replaceState(null, "", "/?start_param=item_obligation_1");
  const { source, lastMe, lastCalendar, last } = fakeSource();
  render(<App source={source} />);
  expect(source.me).toHaveBeenCalledWith("item_obligation_1");
  await act(async () => lastMe().resolve(makeMe(true)));
  expect(source.item).toHaveBeenCalledWith("obligation", 1);
  await act(async () => last("item").resolve(makeObligationCard({ title: "Аванс" })));
  await act(async () => lastCalendar().resolve([makeItem({ title: "Аванс" })]));

  expect(screen.getByRole("button", { name: texts.card.markDone })).toBeInTheDocument();
  expect(source.track).toHaveBeenCalledWith("miniapp_opened", {
    start_param: "item_obligation_1",
    has_profile: true,
  });
  expect(source.track).toHaveBeenCalledWith("item_card_opened", {
    item_id: 1,
    item_type: "obligation",
    source: "bot",
  });

  await userEvent.click(screen.getByRole("button", { name: texts.nav.back }));
  expect(screen.getByRole("button", { name: texts.list.addTask })).toBeInTheDocument();
  expect(screen.getByText("Аванс")).toBeInTheDocument();
});

test("?start_param=task_draft открывает форму 17 с черновиком из /api/me", async () => {
  window.history.replaceState(null, "", "/?start_param=task_draft");
  const { source, lastMe } = fakeSource();
  render(<App source={source} />);
  await act(async () =>
    lastMe().resolve({
      ...makeMe(true),
      draft: { title: "Оплатить аренду", due_date: "2099-11-05" },
    }),
  );
  expect(screen.getByText(texts.form.titleNew)).toBeInTheDocument();
  expect(screen.getByLabelText(texts.form.name)).toHaveValue("Оплатить аренду");
  expect(screen.getByLabelText(texts.form.date)).toHaveValue("2099-11-05");
});

test("?start_param=task_draft_<id> со своим черновиком — форма 17 заполнена, без тоста", async () => {
  window.history.replaceState(null, "", "/?start_param=task_draft_ab12cd34");
  const { source, lastMe } = fakeSource();
  render(<App source={source} />);
  expect(source.me).toHaveBeenCalledWith("task_draft_ab12cd34");
  await act(async () =>
    lastMe().resolve({
      ...makeMe(true),
      draft: { title: "Оплатить аренду", due_date: "2099-11-05" },
      draft_stale: false,
    }),
  );
  expect(screen.getByLabelText(texts.form.name)).toHaveValue("Оплатить аренду");
  expect(screen.queryByText(texts.form.draftStale)).not.toBeInTheDocument();
  expect(source.track).not.toHaveBeenCalledWith("task_draft_stale", expect.anything());
});

test("черновик из старого сообщения: форма 17 пустая, тост и task_draft_stale (#96)", async () => {
  window.history.replaceState(null, "", "/?start_param=task_draft_00ff00ff");
  const { source, lastMe } = fakeSource();
  render(<App source={source} />);
  await act(async () => lastMe().resolve({ ...makeMe(true), draft: null, draft_stale: true }));
  expect(screen.getByText(texts.form.titleNew)).toBeInTheDocument();
  expect(screen.getByLabelText(texts.form.name)).toHaveValue("");
  expect(screen.getByText(texts.form.draftStale)).toBeInTheDocument();
  expect(source.track).toHaveBeenCalledWith("task_draft_stale", { action: "edit" });
  expect(source.track.mock.calls.filter(([name]) => name === "task_draft_stale")).toHaveLength(1);

  // «Назад» — на список 14, как у любого входа из бота
  await userEvent.click(screen.getByRole("button", { name: texts.nav.back }));
  expect(screen.getByRole("button", { name: texts.list.addTask })).toBeInTheDocument();
});

test("отметка в карточке сразу видна в списке — без перезагрузки календаря", async () => {
  const { source, lastMe, lastCalendar, last } = fakeSource();
  render(<App source={source} />);
  await act(async () => lastMe().resolve(makeMe(true)));
  await act(async () => lastCalendar().resolve([makeItem({ title: "Аванс" })]));

  await userEvent.click(screen.getByText("Аванс"));
  await act(async () => last("item").resolve(makeObligationCard({ title: "Аванс" })));
  await userEvent.click(screen.getByRole("button", { name: texts.card.markDone }));
  await act(async () =>
    last("markDone").resolve(
      makeObligationCard({ title: "Аванс", status: "done", done_at: "2026-09-23T07:00:00Z" }),
    ),
  );
  expect(screen.getByRole("button", { name: texts.card.undoDone })).toBeInTheDocument();

  await userEvent.click(screen.getByRole("button", { name: texts.nav.back }));
  const row = screen.getByText("Аванс").closest(".row") as HTMLElement;
  expect(within(row).getByLabelText(texts.status.done)).toBeInTheDocument();
  expect(source.calendar).toHaveBeenCalledOnce();
});

test("новая задача из формы появляется в списке, внизу — «Задача добавлена»", async () => {
  vi.useFakeTimers({ toFake: ["Date"], now: new Date("2026-09-23T09:00:00Z") });
  const { source, lastMe, lastCalendar, last } = fakeSource();
  render(<App source={source} />);
  await act(async () => lastMe().resolve(makeMe(true)));
  await act(async () => lastCalendar().resolve([]));

  await userEvent.click(screen.getAllByRole("button", { name: texts.list.addTask })[0]);
  await userEvent.type(screen.getByLabelText(texts.form.name), "Сверить выписку");
  await userEvent.click(screen.getByRole("button", { name: texts.form.save }));
  expect(source.createTask).toHaveBeenCalledWith({
    title: "Сверить выписку",
    due_date: "2026-09-24",
    remind_offset_days: 1,
    remind_hour: 10,
    remind_minute: 0,
  });
  await act(async () =>
    last("createTask").resolve(
      makeTaskCard({ id: 9, title: "Сверить выписку", due_date: "2026-09-24" }),
    ),
  );
  expect(screen.getByText("Сверить выписку")).toBeInTheDocument();
  expect(screen.getByText(texts.form.created)).toBeInTheDocument();
  expect(source.calendar).toHaveBeenCalledOnce();
  vi.useRealTimers();
});

test("«Дальше» развёрнута — остаётся развёрнутой после карточки 16 и «Назад»; list_expanded", async () => {
  vi.useFakeTimers({ toFake: ["Date"], now: new Date("2026-09-23T09:00:00Z") });
  const { source, lastMe, lastCalendar } = fakeSource();
  render(<App source={source} />);
  await act(async () => lastMe().resolve(makeMe(true)));
  await act(async () =>
    lastCalendar().resolve(
      [1, 2, 3, 4, 5].map((n) =>
        makeItem({ id: n, title: `Срок ${n}`, due_date: `2026-10-0${n}` }),
      ),
    ),
  );
  expect(screen.queryByText("Срок 5")).not.toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: texts.list.showMore(2) }));
  expect(source.track).toHaveBeenCalledWith("list_expanded", { hidden: 2 });

  await userEvent.click(screen.getByText("Срок 5"));
  await userEvent.click(screen.getByRole("button", { name: texts.nav.back }));
  expect(screen.getByText("Срок 5")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: texts.list.showLess })).toBeInTheDocument();
  vi.useRealTimers();
});

test("создание из «Списка» по-прежнему ведёт на экран 14, не на «Месяц» (#93)", async () => {
  vi.useFakeTimers({ toFake: ["Date"], now: new Date("2026-09-23T09:00:00Z") });
  const { source, lastMe, lastCalendar, last } = fakeSource();
  render(<App source={source} />);
  await act(async () => lastMe().resolve(makeMe(true)));
  await act(async () => lastCalendar().resolve([]));

  await userEvent.click(screen.getAllByRole("button", { name: texts.list.addTask })[0]);
  await userEvent.type(screen.getByLabelText(texts.form.name), "Отправить письмо");
  await userEvent.click(screen.getByRole("button", { name: texts.form.save }));
  await act(async () =>
    last("createTask").resolve(
      makeTaskCard({ id: 30, title: "Отправить письмо", due_date: "2026-09-24" }),
    ),
  );

  // Экран 14: активна вкладка «Список», сетки месяца на экране нет.
  expect(screen.getByRole("button", { name: texts.list.tabList })).toHaveAttribute(
    "aria-pressed",
    "true",
  );
  expect(document.querySelector(".month-grid")).not.toBeInTheDocument();
  expect(screen.getByText("Отправить письмо")).toBeInTheDocument();
  vi.useRealTimers();
});

test("задача из «Месяца» возвращает на «Месяц» с её днём — точка и строка видны без повторной загрузки (#93)", async () => {
  vi.useFakeTimers({ toFake: ["Date"], now: new Date("2026-09-23T09:00:00Z") });
  const { source, lastMe, lastCalendar, last } = fakeSource();
  render(<App source={source} />);
  await act(async () => lastMe().resolve(makeMe(true)));
  await act(async () => lastCalendar().resolve([]));

  await userEvent.click(screen.getByRole("button", { name: texts.list.tabMonth }));
  await act(async () => lastCalendar().resolve([])); // сентябрь пуст — «+ Задача» в пустом блоке

  await userEvent.click(screen.getByRole("button", { name: texts.month.addTask }));
  expect(screen.getByLabelText(texts.form.date)).toHaveValue("2026-09-23");
  await userEvent.type(screen.getByLabelText(texts.form.name), "Сверить кассу");
  await userEvent.click(screen.getByRole("button", { name: texts.form.save }));
  await act(async () =>
    last("createTask").resolve(
      makeTaskCard({
        id: 21,
        title: "Сверить кассу",
        due_date: "2026-09-23",
        category: "custom",
      }),
    ),
  );

  // Вернулись на «Месяц» (не на 14), месяц не перезапрашивался — задача уже в кеше через upsert.
  expect(screen.getByRole("button", { name: texts.list.tabMonth })).toHaveAttribute(
    "aria-pressed",
    "true",
  );
  expect(source.calendar).toHaveBeenCalledTimes(2);
  expect(screen.getByText("Сверить кассу")).toBeInTheDocument();
  const selectedDay = document.querySelector(".month-day--selected");
  expect(selectedDay).not.toBeNull();
  expect(selectedDay?.querySelector(".dot--custom")).toBeInTheDocument();
  vi.useRealTimers();
});

test("удалённая задача пропадает из списка, внизу — «Задача удалена»", async () => {
  const { source, lastMe, lastCalendar, last } = fakeSource();
  render(<App source={source} />);
  await act(async () => lastMe().resolve(makeMe(true)));
  await act(async () =>
    lastCalendar().resolve([
      makeItem({ type: "task", id: 3, title: "Аренда", category: "custom" }),
      makeItem({ title: "Аванс" }),
    ]),
  );
  await userEvent.click(screen.getByText("Аренда"));
  await act(async () => last("item").resolve(makeTaskCard({ id: 3, title: "Аренда" })));
  await userEvent.click(screen.getByRole("button", { name: texts.card.delete }));
  await userEvent.click(screen.getByRole("button", { name: texts.card.deleteConfirm }));
  expect(source.deleteTask).toHaveBeenCalledWith(3);
  await act(async () => last("deleteTask").resolve(undefined));

  expect(screen.queryByText("Аренда")).not.toBeInTheDocument();
  expect(screen.getByText("Аванс")).toBeInTheDocument();
  expect(screen.getByText(texts.card.deleted)).toBeInTheDocument();
});

test("в MAX start_param берётся из initDataUnsafe", async () => {
  window.history.replaceState(null, "", "/?start_param=ignored");
  window.WebApp = { initData: "signed", initDataUnsafe: { start_param: "task_draft" } };
  const { source } = fakeSource();
  render(<App source={source} />);
  expect(source.me).toHaveBeenCalledWith("task_draft");
});

test("пометка «ТЕСТОВЫЕ ДАННЫЕ» видна только на моках", async () => {
  const { source } = fakeSource();
  const { unmount } = render(<App source={source} />);
  expect(screen.queryByText(texts.mockBadge)).not.toBeInTheDocument();
  unmount();
  render(<App source={{ ...fakeSource().source, isMock: true }} />);
  expect(screen.getByText(texts.mockBadge)).toBeInTheDocument();
});

// --- Экран 19: переход с шапки 14 и 15, пересборка ------------------------------------------

/** Кнопка «Профиль» в шапке 14/15 (решение человека 29.09 — вместо строки «ИП · …»). */
const HEADER = texts.list.profileButton;

test("кнопка «Профиль» в шапке экрана 14 ведёт на экран 19, «Назад» — на список без перезагрузки", async () => {
  const { source, lastMe, lastCalendar } = fakeSource();
  render(<App source={source} />);
  await act(async () => lastMe().resolve(makeMe(true)));
  await act(async () => lastCalendar().resolve([makeItem({ title: "Аванс" })]));

  await userEvent.click(screen.getByRole("button", { name: HEADER }));
  expect(screen.getByText(texts.profile.disclaimer)).toBeInTheDocument();
  expect(source.me).toHaveBeenCalledTimes(2);
  await act(async () => lastMe().resolve(makeMe(true)));
  expect(screen.getByRole("button", { name: texts.profile.rebuild })).toBeInTheDocument();

  await userEvent.click(screen.getByRole("button", { name: texts.nav.back }));
  expect(screen.getByText("Аванс")).toBeInTheDocument();
  expect(source.calendar).toHaveBeenCalledOnce();
});

test("кнопка «Профиль» в шапке экрана 15 тоже ведёт на экран 19", async () => {
  localStorage.setItem("calendar.tab", "month");
  const { source, lastMe } = fakeSource();
  render(<App source={source} />);
  await act(async () => lastMe().resolve(makeMe(true)));
  await userEvent.click(screen.getByRole("button", { name: HEADER }));
  expect(screen.getByText(texts.profile.disclaimer)).toBeInTheDocument();
});

test("после «Пересобрать» список грузится заново", async () => {
  const { source, lastMe, lastCalendar, last } = fakeSource();
  render(<App source={source} />);
  await act(async () => lastMe().resolve(makeMe(true)));
  await act(async () => lastCalendar().resolve([makeItem({ title: "Аванс" })]));

  await userEvent.click(screen.getByRole("button", { name: HEADER }));
  await act(async () => lastMe().resolve(makeMe(true)));
  await userEvent.click(screen.getByRole("button", { name: texts.profile.rebuild }));
  const profile = { ...makeMe(true).profile!, regime: "usn15" as const };
  await act(async () =>
    last("rebuild").resolve({ items_count: 1, nearest_due_date: null, profile }),
  );
  expect(source.calendar).toHaveBeenCalledTimes(2);

  await userEvent.click(screen.getByRole("button", { name: texts.nav.back }));
  await act(async () => lastCalendar().resolve([makeItem({ title: "Декларация" })]));
  expect(screen.getByText("Декларация")).toBeInTheDocument();
  expect(screen.queryByText("Аванс")).not.toBeInTheDocument();
});

// --- Экран 13: из бота (start_param=settings) и со строки экрана 19 --------------------------

test("?start_param=settings открывает экран «Настройки» поверх списка; «Назад» — на список 14", async () => {
  localStorage.setItem("calendar.tab", "month");
  window.history.replaceState(null, "", "/?start_param=settings");
  const { source, lastMe, lastCalendar } = fakeSource();
  render(<App source={source} />);
  await act(async () => lastMe().resolve(makeMe(true)));
  expect(screen.getByText(texts.settings.title)).toBeInTheDocument();
  // Обе секции экрана «Настройки» (29.09): напоминания, тема (календарь телефона — отдельный экран).
  expect(screen.getByText(texts.settings.sectionReminders)).toBeInTheDocument();
  expect(screen.getByRole("group", { name: texts.settings.theme })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: texts.settings.save })).toBeDisabled();
  expect(source.track).toHaveBeenCalledWith("settings_opened", { source: "bot" });

  await userEvent.click(screen.getByRole("button", { name: texts.nav.back }));
  await act(async () => lastCalendar().resolve([makeItem({ title: "Аванс" })]));
  expect(screen.getByRole("button", { name: texts.list.addTask })).toBeInTheDocument();
  expect(screen.getByText("Аванс")).toBeInTheDocument();
});

test("профиль → «Настройки» → «Назад» ведёт обратно в профиль (29.09)", async () => {
  const { source, lastMe, lastCalendar } = fakeSource();
  render(<App source={source} />);
  await act(async () => lastMe().resolve(makeMe(true)));
  await act(async () => lastCalendar().resolve([makeItem({ title: "Аванс" })]));
  await userEvent.click(screen.getByRole("button", { name: HEADER }));
  await act(async () => lastMe().resolve(makeMe(true)));
  await userEvent.click(screen.getByRole("button", { name: texts.profile.settings }));
  expect(screen.getByText(texts.settings.title)).toBeInTheDocument();
  expect(source.track).toHaveBeenCalledWith("settings_opened", { source: "profile" });

  await userEvent.click(screen.getByRole("button", { name: texts.nav.back }));
  expect(screen.getByText(texts.profile.disclaimer)).toBeInTheDocument();
  expect(screen.queryByText(texts.settings.days)).not.toBeInTheDocument();
  // Экран 19 при открытии, как всегда, запрашивает профиль заново.
  await act(async () => lastMe().resolve(makeMe(true)));
  expect(screen.getByRole("button", { name: texts.profile.rebuild })).toBeInTheDocument();
});

// --- Экран «Календарь телефона»: со строки экрана 19 (задача UI-EXPORT, 29.09) -----------------

test("профиль → «Календарь телефона» → «Назад» ведёт обратно в профиль", async () => {
  const { source, lastMe, lastCalendar } = fakeSource();
  render(<App source={source} />);
  await act(async () => lastMe().resolve(makeMe(true)));
  await act(async () => lastCalendar().resolve([makeItem({ title: "Аванс" })]));
  await userEvent.click(screen.getByRole("button", { name: HEADER }));
  await act(async () => lastMe().resolve(makeMe(true)));
  await userEvent.click(screen.getByRole("button", { name: texts.profile.phoneCalendar }));
  expect(screen.getByText(texts.ics.title)).toBeInTheDocument();
  expect(source.track).toHaveBeenCalledWith("ics_screen_opened");

  await userEvent.click(screen.getByRole("button", { name: texts.nav.back }));
  expect(screen.getByText(texts.profile.disclaimer)).toBeInTheDocument();
  expect(screen.queryByText(texts.ics.sectionOnce)).not.toBeInTheDocument();
});

test("из бота: сохранение возвращает на список 14 с тостом «Сохранено»", async () => {
  window.history.replaceState(null, "", "/?start_param=settings");
  const { source, lastMe, last } = fakeSource();
  render(<App source={source} />);
  await act(async () => lastMe().resolve(makeMe(true)));
  await userEvent.click(screen.getByRole("button", { name: texts.settings.hourOption(9) }));
  await userEvent.click(screen.getByRole("button", { name: texts.settings.save }));
  const profile = makeMe(true).profile!;
  await act(async () =>
    last("saveSettings").resolve({ ...profile, reminders: { ...profile.reminders!, hour: 9 } }),
  );
  expect(screen.queryByText(texts.settings.title)).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: texts.list.addTask })).toBeInTheDocument();
  expect(screen.getByText(texts.settings.saved)).toBeInTheDocument();
});

test("экран 19 → «Настройки» → смена пояса → назад на 19 с новым поясом, календарь заново", async () => {
  const { source, lastMe, lastCalendar, last } = fakeSource();
  render(<App source={source} />);
  await act(async () => lastMe().resolve(makeMe(true)));
  await act(async () => lastCalendar().resolve([makeItem({ title: "Аванс" })]));

  await userEvent.click(screen.getByRole("button", { name: HEADER }));
  await act(async () => lastMe().resolve(makeMe(true)));
  await userEvent.click(screen.getByRole("button", { name: texts.profile.settings }));
  expect(screen.getByText(texts.settings.title)).toBeInTheDocument();
  expect(source.track).toHaveBeenCalledWith("settings_opened", { source: "profile" });

  await userEvent.click(screen.getByRole("button", { name: texts.settings.timezoneShowAll }));
  await userEvent.click(screen.getByRole("radio", { name: "Владивосток (UTC+10)" }));
  await userEvent.click(screen.getByRole("button", { name: texts.settings.save }));
  const saved = { ...makeMe(true).profile!, timezone: "Asia/Vladivostok" };
  await act(async () => last("saveSettings").resolve(saved));

  // Снова экран 19 с тостом; он, как всегда при открытии, запрашивает профиль заново.
  expect(screen.getByText(texts.profile.disclaimer)).toBeInTheDocument();
  expect(screen.getByText(texts.settings.saved)).toBeInTheDocument();
  expect(source.me).toHaveBeenCalledTimes(3);
  await act(async () => lastMe().resolve({ ...makeMe(true), profile: saved }));
  expect(screen.getByText("Владивосток (UTC+10)")).toBeInTheDocument();
  // Пояс сменился — список 14 грузится заново («сегодня» в новом поясе).
  expect(source.calendar).toHaveBeenCalledTimes(2);
});

test("смена пояса сбрасывает кеш месяцев: сетка 15 грузит месяц заново", async () => {
  vi.useFakeTimers({ toFake: ["Date"], now: new Date("2026-09-23T09:00:00Z") });
  localStorage.setItem("calendar.tab", "month");
  const { source, lastMe, last } = fakeSource();
  render(<App source={source} />);
  await act(async () => lastMe().resolve(makeMe(true)));
  const monthCalls = () =>
    source.calendar.mock.calls.filter(([from, to]) => from === "2026-09-01" && to === "2026-09-30")
      .length;
  expect(monthCalls()).toBe(1);

  await userEvent.click(screen.getByRole("button", { name: HEADER }));
  await act(async () => lastMe().resolve(makeMe(true)));
  await userEvent.click(screen.getByRole("button", { name: texts.profile.settings }));
  await userEvent.click(screen.getByRole("button", { name: texts.settings.timezoneShowAll }));
  await userEvent.click(screen.getByRole("radio", { name: "Омск (UTC+6)" }));
  await userEvent.click(screen.getByRole("button", { name: texts.settings.save }));
  await act(async () =>
    last("saveSettings").resolve({ ...makeMe(true).profile!, timezone: "Asia/Omsk" }),
  );
  await userEvent.click(screen.getByRole("button", { name: texts.nav.back }));
  expect(monthCalls()).toBe(2);
  vi.useRealTimers();
});

test("без смены пояса кеш месяцев остаётся", async () => {
  vi.useFakeTimers({ toFake: ["Date"], now: new Date("2026-09-23T09:00:00Z") });
  localStorage.setItem("calendar.tab", "month");
  const { source, lastMe, last } = fakeSource();
  render(<App source={source} />);
  await act(async () => lastMe().resolve(makeMe(true)));
  await userEvent.click(screen.getByRole("button", { name: HEADER }));
  await act(async () => lastMe().resolve(makeMe(true)));
  await userEvent.click(screen.getByRole("button", { name: texts.profile.settings }));
  await userEvent.click(screen.getByRole("button", { name: texts.settings.hourOption(18) }));
  await userEvent.click(screen.getByRole("button", { name: texts.settings.save }));
  const profile = makeMe(true).profile!;
  await act(async () =>
    last("saveSettings").resolve({ ...profile, reminders: { ...profile.reminders!, hour: 18 } }),
  );
  await userEvent.click(screen.getByRole("button", { name: texts.nav.back }));
  // Список 14 и месяц — по одному запросу с запуска, новых нет.
  expect(source.calendar).toHaveBeenCalledTimes(2);
  vi.useRealTimers();
});

test("«Назад» с экрана 13 до ответа сохранения: остаёмся на 19, профиль всё равно обновлён", async () => {
  const { source, lastMe, lastCalendar, last } = fakeSource();
  render(<App source={source} />);
  await act(async () => lastMe().resolve(makeMe(true)));
  await act(async () => lastCalendar().resolve([makeItem({ title: "Аванс" })]));
  await userEvent.click(screen.getByRole("button", { name: HEADER }));
  await act(async () => lastMe().resolve(makeMe(true)));
  await userEvent.click(screen.getByRole("button", { name: texts.profile.settings }));
  await userEvent.click(screen.getByRole("button", { name: texts.settings.timezoneShowAll }));
  await userEvent.click(screen.getByRole("radio", { name: "Омск (UTC+6)" }));
  await userEvent.click(screen.getByRole("button", { name: texts.settings.save }));

  // Человек нажал «Назад», не дождавшись ответа, — он на 19.
  await userEvent.click(screen.getByRole("button", { name: texts.nav.back }));
  expect(screen.getByText(texts.profile.disclaimer)).toBeInTheDocument();
  await act(async () =>
    last("saveSettings").resolve({ ...makeMe(true).profile!, timezone: "Asia/Omsk" }),
  );
  // Ответ пришёл: второго «Назад» нет — по-прежнему экран 19, не список; тоста нет.
  expect(screen.getByText(texts.profile.disclaimer)).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: texts.list.addTask })).not.toBeInTheDocument();
  expect(screen.queryByText(texts.settings.saved)).not.toBeInTheDocument();
  // Профиль ушёл в оболочку: пояс сменился — список 14 грузится заново.
  expect(source.calendar).toHaveBeenCalledTimes(2);
});

test("смена пояса после ошибки календаря: список 14 снова в состоянии загрузки", async () => {
  window.history.replaceState(null, "", "/?start_param=settings");
  const { source, lastMe, lastCalendar, last } = fakeSource();
  render(<App source={source} />);
  await act(async () => lastMe().resolve(makeMe(true)));
  await act(async () => lastCalendar().reject(new ApiError("network")));
  await userEvent.click(screen.getByRole("button", { name: texts.settings.timezoneShowAll }));
  await userEvent.click(screen.getByRole("radio", { name: "Омск (UTC+6)" }));
  await userEvent.click(screen.getByRole("button", { name: texts.settings.save }));
  await act(async () =>
    last("saveSettings").resolve({ ...makeMe(true).profile!, timezone: "Asia/Omsk" }),
  );
  // Список ещё не загружен, запрос в новом поясе идёт — скелетон, как после «Пересобрать».
  expect(screen.getByTestId("skeleton")).toBeInTheDocument();
  expect(source.calendar).toHaveBeenCalledTimes(2);
});

test("экран 19: если профиль не обновился, после сохранения виден новый пояс из оболочки", async () => {
  const { source, lastMe, lastCalendar, last } = fakeSource();
  render(<App source={source} />);
  await act(async () => lastMe().resolve(makeMe(true)));
  await act(async () => lastCalendar().resolve([]));
  await userEvent.click(screen.getByRole("button", { name: HEADER }));
  await act(async () => lastMe().resolve(makeMe(true)));
  await userEvent.click(screen.getByRole("button", { name: texts.profile.settings }));
  await userEvent.click(screen.getByRole("button", { name: texts.settings.timezoneShowAll }));
  await userEvent.click(screen.getByRole("radio", { name: "Омск (UTC+6)" }));
  await userEvent.click(screen.getByRole("button", { name: texts.settings.save }));
  await act(async () =>
    last("saveSettings").resolve({ ...makeMe(true).profile!, timezone: "Asia/Omsk" }),
  );
  // Повторный /api/me не удался — экран 19 показывает профиль оболочки, уже с новым поясом.
  await act(async () => lastMe().reject(new ApiError("network")));
  expect(screen.getByText("Омск (UTC+6)")).toBeInTheDocument();
});

// --- «Поделиться сроком» (SHARE): вход по ссылке ?startapp=share_<code> ------------------------

const SHARE_INVITE = {
  code: "abcdEFGH1234",
  item_type: "task" as const,
  title: "Сдать отчёт",
  due_date: "2026-10-28",
};

test("share_<code> с профилем: приглашение, «Добавить» — карточка новой задачи поверх списка", async () => {
  window.history.replaceState(null, "", "/?start_param=share_abcdEFGH1234");
  const { source, lastMe, lastCalendar, last } = fakeSource();
  render(<App source={source} />);
  // Приглашение грузится, не дожидаясь /api/me.
  expect(source.getShare).toHaveBeenCalledWith("abcdEFGH1234");
  await act(async () => lastMe().resolve(makeMe(true)));
  expect(source.calendar).not.toHaveBeenCalled();
  await act(async () => last("getShare").resolve(SHARE_INVITE));

  await userEvent.click(screen.getByRole("button", { name: texts.share.add }));
  const task = makeTaskCard({ id: 42, title: "Сдать отчёт", due_date: "2026-10-28" });
  await act(async () => last("acceptShare").resolve({ created: true, task }));

  expect(screen.getByText(texts.share.added)).toBeInTheDocument();
  expect(source.item).toHaveBeenCalledWith("task", 42);
  await act(async () => last("item").resolve(task));
  await act(async () => lastCalendar().resolve([]));
  expect(screen.getByRole("button", { name: texts.card.markDone })).toBeInTheDocument();
  expect(source.track).toHaveBeenCalledWith("item_card_opened", {
    item_id: 42,
    item_type: "task",
    source: "share",
  });
  await userEvent.click(screen.getByRole("button", { name: texts.nav.back }));
  expect(screen.getByRole("button", { name: texts.list.addTask })).toBeInTheDocument();
});

test("share_<code> без профиля: задача добавляется, потом заглушка 18 с пояснением", async () => {
  window.history.replaceState(null, "", "/?start_param=share_abcdEFGH1234");
  const { source, lastMe, last } = fakeSource();
  render(<App source={source} />);
  await act(async () => lastMe().resolve(makeMe(false)));
  await act(async () => last("getShare").resolve(SHARE_INVITE));
  expect(screen.queryByText(texts.gate.title)).not.toBeInTheDocument();

  await userEvent.click(screen.getByRole("button", { name: texts.share.add }));
  await act(async () =>
    last("acceptShare").resolve({ created: true, task: makeTaskCard({ id: 42 }) }),
  );
  expect(screen.getByText(texts.share.gateAdded)).toBeInTheDocument();
  expect(screen.getByText(texts.gate.title)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: texts.gate.openChat })).toBeInTheDocument();
  expect(source.calendar).not.toHaveBeenCalled();
});

test("share_<code>: «Не нужно» — обычный вход, список 14 без карточки", async () => {
  window.history.replaceState(null, "", "/?start_param=share_abcdEFGH1234");
  const { source, lastMe, lastCalendar, last } = fakeSource();
  render(<App source={source} />);
  await act(async () => lastMe().resolve(makeMe(true)));
  await act(async () => last("getShare").resolve(SHARE_INVITE));
  await userEvent.click(screen.getByRole("button", { name: texts.share.decline }));

  await act(async () => lastCalendar().resolve([]));
  expect(screen.getByRole("button", { name: texts.list.addTask })).toBeInTheDocument();
  expect(source.item).not.toHaveBeenCalled();
  expect(source.acceptShare).not.toHaveBeenCalled();
});
