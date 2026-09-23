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
  await userEvent.click(screen.getByRole("button", { name: texts.list.tabList }));
  expect(screen.getByText("Аванс")).toBeInTheDocument();
  expect(source.calendar).toHaveBeenCalledOnce();
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
