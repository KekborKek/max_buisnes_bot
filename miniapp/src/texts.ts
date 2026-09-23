// Тексты мини-приложения. Правит UX/UI без изменения логики.
// Источник — docs/spec/product.md и docs/screens/NN-*.md, дословно.
// Значение "TODO" — текста в спеке нет, ждём от UX (перечислены в PR #36).
export const texts = {
  mockBadge: "ТЕСТОВЫЕ ДАННЫЕ",

  common: {
    error: "Не получилось загрузить календарь. Проверьте интернет и нажмите «Повторить».",
    retry: "Повторить",
    /** 401 посреди работы: сессия истекла, нужно открыть мини-апп из бота заново. */
    reopen: "TODO",
  },

  /** Замена BackButton MAX вне MAX. Слово — как у кнопки «Назад» бота (onboarding.btn_back). */
  nav: {
    back: "Назад",
  },

  status: {
    done: "Выполнено",
    overdue: "Просрочено",
    today: "Сегодня",
    upcoming: "Предстоит",
  },

  category: {
    taxes: "Налоги",
    contributions: "Взносы",
    reports: "Отчётность",
    custom: "Своё",
  },

  /** Подписи режимов — как кнопки onboarding.q2_* в боте. */
  regime: {
    usn6: "УСН 6%",
    usn15: "УСН 15%",
    patent: "Патент",
    ausn: "АУСН",
    unknown: "TODO",
  },

  list: {
    profile: (regime: string, employees: string) => `ИП · ${regime} · ${employees}`,
    withoutEmployees: "без сотрудников",
    withEmployees: "есть сотрудники",
    tabList: "Список",
    tabMonth: "Месяц",
    sectionOverdue: "Просрочено",
    sectionToday: "Сегодня",
    sectionWeek: "Эта неделя",
    sectionLater: "Дальше",
    statusOverdue: "Просрочено",
    addTask: "+ Задача",
    emptyTitle: "Ближайших сроков нет",
    emptyText: "Добавьте свою задачу или посмотрите месяц целиком.",
    emptyMonth: "Открыть месяц",
  },

  gate: {
    title: "Календарь собирается в чате",
    text: "Ответьте на четыре вопроса — это займёт минуту.",
    openChat: "Открыть чат",
  },

  /** Временная заглушка экранов 15/16/17 до T12/T13. */
  placeholder: {
    soon: "TODO",
  },
};
