// Даты, секции списка (экран 14) и сетка месяца (экран 15). Статус события не пересчитываем —
// его прислал бэкенд; здесь только раскладка и форматирование.
import type { CalendarItem, Category } from "./types";

export const DEFAULT_TIMEZONE = "Europe/Moscow";
/** Окно списка: события на 90 дней вперёд (docs/screens/14-list.md). */
export const LIST_WINDOW_DAYS = 90;

/** «Сегодня» в часовом поясе пользователя, YYYY-MM-DD. */
export function todayIn(timezone: string, now: Date = new Date()): string {
  const format = (tz: string) =>
    new Intl.DateTimeFormat("en-CA", {
      timeZone: tz,
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
    }).format(now);
  try {
    return format(timezone);
  } catch {
    return format(DEFAULT_TIMEZONE);
  }
}

function parseIso(iso: string): Date {
  return new Date(`${iso}T00:00:00Z`);
}

export function addDays(iso: string, days: number): string {
  const d = parseIso(iso);
  d.setUTCDate(d.getUTCDate() + days);
  return d.toISOString().slice(0, 10);
}

/** Ближайшее воскресенье включительно: если сегодня воскресенье — сегодня. */
export function weekEnd(today: string): string {
  const dow = parseIso(today).getUTCDay(); // 0 — воскресенье
  return addDays(today, (7 - dow) % 7);
}

export type SectionKey = "overdue" | "today" | "week" | "later";
export interface Section {
  key: SectionKey;
  items: CalendarItem[];
}

const SECTION_ORDER: SectionKey[] = ["overdue", "today", "week", "later"];

function sectionOf(item: CalendarItem, today: string, sunday: string): SectionKey {
  if (item.status === "overdue") return "overdue";
  if (item.status === "today") return "today";
  // Выполненные остаются в секции своей даты: прошлые — в «Просрочено», в её конце.
  if (item.due_date < today) return "overdue";
  if (item.due_date === today) return "today";
  return item.due_date <= sunday ? "week" : "later";
}

/** Непустые секции по порядку; внутри — по дате, выполненные в конце секции. */
export function groupSections(items: CalendarItem[], today: string): Section[] {
  const sunday = weekEnd(today);
  const buckets = new Map<SectionKey, CalendarItem[]>(SECTION_ORDER.map((k) => [k, []]));
  for (const item of items) buckets.get(sectionOf(item, today, sunday))!.push(item);
  const byDate = (a: CalendarItem, b: CalendarItem) =>
    Number(a.status === "done") - Number(b.status === "done") ||
    a.due_date.localeCompare(b.due_date);
  return SECTION_ORDER.map((key) => ({ key, items: buckets.get(key)!.sort(byDate) })).filter(
    (s) => s.items.length > 0,
  );
}

// С днём — чтобы месяц был в родительном падеже: «5 мая», а не «5 май».
const dayMonth = new Intl.DateTimeFormat("ru-RU", {
  day: "numeric",
  month: "short",
  timeZone: "UTC",
});

const dayMonthLong = new Intl.DateTimeFormat("ru-RU", {
  day: "numeric",
  month: "long",
  timeZone: "UTC",
});
const weekday = new Intl.DateTimeFormat("ru-RU", { weekday: "long", timeZone: "UTC" });

/** «28 октября»; год — только если не текущий: «5 января 2027». */
export function formatDate(iso: string, today: string): string {
  const d = parseIso(iso);
  const month = dayMonthLong.formatToParts(d).find((p) => p.type === "month")?.value ?? "";
  const year = iso.slice(0, 4) === today.slice(0, 4) ? "" : ` ${iso.slice(0, 4)}`;
  return `${d.getUTCDate()} ${month}${year}`;
}

/** Дата в карточке: «28 октября, среда» (product.md, «Форма»). */
export function formatCardDate(iso: string, today: string): string {
  return `${formatDate(iso, today)}, ${weekday.format(parseIso(iso))}`;
}

/** «20.09.2026» — дата сверки справочника (экран 16). */
export function formatNumericDate(iso: string): string {
  const [y, m, d] = iso.slice(0, 10).split("-");
  return `${d}.${m}.${y}`;
}

