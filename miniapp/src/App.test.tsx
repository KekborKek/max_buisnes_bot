// Оболочка: вход (экран 18 или 14), 401 ≠ сеть, «Повторить», BackButton, start_param, аналитика.
import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

import App from "./App";
import { ApiError } from "./data/http";
import { fakeSource, makeItem, makeMe } from "./test/fakeSource";
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
  expect(screen.queryByText("Аванс")).not.toBeInTheDocument();
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

test("?start_param=item_obligation_1 вне MAX разбирается и ведёт на список", async () => {
  window.history.replaceState(null, "", "/?start_param=item_obligation_1");
  const { source, lastMe, lastCalendar } = fakeSource();
  render(<App source={source} />);
  expect(source.me).toHaveBeenCalledWith("item_obligation_1");
  await act(async () => lastMe().resolve(makeMe(true)));
  await act(async () => lastCalendar().resolve([makeItem({ title: "Аванс" })]));
  expect(screen.getByText("Аванс")).toBeInTheDocument();
  expect(source.track).toHaveBeenCalledWith("miniapp_opened", {
    start_param: "item_obligation_1",
    has_profile: true,
  });
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
