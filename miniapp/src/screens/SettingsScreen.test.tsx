// Экран 13 «Настройки» (решение 29.09 — две секции). Напоминания: начальные значения, 1 день не
// выключается, «Сохранить» только при изменениях, PUT с полным телом, тост и возврат; ошибки
// (сеть, 503, 409, 401) — форма не сбрасывается. Тема — перенесена с 19; календарь телефона
// вынесен дальше на отдельный экран (IcsScreen.test.tsx, задача UI-EXPORT, 29.09).
import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

import { ApiError } from "../data/http";
import { type Navigation, NavigationContext } from "../router";
import { ThemeProvider } from "../shell/Theme";
import { ToastProvider } from "../shell/Toast";
import { fakeSource, PROFILE } from "../test/fakeSource";
import { texts } from "../texts";
import type { Profile } from "../types";
import { type SettingsRoute, SettingsScreen, settingsOf } from "./SettingsScreen";

beforeEach(() => {
  // Секция темы (FRONT-20) читает системную тему — jsdom matchMedia не знает.
  vi.stubGlobal("matchMedia", () => ({
    matches: false,
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
  }));
});

afterEach(() => {
  vi.unstubAllGlobals();
  localStorage.clear();
  delete document.documentElement.dataset.theme;
  delete window.WebApp;
});

const CUSTOM: Profile = {
  ...PROFILE,
  timezone: "Asia/Yekaterinburg",
  reminders: { d30: false, d7: true, hour: 18, digest: false },
};

function renderSettings(profile: Profile = CUSTOM, source: SettingsRoute["source"] = "profile") {
  const fake = fakeSource();
  const route: SettingsRoute = { name: "settings", source };
  const nav: Navigation = {
    route,
    depth: 2,
    push: vi.fn(),
    back: vi.fn(),
    switchTab: vi.fn(),
    home: vi.fn(),
  };
  const onSaved = vi.fn();
  const view = render(
    <ThemeProvider>
      <ToastProvider>
        <NavigationContext.Provider value={nav}>
          <SettingsScreen source={fake.source} route={route} profile={profile} onSaved={onSaved} />
        </NavigationContext.Provider>
      </ToastProvider>
    </ThemeProvider>,
  );
  return { ...fake, nav, onSaved, unmount: view.unmount };
}

const sw = (name: string) => screen.getByRole("switch", { name: new RegExp(`^${name}`) });
const hourBtn = (hour: number) =>
  screen.getByRole("button", { name: texts.settings.hourOption(hour) });
const zone = (label: string) => screen.getByRole("radio", { name: label });
const saveBtn = () => screen.getByRole("button", { name: texts.settings.save });
const showAllZonesBtn = () => screen.getByRole("button", { name: texts.settings.timezoneShowAll });
const collapseZonesBtn = () =>
  screen.getByRole("button", { name: texts.settings.timezoneCollapse });

test("начальные значения — из профиля; открытие шлёт settings_opened", () => {
  const { source } = renderSettings(CUSTOM, "bot");
  expect(screen.getByText(texts.settings.title)).toBeInTheDocument();
  expect(sw(texts.settings.d30)).not.toBeChecked();
  expect(sw(texts.settings.d7)).toBeChecked();
  expect(hourBtn(18)).toHaveAttribute("aria-pressed", "true");
  expect(hourBtn(10)).toHaveAttribute("aria-pressed", "false");
  expect(zone("Екатеринбург (UTC+5)")).toBeChecked();
  expect(sw(texts.settings.digest)).not.toBeChecked();
  expect(source.track).toHaveBeenCalledWith("settings_opened", { source: "bot" });
});

test("список поясов свёрнут по умолчанию — виден только выбранный; разворот и «Свернуть» обратно", async () => {
  renderSettings(CUSTOM, "bot");
  // Свёрнуто: одна строка — текущий пояс, кнопка «Показать все пояса».
  expect(screen.getAllByRole("radio")).toHaveLength(1);
  expect(zone("Екатеринбург (UTC+5)")).toBeChecked();
  expect(showAllZonesBtn()).toHaveAttribute("aria-expanded", "false");

  // Разворот — весь список экрана 2; выбранный пояс остаётся отмеченным.
  await userEvent.click(showAllZonesBtn());
  expect(screen.getAllByRole("radio")).toHaveLength(Object.keys(texts.timezone).length);
  expect(zone("Екатеринбург (UTC+5)")).toBeChecked();
  expect(collapseZonesBtn()).toHaveAttribute("aria-expanded", "true");

  // «Свернуть» — обратно одна строка.
  await userEvent.click(collapseZonesBtn());
  expect(screen.getAllByRole("radio")).toHaveLength(1);
  expect(showAllZonesBtn()).toHaveAttribute("aria-expanded", "false");
});

