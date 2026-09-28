// Оболочка мини-приложения: вход (экран 18 или календарь), роутер, BackButton, общие состояния.
import { Button, Panel, Typography } from "@maxhub/max-ui";
import { useCallback, useEffect, useMemo, useReducer, useRef, useState } from "react";

import { addDays, DEFAULT_TIMEZONE, LIST_WINDOW_DAYS, todayIn } from "./calendar";
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
  type StartTarget,
  type Tab,
  useBackButton,
} from "./router";
import { CardScreen } from "./screens/CardScreen";
import { GateScreen } from "./screens/GateScreen";
import { type CalendarState, ListScreen } from "./screens/ListScreen";
import { MonthScreen } from "./screens/MonthScreen";
import { ProfileScreen } from "./screens/ProfileScreen";
import { SettingsScreen } from "./screens/SettingsScreen";
import { ShareScreen } from "./screens/ShareScreen";
import { TaskFormScreen } from "./screens/TaskFormScreen";
import { ErrorBoundary } from "./shell/ErrorBoundary";
import { ToastProvider, useToast } from "./shell/Toast";
import {
  type CalendarStore,
  CalendarStoreContext,
  itemKey,
  removeItem,
  upsertItem,
  useMonths,
} from "./store";
import { texts } from "./texts";
import type { ItemCard, ItemType, Me, Profile, TaskDraft } from "./types";

type MeState =
  { kind: "loading" } | { kind: "failed"; error: ErrorKind } | { kind: "ready"; me: Me };

const quiet = (p: Promise<unknown>) => void p.catch(() => undefined);

