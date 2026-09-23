// ТЕСТОВЫЕ ДАННЫЕ. Мок API мини-приложения до готовности бэкенда (T10).
// Включается только явно — см. readMockScenario() в source.ts. Названия и даты выдуманы
// для проверки интерфейса и не являются налоговыми сроками.
import { addDays, DEFAULT_TIMEZONE, todayIn } from "../calendar";
import type { CalendarItem, Category, ItemStatus, ItemType, Me, Profile } from "../types";
import { ApiError } from "./http";
import type { DataSource } from "./source";

export const MOCK_SCENARIOS = [
  "list", // профиль есть, события во всех секциях
  "empty", // профиль есть, событий нет
  "no_profile", // открыли не через бота — экран 18
  "unauthorized", // /api/me → 401 — экран 18 без слов об ошибке
  "offline", // /api/me → сеть — экран 18 + плашка ошибки
  "error", // календарь → сеть
  "flaky", // календарь: первые 1,5 с — сеть, «Повторить» позже — успех
  "server_error", // календарь → 5xx
  "session_expired", // календарь → 401
  "slow", // календарь не отвечает — видно скелетон
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
};

function mockMe(hasProfile: boolean, startParam: string | null): Me {
  return {
    user_id: 1,
    first_name: "ТЕСТОВЫЕ ДАННЫЕ",
    is_dev: true,
    start_param: startParam,
    has_profile: hasProfile,
    profile: hasProfile ? MOCK_PROFILE : null,
  };
}

/** Статус здесь считает мок вместо бэкенда; экран его только показывает. */
function item(
  id: number,
  type: ItemType,
  category: Category,
  title: string,
  due: string,
  today: string,
  done = false,
): CalendarItem {
  const status: ItemStatus = done
    ? "done"
    : due < today
      ? "overdue"
      : due === today
        ? "today"
        : "upcoming";
  return {
    type,
    id,
    title,
    category,
    due_date: due,
    original_date: due,
    status,
    done_at: done ? `${today}T07:00:00Z` : null,
  };
}

export function mockItems(today: string): CalendarItem[] {
  const d = (n: number) => addDays(today, n);
  return [
    item(1, "obligation", "reports", "Тестовая декларация (ТЕСТОВЫЕ ДАННЫЕ)", d(-3), today),
    item(2, "obligation", "taxes", "Тестовый авансовый платёж", d(0), today),
    item(3, "task", "custom", "Оплатить аренду офиса", d(1), today, true),
    item(4, "obligation", "contributions", "Тестовый страховой взнос", d(2), today),
    item(
      5,
      "obligation",
      "taxes",
      "Тестовое уведомление об исчисленных суммах с очень длинным названием",
      d(20),
      today,
    ),
    item(6, "task", "custom", "Сверить выписку банка", d(45), today),
    item(7, "obligation", "reports", "Тестовый отчёт за квартал", d(80), today),
  ];
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
  return {
    isMock: true,
    me: (startParam) =>
      wait(() => {
        if (scenario === "unauthorized") throw new ApiError("unauthorized", 401);
        if (scenario === "offline") throw new ApiError("network");
        return mockMe(scenario !== "no_profile", startParam);
      }),
    calendar: () => {
      if (scenario === "slow") return new Promise(() => {});
      return wait(() => {
        if (scenario === "error") throw new ApiError("network");
        if (scenario === "server_error") throw new ApiError("server", 503);
        if (scenario === "session_expired") throw new ApiError("unauthorized", 401);
        if (scenario === "flaky" && Date.now() - createdAt < FLAKY_MS)
          throw new ApiError("network");
        return scenario === "empty" ? [] : mockItems(todayIn(MOCK_PROFILE.timezone));
      });
    },
    track: async (name, props = {}) => {
      console.debug("[ТЕСТОВЫЕ ДАННЫЕ] track", name, props);
    },
  };
}