test("профиль без reminders (бэкенд до #85) — умолчания", () => {
  const legacy: Profile = { ...PROFILE };
  delete legacy.reminders;
  expect(settingsOf(legacy)).toEqual({
    d30: true,
    d7: true,
    hour: 10,
    digest: true,
    timezone: "Europe/Moscow",
  });
  renderSettings(legacy);
  expect(sw(texts.settings.d30)).toBeChecked();
  expect(hourBtn(10)).toHaveAttribute("aria-pressed", "true");
  expect(sw(texts.settings.digest)).toBeChecked();
});

test("за 1 день — включено, не переключается, с подписью", async () => {
  renderSettings();
  const d1 = sw(texts.settings.d1);
  expect(d1).toBeChecked();
  expect(d1).toHaveAttribute("aria-disabled", "true");
  expect(screen.getByText(texts.settings.d1Note)).toBeInTheDocument();
  await userEvent.click(d1);
  await userEvent.click(screen.getByText(texts.settings.d1));
  d1.focus();
  await userEvent.keyboard(" ");
  expect(d1).toBeChecked();
  expect(saveBtn()).toBeDisabled();
});

test("«Сохранить» неактивна без изменений и снова неактивна, если вернуть как было", async () => {
  renderSettings();
  expect(saveBtn()).toBeDisabled();
  await userEvent.click(hourBtn(9));
  expect(saveBtn()).toBeEnabled();
  await userEvent.click(hourBtn(18));
  expect(saveBtn()).toBeDisabled();
});

test("сохранение: PUT с полным телом, профиль — в оболочку, тост и «Назад»", async () => {
  const { source, last, nav, onSaved } = renderSettings();
  await userEvent.click(sw(texts.settings.d30));
  await userEvent.click(sw(texts.settings.d7));
  await userEvent.click(hourBtn(9));
  await userEvent.click(showAllZonesBtn());
  await userEvent.click(zone("Владивосток (UTC+10)"));
  await userEvent.click(sw(texts.settings.digest));
  await userEvent.click(saveBtn());
  expect(source.saveSettings).toHaveBeenCalledWith({
    d30: true,
    d7: false,
    hour: 9,
    digest: true,
    timezone: "Asia/Vladivostok",
  });
  expect(nav.back).not.toHaveBeenCalled();

  const saved: Profile = {
    ...CUSTOM,
    timezone: "Asia/Vladivostok",
    reminders: { d30: true, d7: false, hour: 9, digest: true },
  };
  await act(async () => last("saveSettings").resolve(saved));
  expect(onSaved).toHaveBeenCalledWith(saved);
  expect(nav.back).toHaveBeenCalledOnce();
  // В тесте «Назад» — заглушка, экран остаётся; тост — в слоте ToastProvider.
  expect(document.querySelector(".toast-slot")).toHaveTextContent(texts.settings.saved);
});

test("двойное нажатие «Сохранить» — один запрос", async () => {
  const { source } = renderSettings();
  await userEvent.click(hourBtn(10));
  const button = saveBtn();
  await userEvent.click(button);
  await userEvent.click(button);
  expect(source.saveSettings).toHaveBeenCalledOnce();
});

