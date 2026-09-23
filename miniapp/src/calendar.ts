// Даты и секции списка (экран 14). Статус события не пересчитываем — его прислал бэкенд;
// здесь только раскладка по секциям и форматирование.
import type { CalendarItem } from "./types";

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
