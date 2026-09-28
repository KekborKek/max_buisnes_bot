// Экран 16: четыре состояния (загрузка, «не найдено», ошибка, данные) и действия карточки.
import { act, fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, test, vi } from "vitest";

import { ApiError } from "../data/http";
import { type Navigation, NavigationContext, type Route } from "../router";
import { CONFIRM_MS } from "../shell/useConfirmPress";
import { ToastProvider } from "../shell/Toast";
import { type CalendarStore, CalendarStoreContext } from "../store";
import {
  fakeSource,
  makeItem,
  makeObligationCard,
  makeTaskCard,
  PROFILE,
} from "../test/fakeSource";
import { texts } from "../texts";
import type { CalendarItem, ItemCard, ItemType } from "../types";
import { CardScreen } from "./CardScreen";

const TODAY = "2026-09-23";

afterEach(() => {
  vi.useRealTimers();
  delete window.WebApp;
});

function renderCard(
  opts: {
    type?: ItemType;
    id?: number;
    source?: "list" | "month" | "bot";
    items?: CalendarItem[] | null;
    cards?: Record<string, ItemCard>;
  } = {},
) {
  const { type = "obligation", id = 1, source: from = "list", items = null, cards = {} } = opts;
  const fake = fakeSource();
  const route: Extract<Route, { name: "card" }> = {
    name: "card",
    itemType: type,
    id,
    source: from,
  };
  const nav: Navigation = {
    route,
    depth: 2,
    push: vi.fn(),
    back: vi.fn(),
    switchTab: vi.fn(),
    home: vi.fn(),
  };
  const store: CalendarStore = { items, cards, upsert: vi.fn(), remove: vi.fn() };
  render(
    <ToastProvider>
      <NavigationContext.Provider value={nav}>
        <CalendarStoreContext.Provider value={store}>
          <CardScreen
            source={fake.source}
            route={route}
            today={TODAY}
            timezone={PROFILE.timezone}
          />
        </CalendarStoreContext.Provider>
      </NavigationContext.Provider>
    </ToastProvider>,
  );
  return { ...fake, nav, store };
}

async function loaded(card: ItemCard, opts: Parameters<typeof renderCard>[0] = {}) {
  const ctx = renderCard({ type: card.type, id: card.id, ...opts });
  await act(async () => ctx.last("item").resolve(card));
  return ctx;
}

test("загрузка из списка: заголовок и дата сразу, остальное — серые плашки", () => {
  const { source } = renderCard({ items: [makeItem({ title: "Аванс" })] });
  expect(screen.getByText("Аванс")).toBeInTheDocument();
  expect(screen.getByText(/28 октября, среда/)).toBeInTheDocument();
  expect(screen.getByTestId("skeleton")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: texts.card.markDone })).not.toBeInTheDocument();
  expect(source.item).toHaveBeenCalledWith("obligation", 1);
  expect(source.track).toHaveBeenCalledWith("item_card_opened", {
    item_id: 1,
    item_type: "obligation",
    source: "list",
  });
});

test("загрузка из бота, списка ещё нет: только плашки, без пустого экрана", () => {
  renderCard({ source: "bot" });
  expect(screen.getByTestId("skeleton")).toBeInTheDocument();
});

test("обязательство: все пять блоков, дисклеймер, нет «Перенести» и «Удалить»", async () => {
  await loaded(makeObligationCard({ title: "Аванс" }));
  expect(screen.getByText("Аванс")).toBeInTheDocument();
  expect(screen.getByText(/28 октября, среда · Предстоит ·/)).toHaveTextContent(
    texts.category.taxes,
  );
  expect(screen.getByText(texts.card.sectionHowto)).toBeInTheDocument();
  for (const step of ["Шаг первый.", "Шаг второй.", "Шаг третий."]) {
    expect(screen.getByText(step)).toBeInTheDocument();
  }
  expect(screen.getByText(texts.card.sectionPenalty)).toBeInTheDocument();
  expect(screen.getByText("Последствия пропуска.")).toBeInTheDocument();
  expect(screen.getByText(texts.card.sectionBasis)).toBeInTheDocument();
  expect(screen.getByText("Норма, ст. 1")).toBeInTheDocument();
  expect(screen.getByText(texts.card.checked("20.09.2026"))).toBeInTheDocument();
  expect(screen.getByText(texts.card.disclaimerAdvice)).toBeInTheDocument();

  expect(screen.getByRole("button", { name: texts.card.markDone })).toBeEnabled();
  expect(screen.getByRole("button", { name: texts.card.wrongDate })).toBeEnabled();
  expect(screen.queryByRole("button", { name: texts.card.reschedule })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: texts.card.delete })).not.toBeInTheDocument();
  expect(screen.queryByText(texts.card.disclaimerMark)).not.toBeInTheDocument();
  expect(screen.queryByTestId("skeleton")).not.toBeInTheDocument();
});

