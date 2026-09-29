// Экран «Календарь телефона» — вынесен со строки «Настройки» экрана 19 на отдельный экран
// (docs/screens/should-12-13-19.md, решение человека 29.09, задача UI-EXPORT). Открывается строкой
// на экране 19, «Назад» ведёт обратно в профиль (обычный стек — App.tsx). Логика, состояния и
// события — перенесены без изменений из CalendarExport (был на экране «Настройки»), оформление —
// две секции: «Разово добавить сроки» (файл) и «Подписаться — обновляется само» (ссылка).
import { Button, Typography } from "@maxhub/max-ui";
import { useCallback, useEffect, useRef, useState } from "react";

import { copyLink, openCalendarFeed } from "../bridge";
import { errorKind, type ErrorKind } from "../data/http";
import type { DataSource } from "../data/source";
import { type Route } from "../router";
import { ErrorBanner } from "../shell/ErrorBanner";
import { useToast } from "../shell/Toast";
import { texts } from "../texts";
import type { IcsLink } from "../types";

export type IcsRoute = Extract<Route, { name: "ics" }>;

const quiet = (p: Promise<unknown>) => void p.catch(() => undefined);

/**
 * Маршруты, для которых уже отправлен ics_screen_opened: повторный эффект StrictMode событие
 * не дублирует (как settings_opened в SettingsScreen, item_card_opened в CardScreen).
 */
const openedRoutes = new WeakSet<IcsRoute>();

type IcsLoad =
  { kind: "loading" } | { kind: "ready"; link: IcsLink } | { kind: "failed"; error: ErrorKind };

/** Что пользователь нажал: открыть файл (разовая загрузка) или скопировать ссылку для подписки. */
type IcsAction = "open" | "copy";

interface Props {
  source: DataSource;
  route: IcsRoute;
}

/**
 * Лента .ics (GET /api/ics/link): «Добавить в календарь телефона» и «Скопировать ссылку для
 * подписки». Bridge открывает ссылку только по клику пользователя, поэтому ссылка запрашивается
 * заранее, при открытии экрана, а нажатие срабатывает сразу. Нажали раньше ответа — кнопка
 * «грузится», действие выполнится по ответу (если Bridge не пропустит переход без свежего клика,
 * второе нажатие сработает с готовой ссылкой). Ошибку заранее запрошенной ссылки показываем только
 * после нажатия — тогда же запрос повторяется. Открываем https-ссылку, не webcal:// — см.
 * openCalendarFeed. Событий нет — открывать нечего: тост вместо пустого файла; подписаться можно.
 */
export function IcsScreen({ source, route }: Props) {
  const toast = useToast();
  const [link, setLink] = useState<IcsLoad>({ kind: "loading" });
  const [attempt, setAttempt] = useState(0);
  const [waiting, setWaiting] = useState<IcsAction | null>(null);
  const [error, setError] = useState<ErrorKind | null>(null);
  // Какое действие привело к ошибке — плашка показывается у соответствующей секции/кнопки.
  const [errorAction, setErrorAction] = useState<IcsAction | null>(null);
  // Скопировать не получилось — ссылка показывается текстом, чтобы скопировать вручную.
  const [manual, setManual] = useState<string | null>(null);
  // Нажатие до ответа: читается в колбэке запроса, где состояние было бы устаревшим.
  const pendingRef = useRef<IcsAction | null>(null);

  useEffect(() => {
    if (openedRoutes.has(route)) return;
    openedRoutes.add(route);
    quiet(source.track("ics_screen_opened"));
  }, [source, route]);

  const perform = useCallback(
    (action: IcsAction, ready: IcsLink) => {
      if (action === "open") {
        if (ready.items === 0) {
          toast(texts.ics.empty);
          return;
        }
        openCalendarFeed(ready.url);
        return;
      }
      void copyLink(ready.url).then((ok) => {
        if (ok) {
          setManual(null);
          toast(texts.ics.copied);
        } else {
          setManual(ready.url);
        }
      });
    },
    [toast],
  );

  useEffect(() => {
    let alive = true;
    source.icsLink().then(
      (res) => {
        if (!alive) return;
        setLink({ kind: "ready", link: res });
        const action = pendingRef.current;
        if (action) {
          pendingRef.current = null;
          setWaiting(null);
          perform(action, res);
        }
      },
      (e: unknown) => {
        if (!alive) return;
        const kind = errorKind(e);
        setLink({ kind: "failed", error: kind });
        if (pendingRef.current) {
          const action = pendingRef.current;
          pendingRef.current = null;
          setWaiting(null);
          setError(kind);
          setErrorAction(action);
          quiet(source.track("error", { where: "ics", kind }));
        }
      },
    );
    return () => {
      alive = false;
    };
  }, [source, attempt, perform]);

  const press = (action: IcsAction) => {
    if (pendingRef.current) return;
    quiet(source.track("ics_link_requested", { action }));
    setError(null);
    setErrorAction(null);
    if (link.kind === "ready") {
      perform(action, link.link);
      return;
    }
    pendingRef.current = action;
    setWaiting(action);
    if (link.kind === "failed") {
      setLink({ kind: "loading" });
      setAttempt((n) => n + 1);
    }
  };
  const retry = () => press(errorAction ?? waiting ?? "open");

  return (
    <div className="screen">
      <Typography.Headline className="profile-head">{texts.ics.title}</Typography.Headline>

      <Typography.Title variant="medium" className="settings-section">
        {texts.ics.sectionOnce}
      </Typography.Title>
      <div className="settings-ics">
        {error && errorAction === "open" && (
          <ErrorBanner kind={error} onRetry={retry} retrying={waiting !== null} />
        )}
        <Button
          size="large"
          stretched
          variant="secondary"
          loading={waiting === "open"}
          onClick={() => press("open")}
        >
          {texts.ics.onceButton}
        </Button>
        <Typography.Label className="settings-ics-hint">{texts.ics.onceHint}</Typography.Label>
      </div>

      <Typography.Title variant="medium" className="settings-section">
        {texts.ics.sectionSubscribe} <span className="ics-badge">{texts.ics.recommended}</span>
      </Typography.Title>
      <div className="settings-ics">
        {error && errorAction === "copy" && (
          <ErrorBanner kind={error} onRetry={retry} retrying={waiting !== null} />
        )}
        <Button size="large" stretched loading={waiting === "copy"} onClick={() => press("copy")}>
          {texts.ics.subscribeButton}
        </Button>
        {manual && (
          <div className="settings-ics-manual">
            <Typography.Label>{texts.ics.copyFailed}</Typography.Label>
            <Typography.Body className="settings-ics-url">{manual}</Typography.Body>
          </div>
        )}
        <Typography.Label className="settings-ics-hint">{texts.ics.stepsIphone}</Typography.Label>
        <Typography.Label className="settings-ics-hint">{texts.ics.stepsAndroid}</Typography.Label>
      </div>
    </div>
  );
}
