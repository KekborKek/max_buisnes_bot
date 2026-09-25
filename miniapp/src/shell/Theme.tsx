// Тема оформления (FRONT-20): MAX не передаёт тему мини-приложению (нет ни свойства, ни события
// в window.WebApp/max-web-app.js), поэтому решаем сами. По умолчанию — как в системе, плюс ручной
// выбор на экране 19. Хранение — только localStorage (WebApp.DeviceStorage асинхронный и в веб-версии
// MAX не поддерживается: не годится для синхронного чтения до первого рендера).
import { MaxUI, useSystemColorScheme, type ColorSchemeType } from "@maxhub/max-ui";
import { createContext, type ReactNode, useContext, useEffect, useState } from "react";

export type ThemePref = "system" | "light" | "dark";

const THEME_KEY = "profile.theme";

function isThemePref(value: string | null): value is ThemePref {
  return value === "system" || value === "light" || value === "dark";
}

/** Как loadTab/saveTab в router.tsx: недоступный localStorage (приватный режим, запрет в WebView)
 * не должен ронять приложение — просто выбор не запомнится, останется «как в системе». */
export function readThemePref(): ThemePref {
  try {
    const raw = localStorage.getItem(THEME_KEY);
    return isThemePref(raw) ? raw : "system";
  } catch {
    return "system";
  }
}

export function writeThemePref(pref: ThemePref): void {
  try {
    localStorage.setItem(THEME_KEY, pref);
  } catch {
    // недоступен — выбор просто не переживёт перезапуск.
  }
}

/** index.html красит html/body синхронно до первого рендера (та же логика, продублирована
 * намеренно — инлайн-скрипт выполняется раньше любого бандла). Дальше это поле держит провайдер,
 * чтобы data-theme и color-scheme совпадали с реальной темой и после переключения, и после смены
 * системной. color-scheme нужен отдельно от MaxUI: сама MAX UI 0.5.0 его не выставляет, а без него
 * нативные контролы (`<input type="date">`, скроллбары) остаются в старой цветовой схеме. */
function applyDataTheme(scheme: ColorSchemeType): void {
  document.documentElement.dataset.theme = scheme;
  document.documentElement.style.colorScheme = scheme;
}

interface ThemeValue {
  pref: ThemePref;
  scheme: ColorSchemeType;
  setPref: (pref: ThemePref) => void;
}

const ThemeContext = createContext<ThemeValue>({
  pref: "system",
  scheme: "light",
  setPref: () => undefined,
});

export function useTheme(): ThemeValue {
  return useContext(ThemeContext);
}

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [pref, setPrefState] = useState<ThemePref>(readThemePref);
  // listenChanges: true — «Как в системе» следит за matchMedia("change"), а не только за первым рендером.
  const system = useSystemColorScheme({ listenChanges: true });
  const scheme: ColorSchemeType = pref === "system" ? system : pref;

  useEffect(() => {
    applyDataTheme(scheme);
  }, [scheme]);

  const setPref = (next: ThemePref) => {
    setPrefState(next);
    writeThemePref(next);
  };

  return (
    <ThemeContext.Provider value={{ pref, scheme, setPref }}>
      <MaxUI colorScheme={scheme}>{children}</MaxUI>
    </ThemeContext.Provider>
  );
}