test("ошибка сети: плашка, значения формы на месте, «Повторить» отправляет снова", async () => {
  const { source, last, nav, onSaved } = renderSettings();
  await userEvent.click(hourBtn(10));
  await userEvent.click(showAllZonesBtn());
  await userEvent.click(zone("Омск (UTC+6)"));
  await userEvent.click(saveBtn());
  await act(async () => last("saveSettings").reject(new ApiError("network")));

  expect(screen.getByRole("alert")).toHaveTextContent(texts.common.error);
  expect(source.track).toHaveBeenCalledWith("error", { where: "settings", kind: "network" });
  expect(hourBtn(10)).toHaveAttribute("aria-pressed", "true");
  expect(zone("Омск (UTC+6)")).toBeChecked();
  expect(nav.back).not.toHaveBeenCalled();
  expect(onSaved).not.toHaveBeenCalled();

  await userEvent.click(screen.getByRole("button", { name: texts.common.retry }));
  expect(source.saveSettings).toHaveBeenCalledTimes(2);
  expect(source.saveSettings).toHaveBeenLastCalledWith(
    expect.objectContaining({ hour: 10, timezone: "Asia/Omsk" }),
  );
  await act(async () => last("saveSettings").resolve({ ...CUSTOM, timezone: "Asia/Omsk" }));
  expect(nav.back).toHaveBeenCalledOnce();
});

test("503 reference_unavailable — ошибка сервиса: плашка с «Повторить», форма на месте", async () => {
  const { source, last, nav } = renderSettings();
  await userEvent.click(sw(texts.settings.digest));
  await userEvent.click(saveBtn());
  await act(async () => last("saveSettings").reject(new ApiError("server", 503)));

  expect(screen.getByRole("alert")).toHaveTextContent(texts.common.error);
  expect(source.track).toHaveBeenCalledWith("error", { where: "settings", kind: "server" });
  expect(sw(texts.settings.digest)).toBeChecked();
  expect(saveBtn()).toBeEnabled();
  expect(nav.back).not.toHaveBeenCalled();

  await userEvent.click(screen.getByRole("button", { name: texts.common.retry }));
  expect(source.saveSettings).toHaveBeenCalledTimes(2);
});

test("409 — профиль не заполнен: текст экрана 19, без «Повторить»", async () => {
  const { source, last } = renderSettings();
  await userEvent.click(hourBtn(10));
  await userEvent.click(saveBtn());
  await act(async () => last("saveSettings").reject(new ApiError("client", 409)));
  expect(screen.getByRole("alert")).toHaveTextContent(texts.profile.incomplete);
  expect(screen.queryByRole("button", { name: texts.common.retry })).not.toBeInTheDocument();
  expect(source.track).toHaveBeenCalledWith("error", { where: "settings", kind: "incomplete" });
  expect(hourBtn(10)).toHaveAttribute("aria-pressed", "true");
});

test("401 — как везде: common.reopen без «Повторить»", async () => {
  const { last } = renderSettings();
  await userEvent.click(hourBtn(10));
  await userEvent.click(saveBtn());
  await act(async () => last("saveSettings").reject(new ApiError("unauthorized", 401)));
  expect(screen.getByRole("alert")).toHaveTextContent(texts.common.reopen);
  expect(screen.queryByRole("button", { name: texts.common.retry })).not.toBeInTheDocument();
});

test("пояс вне списка экрана 2: строка с идентификатором, подсказка, «Сохранить» — после выбора из списка", async () => {
  const { source } = renderSettings({ ...CUSTOM, timezone: "Europe/Berlin" });
  // Свёрнуто: единственная видимая строка — неизвестный пояс профиля.
  expect(screen.getAllByRole("radio")).toHaveLength(1);
  expect(zone("Europe/Berlin")).toBeChecked();
  expect(screen.getByText(texts.settings.timezoneUnknown)).toBeInTheDocument();
  expect(saveBtn()).toBeDisabled();

  // Другие поля поменяли, а пояс всё ещё не из списка — сохранить нельзя (бэкенд ответил бы 422).
  await userEvent.click(hourBtn(9));
  expect(saveBtn()).toBeDisabled();
  await userEvent.click(saveBtn());
  expect(source.saveSettings).not.toHaveBeenCalled();

  await userEvent.click(showAllZonesBtn());
  await userEvent.click(zone("Москва, UTC+3"));
  expect(screen.queryByText(texts.settings.timezoneUnknown)).not.toBeInTheDocument();
  expect(saveBtn()).toBeEnabled();
  await userEvent.click(saveBtn());
  expect(source.saveSettings).toHaveBeenCalledWith(
    expect.objectContaining({ hour: 9, timezone: "Europe/Moscow" }),
  );
});

test("пояс из списка — подсказки нет", () => {
  renderSettings();
  expect(screen.queryByText(texts.settings.timezoneUnknown)).not.toBeInTheDocument();
});

