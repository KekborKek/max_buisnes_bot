// Экран «Календарь телефона» (вынесен с экрана «Настройки» на отдельный экран, задача UI-EXPORT,
// решение 29.09): ics_screen_opened при открытии; лента .ics — «Добавить в календарь телефона» и
// «Скопировать ссылку для подписки», перенесено без изменения логики из бывшего CalendarExport
// (было в SettingsScreen.test.tsx до 29.09).
import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, test, vi } from "vitest";

import { ApiError } from "../data/http";
import { ToastProvider } from "../shell/Toast";
import { fakeSource } from "../test/fakeSource";
import { texts } from "../texts";
import { IcsScreen, type IcsRoute } from "./IcsScreen";

afterEach(() => {
  vi.unstubAllGlobals();
  delete window.WebApp;
});

function renderIcs() {
  const fake = fakeSource();
  const route: IcsRoute = { name: "ics" };
  render(
    <ToastProvider>
      <IcsScreen source={fake.source} route={route} />
    </ToastProvider>,
  );
  return fake;
}

const ICS = {
  url: "https://vse-uspel.ru/api/ics/1-abc.ics",
  webcal_url: "webcal://vse-uspel.ru/api/ics/1-abc.ics",
  items: 2,
};

function inMax() {
  const openLink = vi.fn();
  window.WebApp = { initData: "signed", initDataUnsafe: {}, openLink };
  return openLink;
}

test("открытие экрана: заголовок, обе секции, событие ics_screen_opened один раз", () => {
  const { source } = renderIcs();
  expect(screen.getByText(texts.ics.title)).toBeInTheDocument();
  expect(screen.getByText(texts.ics.sectionOnce)).toBeInTheDocument();
  expect(screen.getByText(texts.ics.sectionSubscribe)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: texts.ics.onceButton })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: texts.ics.subscribeButton })).toBeInTheDocument();
  expect(screen.getByText(texts.ics.onceHint)).toBeInTheDocument();
  expect(screen.getByText(texts.ics.stepsIphone)).toBeInTheDocument();
  expect(screen.getByText(texts.ics.stepsAndroid)).toBeInTheDocument();
  expect(source.track).toHaveBeenCalledWith("ics_screen_opened");
  expect(source.track.mock.calls.filter(([name]) => name === "ics_screen_opened")).toHaveLength(1);
});

test("календарь телефона: ссылка запрашивается заранее, нажатие открывает её через openLink", async () => {
  const openLink = inMax();
  const { source, last } = renderIcs();
  // Ссылка запрошена при открытии экрана: Bridge открывает ссылку только по клику.
  expect(source.icsLink).toHaveBeenCalledOnce();
  expect(source.track).not.toHaveBeenCalledWith("ics_link_requested", expect.anything());
  await act(async () => last("icsLink").resolve(ICS));

  await userEvent.click(screen.getByRole("button", { name: texts.ics.onceButton }));
  expect(openLink).toHaveBeenCalledWith(ICS.url);
  expect(source.track).toHaveBeenCalledWith("ics_link_requested", { action: "open" });
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
});

test("календарь телефона: нажали до ответа — кнопка занята, ссылка откроется по ответу", async () => {
  const openLink = inMax();
  const { source, last } = renderIcs();
  const button = screen.getByRole("button", { name: texts.ics.onceButton });
  await userEvent.click(button);
  await userEvent.click(button);
  expect(source.track.mock.calls.filter(([name]) => name === "ics_link_requested")).toHaveLength(1);
  expect(openLink).not.toHaveBeenCalled();

  await act(async () => last("icsLink").resolve(ICS));
  expect(openLink).toHaveBeenCalledOnce();
  expect(openLink).toHaveBeenCalledWith(ICS.url);
  expect(source.icsLink).toHaveBeenCalledOnce();
});

test("календарь телефона вне MAX — новая вкладка", async () => {
  const open = vi.fn();
  vi.stubGlobal("open", open);
  const { last } = renderIcs();
  await act(async () => last("icsLink").resolve(ICS));
  await userEvent.click(screen.getByRole("button", { name: texts.ics.onceButton }));
  expect(open).toHaveBeenCalledWith(ICS.url, "_blank", "noopener");
});

test("календарь телефона: событий нет — файл не открывается, тост «Пока нечего добавлять»", async () => {
  const openLink = inMax();
  const { last } = renderIcs();
  await act(async () => last("icsLink").resolve({ ...ICS, items: 0 }));
  await userEvent.click(screen.getByRole("button", { name: texts.ics.onceButton }));
  expect(openLink).not.toHaveBeenCalled();
  expect(screen.getByRole("status")).toHaveTextContent(texts.ics.empty);
});

test("календарь телефона: ошибка видна после нажатия, «Повторить» без перезапуска", async () => {
  const openLink = inMax();
  const { source, last } = renderIcs();
  // Заранее запрошенная ссылка не пришла — пока не нажали, плашки нет.
  await act(async () => last("icsLink").reject(new ApiError("network")));
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();

  // Нажатие повторяет запрос; он тоже падает — плашка с «Повторить».
  await userEvent.click(screen.getByRole("button", { name: texts.ics.onceButton }));
  expect(source.icsLink).toHaveBeenCalledTimes(2);
  await act(async () => last("icsLink").reject(new ApiError("server", 503)));
  expect(screen.getByRole("alert")).toHaveTextContent(texts.common.error);
  expect(source.track).toHaveBeenCalledWith("error", { where: "ics", kind: "server" });
  expect(openLink).not.toHaveBeenCalled();

  await userEvent.click(screen.getByRole("button", { name: texts.common.retry }));
  expect(source.icsLink).toHaveBeenCalledTimes(3);
  await act(async () => last("icsLink").resolve(ICS));
  expect(openLink).toHaveBeenCalledWith(ICS.url);
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
});

test("ссылка для подписки: копируется https-адрес ленты, тост «Ссылка скопирована»", async () => {
  const openLink = inMax();
  const { source, last } = renderIcs();
  await act(async () => last("icsLink").resolve({ ...ICS, items: 0 }));
  const writeText = vi.fn(() => Promise.resolve());
  vi.stubGlobal("navigator", { ...navigator, clipboard: { writeText } });

  await act(async () => screen.getByRole("button", { name: texts.ics.subscribeButton }).click());
  // Подписка имеет смысл и при пустом календаре: события появятся позже.
  expect(writeText).toHaveBeenCalledWith(ICS.url);
  expect(screen.getByRole("status")).toHaveTextContent(texts.ics.copied);
  expect(source.track).toHaveBeenCalledWith("ics_link_requested", { action: "copy" });
  expect(openLink).not.toHaveBeenCalled();
});

test("ссылка для подписки: буфер недоступен — ссылка показывается текстом", async () => {
  const { last } = renderIcs();
  await act(async () => last("icsLink").resolve(ICS));
  const writeText = vi.fn(() => Promise.reject(new Error("denied")));
  vi.stubGlobal("navigator", { ...navigator, clipboard: { writeText } });
  // execCommand в jsdom нет — запасной путь тоже не срабатывает, как в WebView без буфера.

  await act(async () => screen.getByRole("button", { name: texts.ics.subscribeButton }).click());
  expect(screen.getByText(texts.ics.copyFailed)).toBeInTheDocument();
  expect(screen.getByText(ICS.url)).toBeInTheDocument();
  expect(screen.queryByText(texts.ics.copied)).not.toBeInTheDocument();
});
