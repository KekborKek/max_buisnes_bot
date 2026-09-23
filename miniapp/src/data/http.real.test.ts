// T11b (issue #59): разбор настоящих ответов бэкенда — не моков. Фикстуры в ./fixtures/real.ts
// сняты curl'ом с локального make dev-api (см. комментарий там же и отчёт PR). Смысл теста — поймать
// расхождение между тем, что реально отдаёт FastAPI (backend/app/api/schemas.py), и типами
// miniapp/src/types.ts, а не проверить сетевой слой (это уже покрыто http.test.ts).
import { afterEach, expect, test, vi } from "vitest";

import type { CalendarItem, ItemCard, Me } from "../types";
import {
  calendarFixture,
  itemObligationFixture,
  itemTaskFixture,
  meFixture,
  meNdsNullFixture,
} from "./fixtures/real";
import { httpSource } from "./http";

function respondJson(body: unknown) {
  vi.stubGlobal(
    "fetch",
    vi.fn(
      async () =>
        new Response(JSON.stringify(body), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
    ),
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
});

test("GET /api/me — профиль заполнен, nds_payer: false доходит как boolean", async () => {
  respondJson(meFixture);
  const me: Me = await httpSource.me(null);
  expect(me.has_profile).toBe(true);
  expect(me.profile).toEqual({
    income_band: "lt10",
    regime: "usn6",
    has_employees: false,
    timezone: "Europe/Moscow",
    nds_payer: false,
    calendar_built_at: "2026-09-23T21:25:59.874536Z",
  });
});

test("GET /api/me — nds_payer: null доходит как null, а не false (D25)", async () => {
  respondJson(meNdsNullFixture);
  const me: Me = await httpSource.me(null);
  expect(me.profile?.nds_payer).toBeNull();
});

test("GET /api/calendar — обязательство и своя задача в одном списке, порядок бэкенда сохраняется", async () => {
  respondJson(calendarFixture);
  const items: CalendarItem[] = await httpSource.calendar("2026-09-24", "2026-12-23");
  expect(items).toHaveLength(2);
  expect(items[0]).toMatchObject({ type: "task", id: 2, status: "upcoming" });
  expect(items[1]).toMatchObject({ type: "obligation", id: 1, category: "reports" });
});

test("GET /api/items/obligation/:id — поля справочника заполнены (howto_steps ровно три)", async () => {
  respondJson(itemObligationFixture);
  const card: ItemCard = await httpSource.item("obligation", 1);
  if (card.type !== "obligation") throw new Error("ожидали obligation");
  expect(card.howto_steps).toHaveLength(3);
  expect(card.norm).toBe("Тестовая норма №2");
  expect(card.howto_link).toEqual({
    label: "Открыть тест 2",
    url: "https://example.invalid/howto2",
  });
  expect(card.last_checked_at).toBe("2026-01-01");
});

test("GET /api/items/task/:id — remind_offset_days: 0 — валидное значение, не путать с отсутствием", async () => {
  respondJson(itemTaskFixture);
  const card: ItemCard = await httpSource.item("task", 2);
  if (card.type !== "task") throw new Error("ожидали task");
  expect(card.remind_offset_days).toBe(0);
  expect(card.remind_hour).toBe(9);
  expect(card.category).toBe("custom");
});
