// Экран 13. Настройки напоминаний (docs/screens/should-12-13-19.md, #86): за 30 и 7 дней
// (за 1 день — всегда), час, часовой пояс, сводка по понедельникам. Сохранение —
// PUT /api/profile/settings; событие reminder_settings_changed пишет бэкенд, здесь — только
// settings_opened и error.
import {
  Button,
  CellHeader,
  CellList,
  CellSimple,
  Radio,
  Switch,
  Typography,
} from "@maxhub/max-ui";
import { useEffect, useRef, useState } from "react";

import { ApiError, errorKind, type ErrorKind } from "../data/http";
import type { DataSource } from "../data/source";
import { type Route, useNavigation } from "../router";
import { ErrorBanner } from "../shell/ErrorBanner";
import { useToast } from "../shell/Toast";
import { texts } from "../texts";
import {
  type Profile,
  REMINDER_DEFAULTS,
  REMINDER_HOURS,
  type ReminderSettingsInput,
} from "../types";

export type SettingsRoute = Extract<Route, { name: "settings" }>;

type SaveError = ErrorKind | "incomplete";

const quiet = (p: Promise<unknown>) => void p.catch(() => undefined);

/**
 * Маршруты, для которых уже отправлен settings_opened: повторный эффект StrictMode
 * событие не дублирует (как item_card_opened в CardScreen).
 */
const openedRoutes = new WeakSet<SettingsRoute>();

/** Значения формы из профиля. Нет `reminders` (бэкенд до #85) — умолчания бэкенда. */
export function settingsOf(profile: Profile): ReminderSettingsInput {
  return { ...REMINDER_DEFAULTS, ...profile.reminders, timezone: profile.timezone };
}

function sameSettings(a: ReminderSettingsInput, b: ReminderSettingsInput): boolean {
  return (
    a.d30 === b.d30 &&
    a.d7 === b.d7 &&
    a.hour === b.hour &&
    a.digest === b.digest &&
    a.timezone === b.timezone
  );
}

interface Props {
  source: DataSource;
  route: SettingsRoute;
  /** Профиль оболочки — начальные значения формы. */
  profile: Profile;
  /** Бэкенд вернул обновлённый профиль — в общее состояние приложения. */
  onSaved: (profile: Profile) => void;
}

export function SettingsScreen({ source, route, profile, onSaved }: Props) {
  const nav = useNavigation();
  const toast = useToast();
  const [initial] = useState(() => settingsOf(profile));
  const [values, setValues] = useState(initial);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<SaveError | null>(null);
  // Защита от второго запроса, пока первый в пути, — не дожидаясь перерисовки кнопки.
  const inFlight = useRef(false);

  useEffect(() => {
    if (openedRoutes.has(route)) return;
    openedRoutes.add(route);
    quiet(source.track("settings_opened", { source: route.source }));
  }, [source, route]);

  const dirty = !sameSettings(values, initial);
  const set = (patch: Partial<ReminderSettingsInput>) => setValues((v) => ({ ...v, ...patch }));

  const save = () => {
    if (!dirty || inFlight.current) return;
    inFlight.current = true;
    setSaving(true);
    setError(null);
    source.saveSettings(values).then(
      (next) => {
        inFlight.current = false;
        onSaved(next);
        nav.back();
        toast(texts.settings.saved);
      },
      (e: unknown) => {
        // Форма не сбрасывается: выбранное остаётся на месте, «Сохранить» можно нажать снова.
        inFlight.current = false;
        setSaving(false);
        const incomplete = e instanceof ApiError && e.status === 409;
        const kind = incomplete ? "incomplete" : errorKind(e);
        setError(kind);
        quiet(source.track("error", { where: "settings", kind }));
      },
    );
  };

  // Пояс вне списка экрана 2 (через онбординг не получить) показываем идентификатором, как на 19.
  const zones = Object.keys(texts.timezone);
  if (!zones.includes(initial.timezone)) zones.push(initial.timezone);

  return (
    <div className="screen">
      <Typography.Headline className="form-title">{texts.settings.title}</Typography.Headline>

      <CellList mode="island" header={<CellHeader>{texts.settings.days}</CellHeader>}>
        <CellSimple
          as="label"
          title={texts.settings.d30}
          after={
            <Switch
              checked={values.d30}
              disabled={saving}
              onChange={(e) => set({ d30: e.target.checked })}
            />
          }
        />
        <CellSimple
          as="label"
          title={texts.settings.d7}
          after={
            <Switch
              checked={values.d7}
              disabled={saving}
              onChange={(e) => set({ d7: e.target.checked })}
            />
          }
        />
        {/* За 1 день — последняя защита от пропуска: всегда включено, в тело запроса не входит.
            Не `disabled`: в тёмной теме MAX UI красит выключенный-но-включённый Switch серым,
            и он читается как «выкл». Поэтому aria-disabled, контролируемое checked без изменения
            (React возвращает значение после change; preventDefault на клике контролируемого
            чекбокса, наоборот, рассинхронизирует его) и приглушение в CSS. */}
        <CellSimple
          as="label"
          title={texts.settings.d1}
          subtitle={texts.settings.d1Note}
          innerClassNames={{ subtitle: "card-wrap" }}
          after={
            <Switch
              className="switch-locked"
              checked
              aria-disabled="true"
              onChange={() => undefined}
            />
          }
        />
      </CellList>

      <CellList mode="island" header={<CellHeader>{texts.settings.hour}</CellHeader>}>
        <div className="form-field tabs" role="group" aria-label={texts.settings.hour}>
          {REMINDER_HOURS.map((hour) => (
            <Button
              key={hour}
              size="small"
              variant={values.hour === hour ? "primary" : "secondary"}
              aria-pressed={values.hour === hour}
              disabled={saving}
              onClick={() => set({ hour })}
            >
              {texts.settings.hourOption(hour)}
            </Button>
          ))}
        </div>
      </CellList>

      <CellList
        mode="island"
        role="radiogroup"
        aria-label={texts.settings.timezone}
        header={<CellHeader>{texts.settings.timezone}</CellHeader>}
      >
        {zones.map((zone) => (
          <CellSimple
            key={zone}
            as="label"
            before={
              <Radio
                name="settings-timezone"
                checked={values.timezone === zone}
                disabled={saving}
                onChange={() => set({ timezone: zone })}
              />
            }
            title={texts.timezone[zone] ?? zone}
          />
        ))}
      </CellList>

      <CellList mode="island">
        <CellSimple
          as="label"
          title={texts.settings.digest}
          subtitle={texts.settings.digestNote}
          after={
            <Switch
              checked={values.digest}
              disabled={saving}
              onChange={(e) => set({ digest: e.target.checked })}
            />
          }
        />
      </CellList>

      {/* Плашка — у кнопки, а не в шапке: экран длинный, «Сохранить» нажимают внизу. */}
      {error === "incomplete" && (
        <div className="error-banner" role="alert">
          <Typography.Body>{texts.profile.incomplete}</Typography.Body>
        </div>
      )}
      {error && error !== "incomplete" && (
        <ErrorBanner kind={error} onRetry={save} retrying={saving} />
      )}
      <div className="card-actions">
        <Button size="large" stretched loading={saving} disabled={!dirty} onClick={save}>
          {texts.settings.save}
        </Button>
      </div>
    </div>
  );
}
