// Тема оформления (FRONT-20, #83): по умолчанию — как в системе, следит за matchMedia; явный
// выбор применяется сразу, красит data-theme на <html> и переживает перезапуск; localStorage
// битый или недоступный не роняет приложение.
import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, test, vi } from "vitest";

import { readThemePref, ThemeProvider, useTheme } from "./Theme";

type MediaListener = (e: { matches: boolean }) => void;

/** Одна и та же MediaQueryList на все вызовы matchMedia() — иначе слушатель отдельного вызова
 * не увидит смену matches. change() будит всех подписчиков, как реальное системное событие. */
function fakeMatchMedia(initialMatches: boolean) {
  let matches = initialMatches;
  const listeners = new Set<MediaListener>();
  const mql = {
    get matches() {
      return matches;
    },
    addEventListener: (_type: string, cb: MediaListener) => listeners.add(cb),
    removeEventListener: (_type: string, cb: MediaListener) => listeners.delete(cb),
  };
  return {
    matchMedia: () => mql,
    change(next: boolean) {
      matches = next;
      listeners.forEach((cb) => cb({ matches }));
    },
  };
}

function Probe() {
  const { pref, scheme, setPref } = useTheme();
  return (
    <div>
      <span data-testid="pref">{pref}</span>
      <span data-testid="scheme">{scheme}</span>
      <button onClick={() => setPref("system")}>system</button>
      <button onClick={() => setPref("light")}>light</button>
      <button onClick={() => setPref("dark")}>dark</button>
    </div>
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  localStorage.clear();
  delete document.documentElement.dataset.theme;
  document.documentElement.style.colorScheme = "";
});

test("без сохранённого выбора тема системная и меняется вслед за matchMedia", () => {
  const fake = fakeMatchMedia(false);
  vi.stubGlobal("matchMedia", fake.matchMedia);

  render(
    <ThemeProvider>
      <Probe />
    </ThemeProvider>,
  );
  expect(screen.getByTestId("pref")).toHaveTextContent("system");
  expect(screen.getByTestId("scheme")).toHaveTextContent("light");
  expect(document.documentElement.dataset.theme).toBe("light");

  // Пользователь поменял системную тему телефона, не трогая настройки приложения.
  act(() => fake.change(true));
  expect(screen.getByTestId("scheme")).toHaveTextContent("dark");
  expect(document.documentElement.dataset.theme).toBe("dark");
});

test("выбор «Светлая»/«Тёмная» применяется сразу, красит html и переживает перезапуск", async () => {
  const fake = fakeMatchMedia(true); // система тёмная — явный выбор должен её перебивать
  vi.stubGlobal("matchMedia", fake.matchMedia);
  const user = userEvent.setup();

  const { unmount } = render(
    <ThemeProvider>
      <Probe />
    </ThemeProvider>,
  );
  await user.click(screen.getByRole("button", { name: "light" }));
  expect(screen.getByTestId("pref")).toHaveTextContent("light");
  expect(screen.getByTestId("scheme")).toHaveTextContent("light");
  expect(document.documentElement.dataset.theme).toBe("light");
  expect(document.documentElement.style.colorScheme).toBe("light");
  expect(localStorage.getItem("profile.theme")).toBe("light");

  // «Перезапуск»: новый монтаж ThemeProvider, как при открытии мини-аппа заново.
  unmount();
  render(
    <ThemeProvider>
      <Probe />
    </ThemeProvider>,
  );
  expect(screen.getByTestId("pref")).toHaveTextContent("light");
  expect(screen.getByTestId("scheme")).toHaveTextContent("light");
  expect(document.documentElement.style.colorScheme).toBe("light");
});

test("недоступный localStorage → «Как в системе», без падения", async () => {
  const fake = fakeMatchMedia(false);
  vi.stubGlobal("matchMedia", fake.matchMedia);
  vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
    throw new Error("denied");
  });
  vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
    throw new Error("denied");
  });
  const user = userEvent.setup();

  expect(() =>
    render(
      <ThemeProvider>
        <Probe />
      </ThemeProvider>,
    ),
  ).not.toThrow();
  expect(screen.getByTestId("pref")).toHaveTextContent("system");
  expect(readThemePref()).toBe("system");

  // Явный выбор при бросающем setItem не должен ронять приложение: тема применяется
  // (просто не переживёт перезапуск), а не откатывается и не падает.
  await expect(user.click(screen.getByRole("button", { name: "dark" }))).resolves.not.toThrow();
  expect(screen.getByTestId("pref")).toHaveTextContent("dark");
  expect(screen.getByTestId("scheme")).toHaveTextContent("dark");
  expect(document.documentElement.dataset.theme).toBe("dark");
  expect(document.documentElement.style.colorScheme).toBe("dark");
});

test("битое значение в localStorage → «Как в системе», без падения", () => {
  localStorage.setItem("profile.theme", "purple");
  const fake = fakeMatchMedia(false);
  vi.stubGlobal("matchMedia", fake.matchMedia);

  render(
    <ThemeProvider>
      <Probe />
    </ThemeProvider>,
  );
  expect(screen.getByTestId("pref")).toHaveTextContent("system");
});

test("явный выбор игнорирует смену системной темы", async () => {
  const fake = fakeMatchMedia(false); // система светлая
  vi.stubGlobal("matchMedia", fake.matchMedia);
  const user = userEvent.setup();

  render(
    <ThemeProvider>
      <Probe />
    </ThemeProvider>,
  );
  await user.click(screen.getByRole("button", { name: "light" }));
  expect(screen.getByTestId("pref")).toHaveTextContent("light");
  expect(screen.getByTestId("scheme")).toHaveTextContent("light");

  // Система переключилась на тёмную, но пользователь уже выбрал светлую явно — это не «system».
  act(() => fake.change(true));
  expect(screen.getByTestId("pref")).toHaveTextContent("light");
  expect(screen.getByTestId("scheme")).toHaveTextContent("light");
  expect(document.documentElement.dataset.theme).toBe("light");
  expect(document.documentElement.style.colorScheme).toBe("light");
});

test("MaxUI получает выбранную colorScheme (класс MaxUI_colorScheme_dark на обёртке)", async () => {
  const fake = fakeMatchMedia(false);
  vi.stubGlobal("matchMedia", fake.matchMedia);
  const user = userEvent.setup();

  render(
    <ThemeProvider>
      <Probe />
    </ThemeProvider>,
  );
  await user.click(screen.getByRole("button", { name: "dark" }));

  const maxUiRoot = screen.getByTestId("scheme").closest('[class*="MaxUI_colorScheme"]');
  expect(maxUiRoot).not.toBeNull();
  expect(maxUiRoot?.className).toMatch(/MaxUI_colorScheme_dark/);
});