export default function App({ source: injected }: { source?: DataSource } = {}) {
  const [source] = useState(() => injected ?? getDataSource());
  const [startParam] = useState(getStartParam);
  const [startTarget] = useState(() => parseStartParam(startParam));
  // Приглашение «Поделиться» (SHARE): экран поверх входа, пока получатель не ответил.
  // taskId — добавленная задача (null — «Не нужно» или ссылка недействительна).
  const [shareDone, setShareDone] = useState<{ taskId: number | null } | null>(null);
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
          // «Изменить» / «Выбрать дату» из старого сообщения бота: черновик не свой (#96).
          if (result.draft_stale) {
            quiet(source.track("task_draft_stale", { action: "edit" }));
          }
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

  // Экран 19 или 13 принёс свежий профиль (поменяли в боте, пересобрали, сохранили настройки):
  // шапка 14/15 и пояс.
  const updateProfile = useCallback(
    (next: Profile) =>
      setMe((m) =>
        m.kind === "ready"
          ? { kind: "ready", me: { ...m.me, has_profile: true, profile: next } }
          : m,
      ),
    [],
  );

  const profile = me.kind === "ready" && me.me.has_profile ? me.me.profile : null;

  // После «Добавить» с профилем — сразу карточка новой задачи поверх списка.
  const target: StartTarget =
    shareDone?.taskId != null
      ? { kind: "item", itemType: "task", id: shareDone.taskId, source: "share" }
      : startTarget;

  let content;
  if (startTarget?.kind === "share" && shareDone === null) {
    // Профиль не нужен: получатель может быть новым пользователем, /api/me грузится параллельно.
    content = (
      <ShareScreen
        source={source}
        code={startTarget.code}
        today={todayIn(profile?.timezone ?? DEFAULT_TIMEZONE)}
        onFinish={(task) => setShareDone({ taskId: task?.id ?? null })}
      />
    );
  } else if (profile) {
    const draft = me.kind === "ready" ? (me.me.draft ?? null) : null;
    const draftStale = me.kind === "ready" && me.me.draft_stale === true;
    content = (
      <CalendarApp
        source={source}
        profile={profile}
        target={target}
        draft={draft}
        draftStale={draftStale}
        onProfile={updateProfile}
      />
    );
  } else {
    // Нет профиля или 401 — заглушка без слов об авторизации; сеть/сервис — заглушка + плашка.
    const error = me.kind === "failed" && me.error !== "unauthorized" ? me.error : null;
    content = (
      <GateScreen
        loading={me.kind === "loading"}
        error={error}
        retrying={meRetrying}
        onRetry={retryMe}
        added={shareDone?.taskId != null}
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
  /** Куда открыть при входе: из start_param бота или задача, только что добавленная из приглашения. */
  target: StartTarget;
  /** Черновик задачи из бота для формы 17 (`start_param=task_draft[_<id>]`). */
  draft: TaskDraft | null;
  /** Черновик из старого сообщения бота: форма 17 пустая, при входе — тост (#96). */
  draftStale: boolean;
  /** Свежий профиль с экрана 19 или 13. Стабилен. */
  onProfile: (profile: Profile) => void;
}

/** Календарь пользователя с профилем: экраны 14/15/16/17/19/13. Данные общие для вкладок. */
function CalendarApp({ source, profile, target, draft, draftStale, onProfile }: CalendarAppProps) {
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

  // Секция «Дальше» на 14 развёрнута (29.09): держим здесь, чтобы пережить карточку 16 и «Назад».
  const [laterExpanded, setLaterExpanded] = useState(false);
  const expandLater = useCallback(
    (hidden: number) => {
      setLaterExpanded(true);
      quiet(source.track("list_expanded", { hidden }));
    },
    [source],
  );
  const collapseLater = useCallback(() => setLaterExpanded(false), []);

  // Месяцы сетки 15 — кеш на сессию; выбранный день переживает переход в карточку и «Назад».
  const months = useMonths(source);
  const { upsert: upsertMonth, remove: removeMonth, reset: resetMonths } = months;
  const [monthDay, setMonthDay] = useState<string | null>(null);

  // Карточки 16 за этот запуск; изменения из 16/17 сразу попадают в список 14 и сетку 15.
  const [cards, setCards] = useState<Record<string, ItemCard>>({});
  const upsert = useCallback(
    (card: ItemCard) => {
      setCards((c) => ({ ...c, [itemKey(card.type, card.id)]: card }));
      setCalendar((c) =>
        c.items ? { ...c, items: upsertItem(c.items, card, todayIn(timezone)) } : c,
      );
      upsertMonth(card);
    },
    [timezone, upsertMonth],
  );
  const remove = useCallback(
    (type: ItemType, id: number) => {
      setCards((c) => {
        const next = { ...c };
        delete next[itemKey(type, id)];
        return next;
      });
      setCalendar((c) => (c.items ? { ...c, items: removeItem(c.items, type, id) } : c));
      removeMonth(type, id);
    },
    [removeMonth],
  );
  // «Пересобрать» (экран 19): список грузится заново, кеши месяцев и карточек сбрасываются.
  const rebuilt = useCallback(() => {
    setCards({});
    resetMonths();
    setCalendar((c) => ({ ...c, loading: true }));
    setAttempt((n) => n + 1);
  }, [resetMonths]);
  // Настройки сохранены (экран 13). Сменился пояс — статусы в кеше месяцев и карточек посчитаны
  // бэкендом в старом поясе: сбрасываем. Список 14 перезагрузится сам (эффект зависит от пояса).
  const settingsSaved = useCallback(
    (next: Profile) => {
      if (next.timezone !== timezone) {
        setCards({});
        resetMonths();
        setCalendar((c) => ({ ...c, loading: true }));
      }
      onProfile(next);
    },
    [timezone, resetMonths, onProfile],
  );

  const store = useMemo<CalendarStore>(
    () => ({ items: calendar.items, cards, upsert, remove }),
    [calendar.items, cards, upsert, remove],
  );

  const [stack, dispatch] = useReducer(navReducer, null, () => initialStack(target, loadTab()));
  const back = useCallback(() => dispatch({ type: "back" }), []);
  const nav = useMemo<Navigation>(
    () => ({
      route: stack[stack.length - 1],
      depth: stack.length,
      push: (route) => dispatch({ type: "push", route }),
      back,
      switchTab: (tab: Tab) => {
        saveTab(tab);
        // Переключение на «Месяц»: по умолчанию выбран сегодняшний день (экран 15).
        if (tab === "month" && stack[stack.length - 1].name !== "month") {
          setMonthDay(null);
          quiet(source.track("month_opened"));
        }
        dispatch({ type: "tab", tab });
      },
      // На 14 без записи в localStorage: запомненный режим выбирает пользователь.
      home: () => dispatch({ type: "tab", tab: "list" }),
    }),
    [stack, back, source],
  );

  // Форма 17 открыта пустой вместо чужого черновика — объясняем почему, один раз за запуск (#96).
  const toast = useToast();
  const staleShown = useRef(false);
  useEffect(() => {
    if (!draftStale || staleShown.current) return;
    staleShown.current = true;
    toast(texts.form.draftStale);
  }, [draftStale, toast]);

  const [backButton] = useState(getBackButton);
  useBackButton(backButton, stack.length > 1, back);

  const route = nav.route;
  let screen;
  switch (route.name) {
    case "list":
      screen = (
        <ListScreen
          today={todayIn(timezone)}
          calendar={calendar}
          onRetry={retryCalendar}
          laterExpanded={laterExpanded}
          onExpandLater={expandLater}
          onCollapseLater={collapseLater}
        />
      );
      break;
    case "month":
      screen = (
        <MonthScreen
          today={todayIn(timezone)}
          months={months.cache}
          selected={monthDay}
          onSelect={setMonthDay}
          onLoad={months.load}
          onRetry={months.retry}
        />
      );
      break;
    case "card":
      screen = (
        <CardScreen
          key={itemKey(route.itemType, route.id)}
          source={source}
          route={route}
          today={todayIn(timezone)}
          timezone={timezone}
        />
      );
      break;
    case "profile":
      screen = (
        <ProfileScreen
          source={source}
          initial={profile}
          onProfile={onProfile}
          onRebuilt={rebuilt}
          onSettings={() => nav.push({ name: "settings", source: "profile" })}
        />
      );
      break;
    case "settings":
      screen = (
        <SettingsScreen source={source} route={route} profile={profile} onSaved={settingsSaved} />
      );
      break;
    case "task":
      screen = (
        <TaskFormScreen
          source={source}
          today={todayIn(timezone)}
          draft={route.draft ? draft : null}
          taskId={route.taskId}
          initialDate={route.date}
          onCreatedFromMonth={
            route.from === "month"
              ? (dueDate: string) => {
                  // Возврат на «Месяц» с выбранным днём новой задачи, без записи в localStorage
                  // и без month_opened — пользователь уже был на этой вкладке (#93).
                  setMonthDay(dueDate);
                  dispatch({ type: "tab", tab: "month" });
                }
              : undefined
          }
        />
      );
      break;
  }

  return (
    <NavigationContext.Provider value={nav}>
      <CalendarStoreContext.Provider value={store}>
        {/* Вне MAX BackButton нет — замена, чтобы с неглавного экрана был выход. */}
        {!backButton && stack.length > 1 && (
          <div className="back-fallback">
            <Button size="small" variant="ghost" onClick={back}>
              {texts.nav.back}
            </Button>
          </div>
        )}
        {screen}
      </CalendarStoreContext.Provider>
    </NavigationContext.Provider>
  );
}
