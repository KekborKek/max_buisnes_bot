// Типы данных мини-приложения. Источник — docs/spec/data-model.md §2–3.
// Контракт API календаря ещё не в openapi.yaml (задача T10): при расхождении правы openapi.yaml и T11b.

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

/** Профиль из data-model.md §2. Допущение до T10: в §3 поля `profile` не перечислены. */
export interface Profile {
  income_band: IncomeBand;
  regime: Regime;
  has_employees: boolean;
  /** IANA, например "Europe/Moscow". */
  timezone: string;
  nds_payer: boolean;
  calendar_built_at: string | null;
}

/** Черновик задачи из бота (экраны 9, 10): `DialogState` → `/api/me`. Даты может не быть. */
export interface TaskDraft {
  title: string;
  /** YYYY-MM-DD или null («Выбрать дату» на экране 10). */
  due_date: string | null;
}

/** Ответ GET /api/me. `has_profile`, `profile` и `draft` добавит T10. */
export interface Me {
  user_id: number;
  first_name: string | null;
  is_dev: boolean;
  start_param?: string | null;
  has_profile: boolean;
  profile: Profile | null;
  /** Черновик из бота; есть, только если открыли с `start_param=task_draft` и он ещё жив. */
  draft?: TaskDraft | null;
}

/** Смещение напоминания своей задачи в днях (D11). */
export type RemindOffset = 0 | 1 | 3 | 7;
export const REMIND_OFFSETS: readonly RemindOffset[] = [0, 1, 3, 7];
/** Час напоминания своей задачи (экран 17). */
export const REMIND_HOURS: readonly number[] = [9, 10, 18];

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
  remind_hour: number;
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
  /** YYYY-MM-DD, не раньше сегодняшнего в поясе пользователя. */
  due_date: string;
  remind_offset_days: RemindOffset;
  remind_hour: number;
}

/** Строка списка из карточки: лишние поля в список не тащим. */
export function toCalendarItem(card: ItemCard): CalendarItem {
  const { type, id, title, category, due_date, original_date, status, done_at } = card;
  return { type, id, title, category, due_date, original_date, status, done_at };
}