/** «Сегодня» в поясе пользователя относительно момента `instant` (ISO-время UTC). */
export function dateIn(timezone: string, instant: string): string {
  return todayIn(timezone, new Date(instant));
}

/** «28 окт»; год — только если не текущий: «5 янв 2027». */
export function formatShortDate(iso: string, today: string): string {
  const d = parseIso(iso);
  const month = (dayMonth.formatToParts(d).find((p) => p.type === "month")?.value ?? "").replace(
    ".",
    "",
  );
  const year = iso.slice(0, 4) === today.slice(0, 4) ? "" : ` ${iso.slice(0, 4)}`;
  return `${d.getUTCDate()} ${month}${year}`;
}

// ——— Экран 15. Месяц сеткой ———

/** Листать можно на 12 месяцев назад и вперёд от текущего (docs/screens/15-month.md). */
export const MONTH_RANGE = 12;
/** Под числом — до трёх точек, одна на категорию. */
export const MAX_DAY_DOTS = 3;
/** Порядок точек под числом — как категории в product.md. */
const CATEGORY_ORDER: Category[] = ["taxes", "contributions", "reports", "custom"];

const pad = (n: number, width = 2) => String(n).padStart(width, "0");

/** Месяц даты: «2026-10-28» → «2026-10». */
export function monthOf(iso: string): string {
  return iso.slice(0, 7);
}

function monthIndex(month: string): number {
  const [y, m] = month.split("-").map(Number);
  return y * 12 + (m - 1);
}

/** Соседний месяц: addMonths("2026-12", 1) → «2027-01». */
export function addMonths(month: string, n: number): string {
  const total = monthIndex(month) + n;
  return `${pad(Math.floor(total / 12), 4)}-${pad((total % 12) + 1)}`;
}

/** Сколько месяцев от `from` до `to`: monthDiff("2026-10", "2027-01") → 3. */
export function monthDiff(from: string, to: string): number {
  return monthIndex(to) - monthIndex(from);
}

/** Номер месяца 0–11 — индекс в словарях texts.month. */
export function monthNumber(month: string): number {
  return Number(month.slice(5, 7)) - 1;
}

/** Первое и последнее число месяца — период для GET /api/calendar. */
export function monthRange(month: string): { from: string; to: string } {
  return { from: `${month}-01`, to: addDays(`${addMonths(month, 1)}-01`, -1) };
}

/**
 * Клетки сетки по неделям с понедельника: `null` — пустая клетка до 1-го числа,
 * дальше даты месяца YYYY-MM-DD. Хвост последней недели не добиваем — это делает CSS-сетка.
 */
export function monthGrid(month: string): (string | null)[] {
  const { from, to } = monthRange(month);
  const lead = (parseIso(from).getUTCDay() + 6) % 7; // 0 — понедельник
  const days = Number(to.slice(8, 10));
  return [
    ...Array.from({ length: lead }, () => null),
    ...Array.from({ length: days }, (_, i) => `${month}-${pad(i + 1)}`),
  ];
}

/**
 * События месяца по дням (`due_date`). Чужие месяцы отбрасываются: /api/calendar добавляет
 * к периоду все неотмеченные просроченные. Внутри дня выполненные — в конце, как на экране 14.
 */
export function itemsByDay(items: CalendarItem[], month: string): Map<string, CalendarItem[]> {
  const days = new Map<string, CalendarItem[]>();
  for (const item of items) {
    if (monthOf(item.due_date) !== month) continue;
    const list = days.get(item.due_date);
    if (list) list.push(item);
    else days.set(item.due_date, [item]);
  }
  for (const list of days.values()) {
    list.sort((a, b) => Number(a.status === "done") - Number(b.status === "done"));
  }
  return days;
}

/** Категории дня для точек: по одной на категорию, в порядке product.md, не больше трёх. */
export function dayCategories(items: CalendarItem[]): Category[] {
  const present = new Set(items.map((i) => i.category));
  return CATEGORY_ORDER.filter((c) => present.has(c)).slice(0, MAX_DAY_DOTS);
}