test("ссылка на источник открывается через MAX Bridge openLink", async () => {
  const openLink = vi.fn();
  window.WebApp = { initData: "signed", initDataUnsafe: {}, openLink };
  await loaded(makeObligationCard());
  await userEvent.click(screen.getByRole("link", { name: new RegExp(texts.card.source) }));
  expect(openLink).toHaveBeenCalledWith("https://example.com/source");
});

test("перенос с выходного — строка под датой", async () => {
  await loaded(makeObligationCard({ due_date: "2026-10-26", original_date: "2026-10-25" }));
  expect(screen.getByText(texts.card.shifted("25 октября"))).toBeInTheDocument();
});

test("просрочено — плашка сверху", async () => {
  await loaded(makeObligationCard({ due_date: "2026-09-20", status: "overdue" }));
  expect(screen.getByText(texts.card.overdueBanner("20 сентября"))).toBeInTheDocument();
});

test("выполнено — плашка, «Отменить отметку» и дисклеймер отметки", async () => {
  await loaded(makeObligationCard({ status: "done", done_at: "2026-09-22T07:00:00Z" }));
  expect(screen.getByText(texts.card.doneBanner("22 сентября"))).toBeInTheDocument();
  expect(screen.getByRole("button", { name: texts.card.undoDone })).toBeInTheDocument();
  expect(screen.getByText(texts.card.disclaimerMark)).toBeInTheDocument();
});

test("своя задача: напоминание, «Перенести» и «Удалить», без блоков справочника", async () => {
  const { nav } = await loaded(makeTaskCard({ title: "Аренда" }));
  expect(screen.getByText(/5 ноября, четверг · Предстоит ·/)).toHaveTextContent(
    texts.category.custom,
  );
  expect(
    screen.getByText(texts.card.remindAt(texts.card.remindWhen[1], 10, 0)),
  ).toBeInTheDocument();
  expect(screen.queryByText(texts.card.sectionHowto)).not.toBeInTheDocument();
  expect(screen.queryByText(texts.card.sectionBasis)).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: texts.card.wrongDate })).not.toBeInTheDocument();

  await userEvent.click(screen.getByRole("button", { name: texts.card.reschedule }));
  expect(nav.push).toHaveBeenCalledWith({ name: "task", draft: false, taskId: 3 });
});

test("своя задача с любым временем: «в 7:45» — минуты в формате ЧЧ:ММ (TIME-FE, #104)", async () => {
  await loaded(makeTaskCard({ title: "Аренда", remind_hour: 7, remind_minute: 45 }));
  expect(
    screen.getByText(texts.card.remindAt(texts.card.remindWhen[1], 7, 45)),
  ).toBeInTheDocument();
  expect(screen.getByText(/в 7:45/)).toBeInTheDocument();
});

test("выполненная задача — без дисклеймера про обязательство", async () => {
  await loaded(makeTaskCard({ status: "done", done_at: "2026-09-22T07:00:00Z" }));
  expect(screen.getByRole("button", { name: texts.card.undoDone })).toBeInTheDocument();
  expect(screen.queryByText(texts.card.disclaimerMark)).not.toBeInTheDocument();
});

test("не найдено (старая ссылка из бота): notFound и «К списку»", async () => {
  const { last, nav, store } = renderCard({ type: "task", id: 42, source: "bot" });
  await act(async () => last("item").reject(new ApiError("client", 404)));
  expect(screen.getByText(texts.card.notFound)).toBeInTheDocument();
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  expect(store.remove).toHaveBeenCalledWith("task", 42);
  await userEvent.click(screen.getByRole("button", { name: texts.card.backToList }));
  expect(nav.home).toHaveBeenCalledOnce();
});

test("ошибка загрузки: плашка common.error, «Повторить» загружает карточку", async () => {
  const { last, source } = renderCard({ items: [makeItem({ title: "Аванс" })] });
  await act(async () => last("item").reject(new ApiError("network")));
  const alert = screen.getByRole("alert");
  expect(alert).toHaveTextContent(texts.common.error);
  // Заголовок из списка остаётся на экране.
  expect(screen.getByText("Аванс")).toBeInTheDocument();
  expect(source.track).toHaveBeenCalledWith("error", { where: "card", kind: "network" });

  await userEvent.click(within(alert).getByRole("button", { name: texts.common.retry }));
  expect(source.item).toHaveBeenCalledTimes(2);
  await act(async () => last("item").resolve(makeObligationCard({ title: "Аванс" })));
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: texts.card.markDone })).toBeInTheDocument();
});

