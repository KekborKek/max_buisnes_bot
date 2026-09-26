// ТЕСТОВЫЕ ДАННЫЕ. Мок API мини-приложения до готовности бэкенда (T10).
// Включается только явно — см. readMockScenario() в source.ts. Названия, даты, нормы и ссылки
// выдуманы для проверки интерфейса и не являются налоговыми сроками.
import { addDays, DEFAULT_TIMEZONE, todayIn } from "../calendar";
import type {
  CalendarItem,
  Category,
  ItemCard,
  ItemStatus,
  Me,
  ObligationCard,
  Profile,
  RebuildResult,
  TaskCard,
  TaskInput,
} from "../types";
import { toCalendarItem } from "../types";
import { ApiError } from "./http";
import type { DataSource } from "./source";

export const MOCK_SCENARIOS = [
  "list", // профиль есть, события во всех секциях
  "empty", // профиль есть, событий нет
  "no_profile", // открыли не через бота — экран 18
  "unauthorized", // /api/me → 401 — экран 18 без слов об ошибке
  "offline", // /api/me → сеть — экран 18 + плашка ошибки
  "error", // календарь и карточка → сеть
  "flaky", // календарь и карточка: первые 1,5 с — сеть, «Повторить» позже — успех
  "server_error", // календарь и карточка → 5xx
  "session_expired", // календарь и карточка → 401
  "slow", // календарь и карточка не отвечают — видно скелетон
  "action_error", // данные грузятся, любое действие (отметка, сохранение, удаление) → сеть
] as const;
export type MockScenario = (typeof MOCK_SCENARIOS)[number];

export function isMockScenario(value: string): value is MockScenario {
  return (MOCK_SCENARIOS as readonly string[]).includes(value);
}

export const MOCK_PROFILE: Profile = {
  income_band: "lt10",
  regime: "usn6",
  has_employees: false,
  timezone: DEFAULT_TIMEZONE,
  nds_payer: false,
  calendar_built_at: "2026-09-20T10:00:00Z",
  reference_checked_at: "2026-09-20",
  reminders: { d30: true, d7: true, hour: 10, digest: true },
};

const mockToday = () => todayIn(MOCK_PROFILE.timezone);

function mockMe(profile: Profile | null, startParam: string | null): Me {
  return {
    user_id: 1,
    first_name: "ТЕСТОВЫЕ ДАННЫЕ",
    is_dev: true,
    start_param: startParam,
    has_profile: profile !== null,
    profile,
    // Черновик из чата (экран 9, «Изменить») — при start_param=task_draft[_<id>] (#96);
    // task_draft_stale0 — кнопка из старого сообщения: черновика нет, форма пустая.
    draft:
      /^task_draft(_[0-9a-z]+)?$/.test(startParam ?? "") && startParam !== "task_draft_stale0"
        ? { title: "Оплатить аренду (ТЕСТОВЫЕ ДАННЫЕ)", due_date: addDays(mockToday(), 5) }
        : null,
    draft_stale: startParam === "task_draft_stale0",
  };
}

/** Статус здесь считает мок вместо бэкенда; экран его только показывает. */
function statusOf(due: string, doneAt: string | null, today: string): ItemStatus {
  if (doneAt) return "done";
  if (due < today) return "overdue";
  return due === today ? "today" : "upcoming";
}

function obligation(
  id: number,
  category: Exclude<Category, "custom">,
  title: string,
  due: string,
  today: string,
  original = due,
): ObligationCard {
  return {
    type: "obligation",
    id,
    title,
    category,
    due_date: due,
    original_date: original,
    status: statusOf(due, null, today),
    done_at: null,
    norm: "Тестовая норма, ст. 0 (ТЕСТОВЫЕ ДАННЫЕ)",
    source_url: "https://example.com/test-source",
    howto_steps: [
      "Тестовый шаг один: проверьте данные (ТЕСТОВЫЕ ДАННЫЕ).",
      "Тестовый шаг два: подготовьте документ.",
      "Тестовый шаг три: отправьте его до срока.",
    ],
    howto_link: { label: "Тестовая ссылка", url: "https://example.com/test-howto" },
    penalty_text: "Тестовый текст о последствиях пропуска (ТЕСТОВЫЕ ДАННЫЕ).",
    last_checked_at: "2026-09-20",
  };
}

function task(id: number, title: string, due: string, today: string, done = false): TaskCard {
  const doneAt = done ? `${today}T07:00:00Z` : null;
  return {
    type: "task",
    id,
    title,
    category: "custom",
    due_date: due,
    original_date: due,
    status: statusOf(due, doneAt, today),
    done_at: doneAt,
    remind_offset_days: 1,
    remind_hour: 10,
  };
}

export function mockCards(today: string): ItemCard[] {
  const d = (n: number) => addDays(today, n);
  return [
    obligation(1, "reports", "Тестовая декларация (ТЕСТОВЫЕ ДАННЫЕ)", d(-3), today),
    obligation(2, "taxes", "Тестовый авансовый платёж", d(0), today),
    task(3, "Оплатить аренду офиса", d(1), today, true),
    // Перенос с выходного: исходная дата раньше итоговой.
    obligation(4, "contributions", "Тестовый страховой взнос", d(2), today, d(0)),
    obligation(
      5,
      "taxes",
      "Тестовое уведомление об исчисленных суммах с очень длинным названием",
      d(20),
      today,
    ),
    task(6, "Сверить выписку банка", d(45), today),
    obligation(7, "reports", "Тестовый отчёт за квартал", d(80), today),
  ];
}

