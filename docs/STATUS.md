# STATUS — что сейчас работает

> Обновляется техлидом или планировщиком после каждой волны задач. Это память проекта для людей
> и агентов: новая сессия начинает отсюда, а не с перечитывания истории.

## Дата: 24.09.2026

Продукт — «Календарь обязательств ИП» (спека: `docs/spec/README.md`, план и волны: `docs/spec/plan.md`,
решения D1–D31: `docs/spec/decisions.md`). Агенты работают по `docs/prompts/executor.md`.

### Прод
- `https://vse-uspel.ru` (запасной `https://72-4-66-242.sslip.io`): VPS в Финляндии, `/opt/max-bot`,
  `docker compose --profile prod`, Caddy + Let's Encrypt. Доступен из РФ, MAX API с сервера отвечает.
- Деплой: `git archive HEAD | ssh root@72.4.66.242 'tar -x -C /opt/max-bot'` (кроме `.env`) →
  `docker compose --profile prod up -d --build`. Серверный `.env` — прод-значения, свой секрет вебхука.
- **Вебхук не подписан** — бот в разработке на long polling (`make dev-bot`). Подписка — ближе к демо.
- **Адрес мини-аппа в настройках бота не прописан** — доступа к кабинету нет, прописывают организаторы.

### Слито в main (задачи плана)
T0 ядро · T2 загрузчик справочников · T3 движок дат (372 теста) · T4 сборка календаря и планирование
уведомлений · T5a экраны 1–2 · T5b экраны 3 и 5 · T6 рассылка напоминаний и экран 6 · T8a парсер дат ·
T10 API мини-аппа · T11 каркас мини-аппа + экраны 14, 18 · T12 экраны 16, 17 · T11b мини-апп на настоящем API ·
T7 экраны 7–8 · T9 экран 11 · API-2 тесты API.
Путь `/start` → 4 ответа → ответ про НДС → календарь собран проходит в тестах целиком.

### Завтра — начать отсюда
1. **T8b (#62)** — экраны 9 и 10. Ветка `feat/t8b-task-chat` (worktree `../mbb-t8b`), кода нет, план согласован:
   `handlers/task_chat.py` (новый, `@router.fallback` → `parse_task_from_text`), переписать `fallback.py`,
   черновик строго `DialogState.data["task_draft"] = {title, due_date}`, «Изменить» — `open_app(payload="task_draft")`.
   Разрешена правка `dispatcher.py` (3 строки: `fallback.after_handler(ctx)` до коммита,
   `fallback.on_failure(ctx)` вместо `reply(errors.internal)` — короткая отдельная транзакция после rollback,
   без сети; `last_action` пишется только там, `fb:retry` повторяет его) и `test_dispatcher_transaction.py`.
   `{count}` в `saved` — невыполненные события от сегодня; `unknown_3` только при `SUPPORT_URL`;
   «сохранение не прошло» → `fallback.service` (строкой в PR).
2. **T13** — экран 15 (сетка месяца, D5, D23) + `/demo_remind` для `ADMIN_IDS` (помощник отправки одного
   уведомления есть в `calendar/reminders.py`). Нужен для демо.
3. **Долги одной задачей:** `api/items.py` → `calendar/marks.py`; «Напомнить завтра» по отмеченному событию
   должно отвечать «уже отмечено» (экран 6); один форматтер дат вместо трёх.
4. T14 Should (12, 13, 19) — после всего Must.
5. Когда придут справочники аналитика — деплой (см. «Прод»), прогон пути вживую через `make dev-bot`,
   затем подписка вебхука на `https://vse-uspel.ru/webhook/max`.

### Блокеры и долги
- **Нет реальных `content/obligations.yaml`, `workdays.yaml`, `nds.yaml`** (аналитик, срок 24.09).
  Без них бот на `/start` отвечает `common.error`, список и карточка мини-аппа — 500.
- Ждут подтверждения: D2, D25 (продакт); D28, D29 (аналитик); `SUPPORT_URL`/`PRIVACY_URL` (продакт).
- Тексты `TODO` для UX: `miniapp/src/texts.ts` (`common.reopen`, `regime.unknown`, `placeholder.soon`,
  `card.remindWhen` 0/3/7, `form.dateRequired`), `content/texts.yaml` (`howto.btn_source`).
- Долг: `calendar/reminders.py` и `calendar/howto_text.py` импортируют модули бота.
- Не проверено вживую: обрезка подписей кнопок, индикатор «печатает» в личном диалоге,
  структура `open_app` и callback-апдейтов (**[сверить]** в `docs/max-api-notes.md`).

### Как работаем с агентами
- Планировщик создаёт worktree `../mbb-<задача>` и ветку заранее; агент работает только там.
- Агент показывает план и ждёт «ок»; ведёт журнал передачи `scratchpad/handoff/<задача>.md`.
- Не больше ~4 агентов на opus одновременно (23.09 упёрлись в лимит сессии); простое — на sonnet.
- Планировщик ревьюит PR, при конфликтах в `handlers/__init__.py` / `texts.yaml` сливает сам,
  проверяет `texts.yaml` на дубли ключей, сливает после зелёного CI.
