// Оболочка мини-приложения: вход (экран 18 или календарь), роутер, BackButton, общие состояния.
import { Button, Panel, Typography } from "@maxhub/max-ui";
import { useCallback, useEffect, useMemo, useReducer, useRef, useState } from "react";

import { addDays, LIST_WINDOW_DAYS, todayIn } from "./calendar";
import { getBackButton, getStartParam } from "./bridge";
import { errorKind, type ErrorKind } from "./data/http";
import { type DataSource, getDataSource } from "./data/source";
import {
  initialStack,
  loadTab,
  type Navigation,
  NavigationContext,
  navReducer,
  parseStartParam,
  saveTab,
  type Tab,
  useBackButton,
} from "./router";
import { GateScreen } from "./screens/GateScreen";
import { CalendarHeader, type CalendarState, ListScreen } from "./screens/ListScreen";
import { PlaceholderScreen } from "./screens/PlaceholderScreen";
import { ErrorBoundary } from "./shell/ErrorBoundary";
import { ToastProvider } from "./shell/Toast";
import { texts } from "./texts";
import type { Me, Profile } from "./types";

type MeState =
  { kind: "loading" } | { kind: "failed"; error: ErrorKind } | { kind: "ready"; me: Me };

const quiet = (p: Promise<unknown>) => void p.catch(() => undefined);

export default function App({ source: injected }: { source?: DataSource } = {}) {
  const [source] = useState(() => injected ?? getDataSource());
  const [startParam] = useState(getStartParam);
  const [me, setMe] = useState<MeState>({ kind: "loading" });
  const [meRetrying, setMeRetrying] = useState(false);
  const openedTracked = useRef(false);

  // Повторить = новая попытка: эффект перезапускается, setState — только в колбэках промиса.
  const [meAttempt, setMeAttempt] = useState(0);

  useEffect(() => {
    let alive = true;
    source.me(startParam).then(
      (result) => {
        if (!alive) return;
        setMe({ kind: "ready", me: result });
        setMeRetrying(false);
        if (!openedTracked.current) {
          openedTracked.current = true;
          // start_param бэкенд перезаписывает значением из подписанной initData.
          quiet(
            source.track("miniapp_opened", {
              start_param: startParam,
              has_profile: result.has_profile,
            }),
          );
        }
      },
      (e: unknown) => {
        if (!alive) return;
        const kind = errorKind(e);
        setMe({ kind: "failed", error: kind });
        setMeRetrying(false);
        if (kind !== "unauthorized") quiet(source.track("error", { where: "me", kind }));
      },
    );
    return () => {
      alive = false;
    };
  }, [source, startParam, meAttempt]);

  const retryMe = () => {
    setMeRetrying(true);
    setMeAttempt((n) => n + 1);
  };

  const profile = me.kind === "ready" && me.me.has_profile ? me.me.profile : null;

  let content;
  if (profile) {
    content = <CalendarApp source={source} profile={profile} startParam={startParam} />;
  } else {
    // Нет профиля или 401 — заглушка без слов об авторизации; сеть/сервис — заглушка + плашка.
    const error = me.kind === "failed" && me.error !== "unauthorized" ? me.error : null;
    content = (
      <GateScreen
        loading={me.kind === "loading"}
        error={error}
        retrying={meRetrying}
        onRetry={retryMe}
      />
    );
  }

  return (
    <ToastProvider>
      <Panel mode="secondary" className="app">
        {source.isMock && (
          <Typography.Label className="mock-badge">{texts.mockBadge}</Typography.Label>
        )}
        <ErrorBoundary
          onError={() => quiet(source.track("error", { where: "render", kind: "crash" }))}
        >
          {content}
        </ErrorBoundary>
      </Panel>
    </ToastProvider>
  );
}

interface CalendarAppProps {
  source: DataSource;
  profile: Profile;
  startParam: string | null;
}

/** Календарь пользователя с профилем: экраны 14/15/16/17. Данные общие для вкладок. */
function CalendarApp({ source, profile, startParam }: CalendarAppProps) {
  const [calendar, setCalendar] = useState<CalendarState>({
    items: null,
    loading: true,
    error: null,
  });
  const [attempt, setAttempt] = useState(0);
  const timezone = profile.timezone;

  useEffect(() => {
    let alive = true;
    const today = todayIn(timezone);
    source.calendar(today, addDays(today, LIST_WINDOW_DAYS)).then(
      (items) => {
        if (alive) setCalendar({ items, loading: false, error: null });
      },
      (e: unknown) => {
        if (!alive) return;
        const kind = errorKind(e);
        // Уже загруженный список остаётся на экране под плашкой.
        setCalendar((c) => ({ ...c, loading: false, error: kind }));
        quiet(source.track("error", { where: "calendar", kind }));
      },
    );
    return () => {
      alive = false;
    };
  }, [source, timezone, attempt]);

  const retryCalendar = () => {
    setCalendar((c) => ({ ...c, loading: true }));
    setAttempt((n) => n + 1);
  };

  const [stack, dispatch] = useReducer(navReducer, null, () =>
    initialStack(parseStartParam(startParam), loadTab()),
  );
  const back = useCallback(() => dispatch({ type: "back" }), []);
  const nav = useMemo<Navigation>(
    () => ({
      route: stack[stack.length - 1],
      depth: stack.length,
      push: (route) => dispatch({ type: "push", route }),
      back,
      switchTab: (tab: Tab) => {
        saveTab(tab);
        dispatch({ type: "tab", tab });
      },
    }),
    [stack, back],
  );

  const [backButton] = useState(getBackButton);
  useBackButton(backButton, stack.length > 1, back);

  const route = nav.route;
  let screen;
  switch (route.name) {
    case "list":
      screen = (
        <ListScreen
          profile={profile}
          today={todayIn(timezone)}
          calendar={calendar}
          onRetry={retryCalendar}
        />
      );
      break;
    case "month":
      screen = (
        <div className="screen">
          <CalendarHeader profile={profile} tab="month" />
          <PlaceholderScreen screen="15" />
        </div>
      );
      break;
    case "card":
      screen = <PlaceholderScreen screen="16" />;
      break;
    case "task":
      screen = <PlaceholderScreen screen="17" />;
      break;
  }

  return (
    <NavigationContext.Provider value={nav}>
      {/* Вне MAX BackButton нет — замена, чтобы с неглавного экрана был выход. */}
      {!backButton && stack.length > 1 && (
        <div className="back-fallback">
          <Button size="small" variant="ghost" onClick={back}>
            {texts.nav.back}
          </Button>
        </div>
      )}
      {screen}
    </NavigationContext.Provider>
  );
}
