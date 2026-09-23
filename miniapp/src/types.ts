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

/** Ответ GET /api/me. `has_profile` и `profile` добавит T10. */
export interface Me {
  user_id: number;
  first_name: string | null;
  is_dev: boolean;
  start_param?: string | null;
  has_profile: boolean;
  profile: Profile | null;
}
