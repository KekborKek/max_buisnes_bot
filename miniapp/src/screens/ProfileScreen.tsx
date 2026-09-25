// Экран 19. Профиль и «Пересобрать» (docs/screens/should-12-13-19.md).
// Профиль при открытии запрашивается заново: его могли поменять в боте («Изменить»).
// calendar_built пишет бэкенд в POST /api/calendar/rebuild; открытие экрана события не шлёт.
import { Button, CellList, CellSimple, Typography } from "@maxhub/max-ui";
import { useEffect, useState } from "react";

import { botStartUrl, getBotUrl, openBotChat } from "../bridge";
import { formatNumericDate } from "../calendar";
import { ApiError, errorKind, type ErrorKind } from "../data/http";
import type { DataSource } from "../data/source";
import { ErrorBanner } from "../shell/ErrorBanner";
import { Skeleton } from "../shell/Skeleton";
import { useTheme, type ThemePref } from "../shell/Theme";
import { useToast } from "../shell/Toast";
import { texts } from "../texts";
import type { Profile, RebuildResult } from "../types";
import { GateScreen } from "./GateScreen";

/** Параметр запуска бота для «Изменить»: бот задаёт вопросы экрана 2 (bot/handlers/start.py). */
export const EDIT_START_PAYLOAD = "profile_edit";

type Load =
  | { kind: "loading" }
  | { kind: "ready"; profile: Profile }
  | { kind: "empty" }
  | { kind: "failed"; error: ErrorKind };

type RebuildError = ErrorKind | "incomplete";

const quiet = (p: Promise<unknown>) => void p.catch(() => undefined);

interface Props {
  source: DataSource;
  /** Профиль из /api/me при запуске: остаётся на экране, если обновить не получилось. */
  initial: Profile;
  /** Свежий профиль (открыли экран или пересобрали) — шапке 14/15 и поясу оболочки. Стабилен. */
  onProfile: (profile: Profile) => void;
  /** Календарь пересобран — оболочка перезагружает список и сбрасывает кеши. */
  onRebuilt: (result: RebuildResult) => void;
}

function ProfileRows({ profile }: { profile: Profile }) {
  const checked = profile.reference_checked_at;
  return (
    <CellList mode="island">
      <CellSimple
        title={texts.profile.income}
        subtitle={texts.income[profile.income_band] ?? texts.income.unknown}
      />
      <CellSimple
        title={texts.profile.regime}
        subtitle={texts.regime[profile.regime] ?? texts.regime.unknown}
      />
      <CellSimple
        title={texts.profile.employees}
        subtitle={profile.has_employees ? texts.employees.yes : texts.employees.no}
      />
      <CellSimple
        title={texts.profile.timezone}
        subtitle={texts.timezone[profile.timezone] ?? profile.timezone}
      />
      {checked && (
        <CellSimple
          title={texts.profile.reference}
          subtitle={texts.card.checked(formatNumericDate(checked))}
        />
      )}
    </CellList>
  );
}

/** Выбор темы (FRONT-20, #83): «Как в системе» / «Светлая» / «Тёмная» — сегмент по D8/D23
 * (Button size="small", активная — primary, aria-pressed), как «Список | Месяц» в CalendarHeader.
 * Хранится на устройстве (shell/Theme.tsx), от профиля и сети не зависит. */
function ThemeControl({ source }: { source: DataSource }) {
  const { pref, setPref } = useTheme();
  const options: [ThemePref, string][] = [
    ["system", texts.profile.themeSystem],
    ["light", texts.profile.themeLight],
    ["dark", texts.profile.themeDark],
  ];
  const choose = (value: ThemePref) => {
    if (value === pref) return;
    setPref(value);
    quiet(source.track("theme_changed", { theme: value }));
  };
  return (
    <div className="profile-theme">
      <Typography.Label>{texts.profile.theme}</Typography.Label>
      <div className="tabs" role="group" aria-label={texts.profile.theme}>
        {options.map(([value, label]) => (
          <Button
            key={value}
            size="small"
            variant={pref === value ? "primary" : "secondary"}
            aria-pressed={pref === value}
            onClick={() => choose(value)}
          >
            {label}
          </Button>
        ))}
      </div>
    </div>
  );
}

