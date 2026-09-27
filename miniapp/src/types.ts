// Типы данных мини-приложения. Источник — docs/spec/data-model.md §2–3 и openapi.yaml
// (backend/app/api/schemas.py) — при расхождении правы они, не этот файл.

export type ItemType = "obligation" | "task";
export type Category = "taxes" | "contributions" | "reports" | "custom";
/** Статус считает бэкенд в часовом поясе пользователя. Мини-апп его не пересчитывает. */
export type ItemStatus = "done" | "overdue" | "today" | "upcoming";

/** DTO события календаря (обязательство или своя задача). Не путать с `Event` — это аналитика. */
export interface CalendarItem {
  type: ItemType;
  id: number;
  title: string;
  category: Category;
  /** YYYY-MM-DD, после переноса на рабочий день. */
  due_date: string;
  /** YYYY-MM-DD, дата по правилу до переноса. */
  original_date: string;
  status: ItemStatus;
  done_at: string | null;
}

export type IncomeBand = "lt10" | "10_20" | "20_60" | "gt60" | "unknown";
export type Regime = "usn6" | "usn15" | "patent" | "ausn" | "unknown";

/** Профиль из data-model.md §2 (`ProfileOut` в backend/app/api/schemas.py). */
export interface Profile {
  income_band: IncomeBand;
  regime: Regime;
  has_employees: boolean;
  /** IANA, например "Europe/Moscow". */
  timezone: string;
  /** null — нельзя определить (D25): «не знаю» или диапазон дохода пересекает порог. */
  nds_payer: boolean | null;
  calendar_built_at: string | null;
  /** YYYY-MM-DD — дата сверки справочника (экран 19); null — справочник недоступен. */
  reference_checked_at?: string | null;
  /** Настройки напоминаний (экран 13, #85). Нет поля (бэкенд до #85) — умолчания REMINDER_DEFAULTS. */
  reminders?: ReminderSettings;
}

/** Час напоминаний об обязательствах (экран 13). */
export type ReminderHour = 9 | 10 | 18;
export const REMINDER_HOURS: readonly ReminderHour[] = [9, 10, 18];

/** `Profile.reminders` (контракт #85). За 1 день напоминание есть всегда — в контракт не входит. */
export interface ReminderSettings {
  d30: boolean;
  d7: boolean;
  hour: ReminderHour;
  /** Сводка по понедельникам (экран 12). */
  digest: boolean;
}

/** Умолчания бэкенда (`reminder_settings()` + `digest_enabled()`). */
export const REMINDER_DEFAULTS: ReminderSettings = { d30: true, d7: true, hour: 10, digest: true };

/** Тело PUT /api/profile/settings — все поля обязательны. */
export interface ReminderSettingsInput extends ReminderSettings {
  /** IANA из списка экрана 2 (texts.timezone). */
  timezone: string;
}

/** Ответ POST /api/calendar/rebuild — «Пересобрать» на экране 19. */
export interface RebuildResult {
  /** Событий до конца текущего года — как на экране 5. */
  items_count: number;
  /** YYYY-MM-DD — ближайший неотмеченный срок или null. */
  nearest_due_date: string | null;
  profile: Profile;
}

/** Черновик задачи из бота (экраны 9, 10): `DialogState` → `/api/me`. Даты может не быть. */
export interface TaskDraft {
  title: string;
  /** YYYY-MM-DD или null («Выбрать дату» на экране 10). */
  due_date: string | null;
  /** 0-23 или null — времени в сообщении не было (контракт TIME-BE, #103/#104). */
  remind_hour?: number | null;
  /** 0-59 или null — вместе с remind_hour; время есть → в форме offset «в день срока». */
  remind_minute?: number | null;
}

/** Ответ GET /api/me. */
export interface Me {
  user_id: number;
  first_name: string | null;
  is_dev: boolean;
  start_param?: string | null;
  has_profile: boolean;
  profile: Profile | null;
  /** Черновик из бота; есть, только если открыли с `start_param=task_draft[_<id>]` и он ещё жив. */
  draft?: TaskDraft | null;
  /** Открыли кнопкой черновика из старого сообщения: `draft` — null, форма 17 пустая (#96). */
  draft_stale?: boolean;
}

/** Смещение напоминания своей задачи в днях (D11). */
export type RemindOffset = 0 | 1 | 3 | 7;
export const REMIND_OFFSETS: readonly RemindOffset[] = [0, 1, 3, 7];

export interface HowtoLink {
  label: string;
  url: string;
}

/** Карточка обязательства: CalendarItem + поля справочника (data-model.md §1, §3). */
export interface ObligationCard extends CalendarItem {
  type: "obligation";
  norm: string;
  source_url: string;
  /** Ровно три шага, плейсхолдеры уже подставлены бэкендом. */
  howto_steps: string[];
  howto_link: HowtoLink;
  penalty_text: string;
  /** YYYY-MM-DD — дата сверки записи справочника. */
  last_checked_at: string;
}

/** Карточка своей задачи: CalendarItem + настройки напоминания. */
export interface TaskCard extends CalendarItem {
  type: "task";
  category: "custom";
  remind_offset_days: RemindOffset;
  /** 0-23. */
  remind_hour: number;
  /** 0-59 (контракт TIME-BE, #103/#104). */
  remind_minute: number;
}

/**
 * Ответ GET /api/items/{type}/{id}. Той же карточкой (200) отвечают
 * POST/DELETE /api/items/{type}/{id}/done, POST /api/tasks и PATCH /api/tasks/{id}.
 * DELETE /api/tasks/{id} и POST /api/obligations/{id}/report — 204 без тела.
 */
export type ItemCard = ObligationCard | TaskCard;

/** Тело POST /api/tasks и PATCH /api/tasks/{id}. Категория всегда `custom`, её не передаём. */
export interface TaskInput {
  title: string;
  /** YYYY-MM-DD; можно в прошлом — тогда напоминания нет (D32, #108). */
  due_date: string;
  remind_offset_days: RemindOffset;
  /** 0-23. */
  remind_hour: number;
  /** 0-59 (контракт TIME-BE, #103/#104). */
  remind_minute: number;
  /** «Уже выполнено» — только при создании задачи в прошлом (D32, #108); PATCH его не шлёт. */
  done?: boolean;
}

/** Строка списка из карточки: лишние поля в список не тащим. */
export function toCalendarItem(card: ItemCard): CalendarItem {
  const { type, id, title, category, due_date, original_date, status, done_at } = card;
  return { type, id, title, category, due_date, original_date, status, done_at };
}
