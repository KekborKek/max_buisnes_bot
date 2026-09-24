// Тексты мини-приложения. Правит UX/UI без изменения логики.
// Источник — docs/spec/product.md и docs/screens/NN-*.md, дословно.
// Значение "TODO" — текста в спеке нет, ждём от UX (перечислены в PR #36 и PR T12).
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

  /** Экран 16 — docs/screens/16-card.md. */
  card: {
    sectionHowto: "Что сделать",
    sectionPenalty: "Если пропустить",
    sectionBasis: "Основание",
    source: "Источник",
    checked: (date: string) => `сверено ${date}`,
    shifted: (date: string) => `перенос с ${date}, выходной`,
    remindAt: (when: string, hour: number) => `Напомню ${when} в ${hour}:00`,
    /** `when` для remindAt по смещению задачи. В спеке есть только «за день» (экраны 9, 16). */
    remindWhen: {
      0: "TODO",
      1: "за день",
      3: "TODO",
      7: "TODO",
    },
    markDone: "Отметить выполненным",
    undoDone: "Отменить отметку",
    wrongDate: "Неверный срок",
    wrongDateThanks: "Спасибо, проверим",
    reschedule: "Перенести",
    delete: "Удалить",
    deleteConfirm: "Точно удалить?",
    deleted: "Задача удалена",
    overdueBanner: (date: string) => `Срок прошёл ${date}`,
    doneBanner: (date: string) => `Выполнено ${date}`,
    notFound: "Этого события больше нет",
    backToList: "К списку",
    disclaimerMark:
      "Вы отметили обязательство выполненным. Продукт не проверяет факт оплаты или отправки отчётности.",
    disclaimerAdvice: "Это не налоговая консультация. Суммы не считаем.",
  },

  /** Экран 17 — docs/screens/17-task-form.md. */
  form: {
    titleNew: "Новая задача",
    titleEdit: "Изменить задачу",
    name: "Название",
    namePlaceholder: "Например, оплатить аренду",
    nameRequired: "Назовите задачу",
    date: "Дата",
    datePast: "Выберите дату не раньше сегодняшней",
    /** Дату стёрли в поле. В спеке текста нет. */
    dateRequired: "TODO",
    remind: "Напомнить",
    remind0: "В день срока",
    remind1: "За 1 день",
    remind3: "За 3 дня",
    remind7: "За 7 дней",
    time: "Время напоминания",
    /** Подпись варианта времени: «9:00 · 10:00 · 18:00» — формат как в card.remindAt. */
    hour: (hour: number) => `${hour}:00`,
    save: "Сохранить",
    cancel: "Отмена",
    discardConfirm: "Удалить черновик?",
    created: "Задача добавлена",
    updated: "Задача изменена",
  },

  /** Экран 15 — docs/screens/15-month.md. */
  month: {
    today: "Сегодня",
    weekdays: ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"],
    dayEmpty: "В этот день сроков нет.",
    /** «В ноябре…» — предложный падеж из словаря monthsIn. */
    monthEmpty: (month: string) => `В ${month} обязательных сроков нет`,
    addTask: "+ Задача",
    /** Заголовок сетки: «Октябрь 2026». */
    title: (month: string, year: string) => `${month} ${year}`,
    /** Названия месяцев для заголовка, январь — 0. */
    months: [
      "Январь",
      "Февраль",
      "Март",
      "Апрель",
      "Май",
      "Июнь",
      "Июль",
      "Август",
      "Сентябрь",
      "Октябрь",
      "Ноябрь",
      "Декабрь",
    ],
    /** Предложный падеж для monthEmpty, январь — 0. */
    monthsIn: [
      "январе",
      "феврале",
      "марте",
      "апреле",
      "мае",
      "июне",
      "июле",
      "августе",
      "сентябре",
      "октябре",
      "ноябре",
      "декабре",
    ],
  },
};