test("отметка: статус из ответа бэкенда, дисклеймер, список обновлён; снятие отметки", async () => {
  const { last, source, store } = await loaded(makeObligationCard());
  await userEvent.click(screen.getByRole("button", { name: texts.card.markDone }));
  expect(source.markDone).toHaveBeenCalledWith("obligation", 1);
  const doneCard = makeObligationCard({ status: "done", done_at: "2026-09-23T07:00:00Z" });
  await act(async () => last("markDone").resolve(doneCard));

  expect(screen.getByText(texts.card.disclaimerMark)).toBeInTheDocument();
  expect(screen.getByText(texts.card.doneBanner("23 сентября"))).toBeInTheDocument();
  expect(store.upsert).toHaveBeenLastCalledWith(doneCard);
  // Сообщение в бот и события отметки шлёт бэкенд, не мини-апп (D20).
  expect(source.track).not.toHaveBeenCalledWith("item_done", expect.anything());

  await userEvent.click(screen.getByRole("button", { name: texts.card.undoDone }));
  expect(source.undoDone).toHaveBeenCalledWith("obligation", 1);
  await act(async () => last("undoDone").resolve(makeObligationCard({ status: "today" })));
  expect(screen.getByRole("button", { name: texts.card.markDone })).toBeInTheDocument();
  expect(screen.getByText(/· Сегодня ·/)).toBeInTheDocument();
  expect(screen.queryByText(texts.card.disclaimerMark)).not.toBeInTheDocument();
});

test("ошибка отметки: общая плашка, статус прежний, «Повторить» повторяет отметку", async () => {
  const { last, source } = await loaded(makeObligationCard());
  await userEvent.click(screen.getByRole("button", { name: texts.card.markDone }));
  await act(async () => last("markDone").reject(new ApiError("server", 502)));

  expect(screen.getByRole("alert")).toHaveTextContent(texts.common.error);
  expect(screen.getByText(/· Предстоит ·/)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: texts.card.markDone })).toBeEnabled();
  expect(source.track).toHaveBeenCalledWith("error", { where: "card_done", kind: "server" });

  await userEvent.click(screen.getByRole("button", { name: texts.common.retry }));
  expect(source.markDone).toHaveBeenCalledTimes(2);
  await act(async () =>
    last("markDone").resolve(
      makeObligationCard({ status: "done", done_at: "2026-09-23T07:00:00Z" }),
    ),
  );
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: texts.card.undoDone })).toBeInTheDocument();
});

test("«Неверный срок»: уведомление внизу, кнопка неактивна", async () => {
  const { last, source } = await loaded(makeObligationCard());
  await userEvent.click(screen.getByRole("button", { name: texts.card.wrongDate }));
  expect(source.reportWrongDate).toHaveBeenCalledWith(1);
  await act(async () => last("reportWrongDate").resolve(undefined));
  expect(screen.getByText(texts.card.wrongDateThanks)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: texts.card.wrongDate })).toBeDisabled();
});

test("удаление: первое нажатие — «Точно удалить?» на 3 с, потом кнопка возвращается", async () => {
  const { source } = await loaded(makeTaskCard());
  vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
  fireEvent.click(screen.getByRole("button", { name: texts.card.delete }));
  expect(screen.getByRole("button", { name: texts.card.deleteConfirm })).toBeInTheDocument();
  act(() => vi.advanceTimersByTime(CONFIRM_MS - 1));
  expect(screen.getByRole("button", { name: texts.card.deleteConfirm })).toBeInTheDocument();
  act(() => vi.advanceTimersByTime(1));
  expect(screen.getByRole("button", { name: texts.card.delete })).toBeInTheDocument();
  expect(source.deleteTask).not.toHaveBeenCalled();
});

test("удаление: второе нажатие — DELETE, экран 14 и «Задача удалена»", async () => {
  const { last, source, nav, store } = await loaded(makeTaskCard());
  await userEvent.click(screen.getByRole("button", { name: texts.card.delete }));
  await userEvent.click(screen.getByRole("button", { name: texts.card.deleteConfirm }));
  expect(source.deleteTask).toHaveBeenCalledWith(3);
  await act(async () => last("deleteTask").resolve(undefined));
  expect(store.remove).toHaveBeenCalledWith("task", 3);
  expect(nav.home).toHaveBeenCalledOnce();
  expect(screen.getByText(texts.card.deleted)).toBeInTheDocument();
});

// --- «Поделиться» (SHARE) ------------------------------------------------------------------------