export function ProfileScreen({ source, initial, onProfile, onRebuilt }: Props) {
  const toast = useToast();
  const [load, setLoad] = useState<Load>({ kind: "loading" });
  const [attempt, setAttempt] = useState(0);
  const [retrying, setRetrying] = useState(false);
  const [rebuilding, setRebuilding] = useState(false);
  const [rebuildError, setRebuildError] = useState<RebuildError | null>(null);

  useEffect(() => {
    let alive = true;
    source.me(null).then(
      (me) => {
        if (!alive) return;
        setRetrying(false);
        if (me.has_profile && me.profile) {
          setLoad({ kind: "ready", profile: me.profile });
          onProfile(me.profile);
        } else {
          setLoad({ kind: "empty" });
        }
      },
      (e: unknown) => {
        if (!alive) return;
        const kind = errorKind(e);
        setRetrying(false);
        setLoad({ kind: "failed", error: kind });
        quiet(source.track("error", { where: "profile", kind }));
      },
    );
    return () => {
      alive = false;
    };
  }, [source, attempt, onProfile]);

  const retryLoad = () => {
    setRetrying(true);
    setAttempt((n) => n + 1);
  };

  const rebuild = () => {
    if (rebuilding) return;
    setRebuilding(true);
    setRebuildError(null);
    source.rebuild().then(
      (result) => {
        setRebuilding(false);
        setLoad({ kind: "ready", profile: result.profile });
        onProfile(result.profile);
        onRebuilt(result);
        toast(texts.profile.rebuilt);
      },
      (e: unknown) => {
        setRebuilding(false);
        const kind = errorKind(e);
        const incomplete = e instanceof ApiError && e.status === 409;
        setRebuildError(incomplete ? "incomplete" : kind);
        quiet(source.track("error", { where: "rebuild", kind: incomplete ? "incomplete" : kind }));
      },
    );
  };

  if (load.kind === "empty") {
    // Профиля больше нет — как экран 18: путь в бота одним нажатием.
    return <GateScreen loading={false} error={null} onRetry={retryLoad} />;
  }

  const profile = load.kind === "ready" ? load.profile : load.kind === "failed" ? initial : null;
  const botUrl = getBotUrl();

  return (
    <div className="screen">
      <Typography.Headline className="profile-head">{texts.profile.title}</Typography.Headline>
      {load.kind === "failed" && (
        <ErrorBanner kind={load.error} onRetry={retryLoad} retrying={retrying} />
      )}
      {rebuildError === "incomplete" && (
        <div className="error-banner" role="alert">
          <Typography.Body>{texts.profile.incomplete}</Typography.Body>
        </div>
      )}
      {rebuildError && rebuildError !== "incomplete" && (
        <ErrorBanner kind={rebuildError} onRetry={rebuild} retrying={rebuilding} />
      )}
      {profile ? <ProfileRows profile={profile} /> : <Skeleton />}
      <ThemeControl source={source} />
      <Typography.Label className="profile-note">{texts.profile.disclaimer}</Typography.Label>
      {profile && (
        <div className="profile-actions">
          {/* Адреса бота нет (VITE_BOT_URL пуст) — «Изменить» не показываем, как на экране 18. */}
          {botUrl && (
            <Button
              size="large"
              stretched
              variant="secondary"
              onClick={() => openBotChat(botStartUrl(botUrl, EDIT_START_PAYLOAD))}
            >
              {texts.profile.edit}
            </Button>
          )}
          <Button size="large" stretched loading={rebuilding} onClick={rebuild}>
            {texts.profile.rebuild}
          </Button>
        </div>
      )}
    </div>
  );
}
