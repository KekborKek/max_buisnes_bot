// Экран 19: четыре состояния (загрузка, пусто, ошибка, получилось), «Пересобрать», «Изменить».
import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

import { ApiError } from "../data/http";
import { ToastProvider } from "../shell/Toast";
import { fakeSource, makeMe, PROFILE } from "../test/fakeSource";
import { texts } from "../texts";
import type { Profile, RebuildResult } from "../types";
import { ProfileScreen } from "./ProfileScreen";

beforeEach(() => {
  vi.stubEnv("VITE_BOT_URL", "https://max.ru/test_bot");
});

afterEach(() => {
  vi.unstubAllEnvs();
  delete window.WebApp;
});

const FRESH: Profile = {
  ...PROFILE,
  income_band: "10_20",
  regime: "usn15",
  has_employees: true,
  timezone: "Asia/Vladivostok",
  reference_checked_at: "2026-09-20",
};

function renderProfile() {
  const fake = fakeSource();
  const onProfile = vi.fn();
  const onRebuilt = vi.fn();
  render(
    <ToastProvider>
      <ProfileScreen
        source={fake.source}
        initial={PROFILE}
        onProfile={onProfile}
        onRebuilt={onRebuilt}
      />
    </ToastProvider>,
  );
  return { ...fake, onProfile, onRebuilt };
}

async function renderReady(profile: Profile = FRESH) {
  const view = renderProfile();
  await act(async () => view.lastMe().resolve({ ...makeMe(true), profile }));
  return view;
}

test("грузится: профиль запрашивается заново, на месте строк — скелетон", () => {
  const { source } = renderProfile();
  expect(source.me).toHaveBeenCalledWith(null);
  expect(screen.getByTestId("skeleton")).toBeInTheDocument();
  expect(screen.getByText(texts.profile.disclaimer)).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: texts.profile.rebuild })).not.toBeInTheDocument();
});

test("получилось: доход, режим, сотрудники, пояс, дата сверки и disclaimer.profile", async () => {
  const { onProfile, source } = await renderReady();
  expect(screen.getByText(texts.income["10_20"])).toBeInTheDocument();
  expect(screen.getByText(texts.regime.usn15)).toBeInTheDocument();
  expect(screen.getByText(texts.employees.yes)).toBeInTheDocument();
  expect(screen.getByText("Владивосток (UTC+10)")).toBeInTheDocument();
  expect(screen.getByText(texts.card.checked("20.09.2026"))).toBeInTheDocument();
  expect(screen.getByText(texts.profile.disclaimer)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: texts.profile.rebuild })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: texts.profile.edit })).toBeInTheDocument();
  // Свежий профиль уходит в оболочку — шапка 14/15 и пояс обновятся.
  expect(onProfile).toHaveBeenCalledWith(FRESH);
  // Открытие экрана 19 событий не шлёт (решение планировщика по #78).
  expect(source.track).not.toHaveBeenCalled();
});

test("пояс вне списка экрана 2 показывается идентификатором, без даты сверки — без строки", async () => {
  await renderReady({ ...FRESH, timezone: "Europe/Berlin", reference_checked_at: null });
  expect(screen.getByText("Europe/Berlin")).toBeInTheDocument();
  // Подпись строки — TODO, поэтому ищем по значению: «сверено …» нет.
  expect(screen.queryByText(/^сверено/)).not.toBeInTheDocument();
});

test("пусто: профиля больше нет — тексты экрана 18 и путь в бота", async () => {
  const { lastMe } = renderProfile();
  await act(async () => lastMe().resolve(makeMe(false)));
  expect(screen.getByText(texts.gate.title)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: texts.gate.openChat })).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: texts.profile.rebuild })).not.toBeInTheDocument();
});

test("ошибка: плашка с «Повторить», профиль из запуска остаётся; повтор загружает свежий", async () => {
  const { lastMe, source } = renderProfile();
  await act(async () => lastMe().reject(new ApiError("network")));

  expect(screen.getByRole("alert")).toHaveTextContent(texts.common.error);
  expect(screen.getByText(texts.income.lt10)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: texts.profile.rebuild })).toBeInTheDocument();
  expect(source.track).toHaveBeenCalledWith("error", { where: "profile", kind: "network" });

  await userEvent.click(screen.getByRole("button", { name: texts.common.retry }));
  await act(async () => lastMe().resolve({ ...makeMe(true), profile: FRESH }));
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  expect(screen.getByText(texts.income["10_20"])).toBeInTheDocument();
  expect(source.me).toHaveBeenCalledTimes(2);
});

test("«Пересобрать»: кнопка занята, пока идёт запрос; после — тост и сигнал оболочке", async () => {
  const { source, last, onRebuilt, onProfile } = await renderReady();
  const button = screen.getByRole("button", { name: texts.profile.rebuild });
  await userEvent.click(button);
  await userEvent.click(button);
  expect(source.rebuild).toHaveBeenCalledOnce();

  const result: RebuildResult = {
    items_count: 23,
    nearest_due_date: "2026-10-28",
    profile: { ...FRESH, calendar_built_at: "2026-09-23T07:00:00Z" },
  };
  await act(async () => last("rebuild").resolve(result));
  expect(onRebuilt).toHaveBeenCalledWith(result);
  expect(onProfile).toHaveBeenLastCalledWith(result.profile);
  expect(screen.getByRole("status")).toHaveTextContent(texts.profile.rebuilt);
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
});

test("ошибка пересборки: плашка с «Повторить», повтор без перезапуска", async () => {
  const { source, last, onRebuilt } = await renderReady();
  await userEvent.click(screen.getByRole("button", { name: texts.profile.rebuild }));
  await act(async () => last("rebuild").reject(new ApiError("server", 503)));

  expect(screen.getByRole("alert")).toHaveTextContent(texts.common.error);
  expect(source.track).toHaveBeenCalledWith("error", { where: "rebuild", kind: "server" });
  expect(onRebuilt).not.toHaveBeenCalled();
  // Профиль на экране остался.
  expect(screen.getByText(texts.income["10_20"])).toBeInTheDocument();

  await userEvent.click(screen.getByRole("button", { name: texts.common.retry }));
  expect(source.rebuild).toHaveBeenCalledTimes(2);
});

test("409 — профиль не заполнен: своя плашка без «Повторить»", async () => {
  const { source, last } = await renderReady();
  await userEvent.click(screen.getByRole("button", { name: texts.profile.rebuild }));
  await act(async () => last("rebuild").reject(new ApiError("client", 409)));
  expect(screen.getByRole("alert")).toHaveTextContent(texts.profile.incomplete);
  expect(screen.queryByRole("button", { name: texts.common.retry })).not.toBeInTheDocument();
  expect(source.track).toHaveBeenCalledWith("error", { where: "rebuild", kind: "incomplete" });
});

test("«Изменить» в MAX — openMaxLink с диплинком бота ?start=profile_edit", async () => {
  const openMaxLink = vi.fn();
  window.WebApp = { initData: "signed", initDataUnsafe: {}, openMaxLink };
  await renderReady();
  await userEvent.click(screen.getByRole("button", { name: texts.profile.edit }));
  expect(openMaxLink).toHaveBeenCalledWith("https://max.ru/test_bot?start=profile_edit");
});

test("без VITE_BOT_URL «Изменить» нет, «Пересобрать» есть", async () => {
  vi.stubEnv("VITE_BOT_URL", "");
  await renderReady();
  expect(screen.queryByRole("button", { name: texts.profile.edit })).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: texts.profile.rebuild })).toBeInTheDocument();
});