export function mockItems(today: string): CalendarItem[] {
  return mockCards(today).map(toCalendarItem);
}

const DELAY_MS = 400;
const FLAKY_MS = 1500;
const wait = <T>(value: () => T): Promise<T> =>
  new Promise((resolve, reject) =>
    setTimeout(() => {
      try {
        resolve(value());
      } catch (e) {
        reject(e);
      }
    }, DELAY_MS),
  );

export function createMockSource(scenario: MockScenario): DataSource {
  const createdAt = Date.now();
  // Состояние мока живёт, пока открыт мини-апп: отметки и задачи видны в списке и карточке.
  let cards: ItemCard[] = scenario === "empty" ? [] : mockCards(mockToday());
  let nextTaskId = 100;
  // Профиль тоже живёт в моке: сохранённые на экране 13 настройки видны после «Назад» и на экране 19.
  let profile: Profile = MOCK_PROFILE;

  /** Сбои чтения — как у календаря в T11. */
  const readFailure = () => {
    if (scenario === "error") throw new ApiError("network");
    if (scenario === "server_error") throw new ApiError("server", 503);
    if (scenario === "session_expired") throw new ApiError("unauthorized", 401);
    if (scenario === "flaky" && Date.now() - createdAt < FLAKY_MS) throw new ApiError("network");
  };
  const read = <T>(value: () => T): Promise<T> =>
    scenario === "slow"
      ? new Promise<T>(() => {})
      : wait(() => {
          readFailure();
          return value();
        });
  const write = <T>(value: () => T): Promise<T> =>
    wait(() => {
      if (scenario === "action_error") throw new ApiError("network");
      return value();
    });

  const find = (type: ItemCard["type"], id: number): ItemCard => {
    const card = cards.find((c) => c.type === type && c.id === id);
    if (!card) throw new ApiError("client", 404);
    return card;
  };
  const replace = (next: ItemCard): ItemCard => {
    cards = cards.map((c) => (c.type === next.type && c.id === next.id ? next : c));
    return next;
  };
  const setDone = (type: ItemCard["type"], id: number, done: boolean): ItemCard => {
    const card = find(type, id);
    const doneAt = done ? (card.done_at ?? new Date().toISOString()) : null;
    return replace({
      ...card,
      done_at: doneAt,
      status: statusOf(card.due_date, doneAt, mockToday()),
    });
  };
  const taskFrom = (id: number, input: TaskInput, doneAt: string | null): TaskCard => ({
    type: "task",
    id,
    title: input.title,
    category: "custom",
    due_date: input.due_date,
    original_date: input.due_date,
    status: statusOf(input.due_date, doneAt, mockToday()),
    done_at: doneAt,
    remind_offset_days: input.remind_offset_days,
    remind_hour: input.remind_hour,
  });

  return {
    isMock: true,
    me: (startParam) =>
      wait(() => {
        if (scenario === "unauthorized") throw new ApiError("unauthorized", 401);
        if (scenario === "offline") throw new ApiError("network");
        return mockMe(scenario !== "no_profile" ? profile : null, startParam);
      }),
    calendar: () => read(() => cards.map(toCalendarItem)),
    item: (type, id) => read(() => find(type, id)),
    markDone: (type, id) => write(() => setDone(type, id, true)),
    undoDone: (type, id) => write(() => setDone(type, id, false)),
    reportWrongDate: (id) =>
      write(() => {
        find("obligation", id);
      }),
    createTask: (input) =>
      write(() => {
        const created = taskFrom(nextTaskId++, input, null);
        cards = [...cards, created];
        return created;
      }),
    updateTask: (id, input) =>
      write(() => replace(taskFrom(id, input, find("task", id).done_at)) as TaskCard),
    deleteTask: (id) =>
      write(() => {
        find("task", id);
        cards = cards.filter((c) => !(c.type === "task" && c.id === id));
      }),
    // Мок сборку не повторяет: события те же, меняется только время сборки.
    rebuild: () =>
      write((): RebuildResult => {
        const today = mockToday();
        const upcoming = cards
          .filter((c) => c.type === "obligation" && !c.done_at && c.due_date >= today)
          .map((c) => c.due_date)
          .sort();
        profile = { ...profile, calendar_built_at: new Date().toISOString() };
        return {
          items_count: cards.filter(
            (c) => c.type === "obligation" && c.due_date.slice(0, 4) === today.slice(0, 4),
          ).length,
          nearest_due_date: upcoming[0] ?? null,
          profile,
        };
      }),
    saveSettings: ({ timezone, ...reminders }) =>
      write(() => {
        profile = { ...profile, timezone, reminders };
        return profile;
      }),
    track: async (name, props = {}) => {
      console.debug("[ТЕСТОВЫЕ ДАННЫЕ] track", name, props);
    },
  };
}