const SHARE = {
  code: "abcdEFGH1234",
  link: "https://max.ru/test_bot?startapp=share_abcdEFGH1234",
  title: "Аренда",
  due_date: "2026-11-05",
};
const SHARE_TEXT = texts.share.message("Аренда", "5 ноября");

function stubClipboard() {
  const writeText = vi.fn(() => Promise.resolve());
  Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
  return writeText;
}

afterEach(() => {
  vi.unstubAllEnvs();
  Reflect.deleteProperty(navigator, "clipboard");
});

async function clickShare() {
  await act(async () => {
    fireEvent.click(screen.getByRole("button", { name: texts.share.button }));
  });
}

test("«Поделиться» есть и у задачи, и у обязательства; ссылка готовится при открытии", async () => {
  const { source } = await loaded(makeObligationCard());
  expect(screen.getByRole("button", { name: texts.share.button })).toBeEnabled();
  expect(source.createShare).toHaveBeenCalledWith("obligation", 1);
  // Само открытие карточки — не «поделились»: событие пишется только по нажатию.
  expect(source.track).not.toHaveBeenCalledWith("share_created", expect.anything());
});

test("в MAX «Поделиться» зовёт shareMaxContent с текстом и ссылкой, пишет share_created", async () => {
  const shareMaxContent = vi.fn(() => Promise.resolve());
  window.WebApp = { initData: "signed", initDataUnsafe: {}, shareMaxContent };
  const writeText = stubClipboard();
  const { last, source } = await loaded(makeTaskCard());
  expect(source.createShare).toHaveBeenCalledWith("task", 3);
  await act(async () => last("createShare").resolve(SHARE));

  await clickShare();
  expect(shareMaxContent).toHaveBeenCalledWith({ text: SHARE_TEXT, link: SHARE.link });
  expect(source.track).toHaveBeenCalledWith("share_created", { item_type: "task", item_id: 3 });
  expect(writeText).not.toHaveBeenCalled();
  expect(screen.queryByText(texts.share.copied)).not.toBeInTheDocument();
});

test("вне MAX — фолбэк: текст со ссылкой в буфер и тост «Ссылка скопирована»", async () => {
  const writeText = stubClipboard();
  const { last } = await loaded(makeTaskCard());
  await act(async () => last("createShare").resolve(SHARE));

  await clickShare();
  expect(writeText).toHaveBeenCalledWith(`${SHARE_TEXT}\n${SHARE.link}`);
  expect(screen.getByText(texts.share.copied)).toBeInTheDocument();
});

test("shareMaxContent отклонил вызов — тоже фолбэк в буфер", async () => {
  const shareMaxContent = vi.fn(() => Promise.reject(new Error("no gesture")));
  window.WebApp = { initData: "signed", initDataUnsafe: {}, shareMaxContent };
  const writeText = stubClipboard();
  const { last } = await loaded(makeTaskCard());
  await act(async () => last("createShare").resolve(SHARE));

  await clickShare();
  expect(shareMaxContent).toHaveBeenCalled();
  expect(writeText).toHaveBeenCalledWith(`${SHARE_TEXT}\n${SHARE.link}`);
  expect(screen.getByText(texts.share.copied)).toBeInTheDocument();
});

test("бэкенд без имени бота (link: null) — ссылка из VITE_BOT_URL", async () => {
  vi.stubEnv("VITE_BOT_URL", "https://max.ru/test_bot");
  const writeText = stubClipboard();
  const { last } = await loaded(makeTaskCard());
  await act(async () => last("createShare").resolve({ ...SHARE, link: null }));

  await clickShare();
  expect(writeText).toHaveBeenCalledWith(
    `${SHARE_TEXT}\nhttps://max.ru/test_bot?startapp=share_abcdEFGH1234`,
  );
});

test("ссылка не успела подготовиться — запрос по нажатию; ошибка — плашка, карточка на месте", async () => {
  const writeText = stubClipboard();
  const { last, source } = await loaded(makeTaskCard());
  await act(async () => last("createShare").reject(new ApiError("network")));

  await clickShare();
  expect(source.createShare).toHaveBeenCalledTimes(2);
  await act(async () => last("createShare").reject(new ApiError("network")));
  expect(screen.getByRole("alert")).toHaveTextContent(texts.common.error);
  expect(screen.getByRole("button", { name: texts.card.markDone })).toBeEnabled();

  await userEvent.click(within(screen.getByRole("alert")).getByRole("button"));
  expect(source.createShare).toHaveBeenCalledTimes(3);
  await act(async () => last("createShare").resolve(SHARE));
  expect(writeText).toHaveBeenCalledWith(`${SHARE_TEXT}\n${SHARE.link}`);
  expect(screen.getByText(texts.share.copied)).toBeInTheDocument();
});
