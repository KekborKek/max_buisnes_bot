// T11b (issue #59): настоящие ответы локального бэкенда, сняты curl'ом с `make dev-api`
// (ALLOW_DEV_INITDATA=true; OBLIGATIONS_FILE/WORKDAYS_FILE/NDS_FILE → backend/tests/fixtures/*.yaml;
// user_id=1, профиль usn6/lt10/без сотрудников засеян вручную — Profile создаётся ботом при онбординге,
// здесь этого шага нет). Значения переписаны без изменений в TS-константы: `resolveJsonModule`
// в tsconfig.json не включён (конфиги сборки — не в «Можно менять» T11b), поэтому не `.json`.
// Байт в байт то же самое можно получить, повторив curl-команды из отчёта PR.
export const meFixture = {
  user_id: 1,
  first_name: "Dev",
  is_dev: true,
  start_param: null,
  has_profile: true,
  profile: {
    income_band: "lt10",
    regime: "usn6",
    has_employees: false,
    timezone: "Europe/Moscow",
    nds_payer: false,
    calendar_built_at: "2026-09-23T21:25:59.874536Z",
  },
  draft: null,
};

/** D25: income_band/regime сочетание, для которого бэкенд не может определить nds_payer. */
export const meNdsNullFixture = {
  ...meFixture,
  profile: { ...meFixture.profile, nds_payer: null },
};

export const calendarFixture = [
  {
    type: "task",
    id: 2,
    title: "Через vite proxy",
    category: "custom",
    due_date: "2026-09-30",
    original_date: "2026-09-30",
    status: "upcoming",
    done_at: null,
  },
  {
    type: "obligation",
    id: 1,
    title: "Тестовое квартальное обязательство",
    category: "reports",
    due_date: "2026-10-25",
    original_date: "2026-10-25",
    status: "upcoming",
    done_at: null,
  },
];

export const itemObligationFixture = {
  type: "obligation",
  id: 1,
  title: "Тестовое квартальное обязательство",
  category: "reports",
  due_date: "2026-10-25",
  original_date: "2026-10-25",
  status: "upcoming",
  done_at: null,
  norm: "Тестовая норма №2",
  source_url: "https://example.invalid/test-quarterly",
  howto_steps: ["Тестовый шаг А.", "Тестовый шаг Б.", "Тестовый шаг В."],
  howto_link: { label: "Открыть тест 2", url: "https://example.invalid/howto2" },
  penalty_text: "Тестовый текст о штрафе.",
  last_checked_at: "2026-01-01",
  remind_offset_days: null,
  remind_hour: null,
};

export const itemTaskFixture = {
  type: "task",
  id: 2,
  title: "Через vite proxy",
  category: "custom",
  due_date: "2026-09-30",
  original_date: "2026-09-30",
  status: "upcoming",
  done_at: null,
  norm: null,
  source_url: null,
  howto_steps: null,
  howto_link: null,
  penalty_text: null,
  last_checked_at: null,
  remind_offset_days: 0,
  remind_hour: 9,
};