test("правка формы после ошибки снимает плашку", async () => {
  const { last } = renderSettings();
  await userEvent.click(hourBtn(10));
  await userEvent.click(saveBtn());
  await act(async () => last("saveSettings").reject(new ApiError("network")));
  expect(screen.getByRole("alert")).toBeInTheDocument();

  // Вернули как было: «Сохранить» неактивна, и плашки с бесполезным «Повторить» нет.
  await userEvent.click(hourBtn(18));
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  expect(saveBtn()).toBeDisabled();
});

test("во время запроса переключатели aria-disabled (не disabled) и не меняются", async () => {
  renderSettings();
  await userEvent.click(hourBtn(10));
  await userEvent.click(saveBtn());
  for (const name of [texts.settings.d30, texts.settings.d7, texts.settings.digest]) {
    const toggle = sw(name);
    const before = (toggle as HTMLInputElement).checked;
    expect(toggle).toHaveAttribute("aria-disabled", "true");
    expect(toggle).not.toBeDisabled();
    await userEvent.click(toggle);
    expect((toggle as HTMLInputElement).checked).toBe(before);
  }
});

test("ушли с экрана до ответа: профиль — в оболочку, но без «Назад» и тоста", async () => {
  const { last, nav, onSaved, unmount } = renderSettings();
  await userEvent.click(hourBtn(10));
  await userEvent.click(saveBtn());
  unmount();
  const saved: Profile = { ...CUSTOM, reminders: { ...CUSTOM.reminders!, hour: 10 } };
  await act(async () => last("saveSettings").resolve(saved));
  expect(onSaved).toHaveBeenCalledWith(saved);
  expect(nav.back).not.toHaveBeenCalled();
});

test("две секции по порядку: напоминания, тема оформления (29.09; календарь телефона — отдельный экран)", () => {
  renderSettings();
  expect(screen.getByText("Настройки")).toBeInTheDocument();
  const reminders = screen.getByText(texts.settings.sectionReminders);
  const theme = screen.getByRole("group", { name: texts.settings.theme });
  const following = (a: Node, b: Node) =>
    Boolean(a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING);
  expect(following(reminders, saveBtn())).toBe(true);
  expect(following(saveBtn(), theme)).toBe(true);
  expect(screen.queryByRole("button", { name: texts.ics.onceButton })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: texts.ics.subscribeButton })).not.toBeInTheDocument();
});

test("смена темы не трогает форму напоминаний: «Сохранить» остаётся неактивной", async () => {
  const { source } = renderSettings();
  await userEvent.click(screen.getByRole("button", { name: texts.settings.themeLight }));
  expect(saveBtn()).toBeDisabled();
  expect(source.saveSettings).not.toHaveBeenCalled();
  expect(localStorage.getItem("profile.theme")).toBe("light");
});

// --- Тема оформления (FRONT-20, #83), перенесена с экрана 19 (29.09) ---------------------------

test("тема: сегмент с тремя вариантами, выбор красит активную кнопку, html и шлёт theme_changed", async () => {
  const { source } = renderSettings();
  const systemBtn = screen.getByRole("button", { name: texts.settings.themeSystem });
  const lightBtn = screen.getByRole("button", { name: texts.settings.themeLight });
  const darkBtn = screen.getByRole("button", { name: texts.settings.themeDark });

  // Без сохранённого выбора активна «Как в системе» (matchMedia замокан на светлую).
  expect(systemBtn).toHaveAttribute("aria-pressed", "true");
  expect(lightBtn).toHaveAttribute("aria-pressed", "false");
  expect(darkBtn).toHaveAttribute("aria-pressed", "false");

  await userEvent.click(darkBtn);
  expect(darkBtn).toHaveAttribute("aria-pressed", "true");
  expect(systemBtn).toHaveAttribute("aria-pressed", "false");
  expect(document.documentElement.dataset.theme).toBe("dark");
  expect(source.track).toHaveBeenCalledWith("theme_changed", { theme: "dark" });
});

test("тема: повторный клик по уже активному варианту событие не повторяет", async () => {
  const { source } = renderSettings();
  await userEvent.click(screen.getByRole("button", { name: texts.settings.themeSystem }));
  expect(source.track).not.toHaveBeenCalledWith("theme_changed", expect.anything());
});
