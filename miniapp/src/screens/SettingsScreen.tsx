// Экран 13. Настройки (docs/screens/should-12-13-19.md, #86; решение человека 29.09 — две секции).
// Напоминания: за 30 и 7 дней (за 1 день — всегда), час, часовой пояс, сводка по понедельникам;
// сохранение — PUT /api/profile/settings, событие reminder_settings_changed пишет бэкенд, здесь —
// settings_opened и error. Тема оформления (theme_changed) — перенесена с экрана 19, работает
// независимо от «Сохранить». Календарь телефона (.ics) вынесен с 29.09 на отдельный экран
// (IcsScreen.tsx, задача UI-EXPORT) — здесь его больше нет.
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
import { type ThemePref, useTheme } from "../shell/Theme";
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

/** Выбор темы (FRONT-20, #83; с экрана 19 перенесён сюда 29.09): «Как в системе» / «Светлая» /
 * «Тёмная» — сегмент по D8/D23 (Button size="small", активная — primary, aria-pressed), как
 * «Список | Месяц» в CalendarHeader. Хранится на устройстве (shell/Theme.tsx, `profile.theme`),
 * от профиля и сети не зависит. */
function ThemeControl({ source }: { source: DataSource }) {
  const { pref, setPref } = useTheme();
  const options: [ThemePref, string][] = [
    ["system", texts.settings.themeSystem],
    ["light", texts.settings.themeLight],
    ["dark", texts.settings.themeDark],
  ];
  const choose = (value: ThemePref) => {
    if (value === pref) return;
    setPref(value);
    quiet(source.track("theme_changed", { theme: value }));
  };
  return (
    <div className="settings-theme">
      <div className="tabs" role="group" aria-label={texts.settings.theme}>
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
  // Список поясов длинный — свёрнут по умолчанию, виден только выбранный (задача UI-TZ-COLLAPSE).
  // Выбор из развёрнутого списка список не сворачивает обратно — так проще.
  const [zonesExpanded, setZonesExpanded] = useState(false);
  // Защита от второго запроса, пока первый в пути, — не дожидаясь перерисовки кнопки.
  const inFlight = useRef(false);
  // Ушли с экрана («Назад») до ответа — профиль в оболочку всё равно, а «Назад» и тост — нет:
  // иначе второй «Назад» увёл бы с того экрана, куда человек уже вернулся сам.
  const mounted = useRef(true);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  useEffect(() => {
    if (openedRoutes.has(route)) return;
    openedRoutes.add(route);
    quiet(source.track("settings_opened", { source: route.source }));
  }, [source, route]);

  const dirty = !sameSettings(values, initial);
  // Пояс не из списка экрана 2 бэкенд не примет (422) — сначала выбрать из списка.
  const zoneKnown = Object.keys(texts.timezone).includes(values.timezone);
  const canSave = dirty && zoneKnown;
  // Любая правка снимает плашку прошлой ошибки: «Повторить» относился к другим значениям.
  const set = (patch: Partial<ReminderSettingsInput>) => {
    setError(null);
    setValues((v) => ({ ...v, ...patch }));
  };

  const save = () => {
    if (!canSave || inFlight.current) return;
    inFlight.current = true;
    setSaving(true);
    setError(null);
    source.saveSettings(values).then(
      (next) => {
        inFlight.current = false;
        onSaved(next);
        if (!mounted.current) return;
        nav.back();
        toast(texts.settings.saved);
      },
      (e: unknown) => {
        // Форма не сбрасывается: выбранное остаётся на месте, «Сохранить» можно нажать снова.
        inFlight.current = false;
        const incomplete = e instanceof ApiError && e.status === 409;
        const kind = incomplete ? "incomplete" : errorKind(e);
        quiet(source.track("error", { where: "settings", kind }));
        if (!mounted.current) return;
        setSaving(false);
        setError(kind);
      },
    );
  };

  // Пояс вне списка экрана 2 (через онбординг не получить) показываем идентификатором, как на 19;
  // сохранить с ним нельзя — под списком подсказка выбрать пояс из списка.
  const zones = Object.keys(texts.timezone);
  if (!zones.includes(initial.timezone)) zones.push(initial.timezone);

  return (
    <div className="screen">
      <Typography.Headline className="form-title">{texts.settings.title}</Typography.Headline>

      <Typography.Title variant="medium" className="settings-section">
        {texts.settings.sectionReminders}
      </Typography.Title>

      {/* Во время запроса переключатели не `disabled`, а aria-disabled + игнор onChange —
          по той же причине, что «За 1 день»: disabled в тёмной теме читается как «выкл». */}
      <CellList mode="island" header={<CellHeader>{texts.settings.days}</CellHeader>}>
        <CellSimple
          as="label"
          title={texts.settings.d30}
          after={
            <Switch
              checked={values.d30}
              aria-disabled={saving || undefined}
              onChange={(e) => {
                if (!saving) set({ d30: e.target.checked });
              }}
            />
          }
        />
        <CellSimple
          as="label"
          title={texts.settings.d7}
          after={
            <Switch
              checked={values.d7}
              aria-disabled={saving || undefined}
              onChange={(e) => {
                if (!saving) set({ d7: e.target.checked });
              }}
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

      <div className="list-section">
        <CellList
          mode="island"
          role="radiogroup"
          aria-label={texts.settings.timezone}
          aria-describedby={zoneKnown ? undefined : "settings-timezone-hint"}
          header={<CellHeader>{texts.settings.timezone}</CellHeader>}
        >
          {/* Свёрнуто — видна только текущая строка (подпись как в развёрнутом списке); полный
              список — по кнопке «Показать все пояса» ниже. */}
          {(zonesExpanded ? zones : [values.timezone]).map((zone) => (
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
        <div className="list-more">
          <Button
            size="medium"
            variant="secondary"
            stretched
            aria-expanded={zonesExpanded}
            onClick={() => setZonesExpanded((v) => !v)}
          >
            {zonesExpanded ? texts.settings.timezoneCollapse : texts.settings.timezoneShowAll}
          </Button>
        </div>
      </div>
      {!zoneKnown && (
        <Typography.Label id="settings-timezone-hint" className="field-error">
          {texts.settings.timezoneUnknown}
        </Typography.Label>
      )}

      <CellList mode="island">
        <CellSimple
          as="label"
          title={texts.settings.digest}
          subtitle={texts.settings.digestNote}
          after={
            <Switch
              checked={values.digest}
              aria-disabled={saving || undefined}
              onChange={(e) => {
                if (!saving) set({ digest: e.target.checked });
              }}
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
        <Button size="large" stretched loading={saving} disabled={!canSave} onClick={save}>
          {texts.settings.save}
        </Button>
      </div>

      <Typography.Title variant="medium" className="settings-section">
        {texts.settings.theme}
      </Typography.Title>
      <ThemeControl source={source} />
    </div>
  );
}
