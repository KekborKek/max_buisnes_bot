# AGENTS.md — правила для ИИ-агентов (Claude Code, Codex, Cursor и др.)

Прочитай этот файл целиком перед любой задачей. Если что-то противоречит задаче — спроси.

## Проект
Чат-бот + мини-приложение для мессенджера MAX, трек «Эффективный бизнес» (хакатон MAX, команда «Парето»).
Идея продукта фиксируется в `docs/decisions.md`. Текущее состояние — `docs/STATUS.md`.

## Архитектура
```
MAX ──вебхук──▶ backend/app/webhook.py ──фон──▶ bot/dispatcher.py ──▶ bot/router.py ──▶ bot/handlers/*
                                                   │ идемпотентность        │ FSM в БД (Ctx.get_state/set_state)
Мини-апп (miniapp/, React + MAX UI + MAX Bridge) ──/api──▶ backend/app/api/routes.py (проверка initData)
Все обращения к MAX API — только core/max_client.py. Внешние данные — только core/adapters (Mock|Real).
Аналитика — core/events.track(). Тексты бота — content/texts.yaml, мини-аппа — miniapp/src/texts.ts.
```
Один процесс бэкенда, одна БД (SQLite), один сервер. Без микросервисов, очередей и кэшей.

## Стек (версии зафиксированы)
- Бэкенд: Python 3.12, FastAPI, SQLAlchemy 2 (async) + aiosqlite, httpx, pydantic-settings — `backend/requirements.txt`
- Мини-апп: React 19.2.8, @maxhub/max-ui 0.5.0, Vite, TypeScript — `miniapp/package.json`
- Запуск: Docker Compose (`compose.yaml`), прод — профиль `prod` с Caddy (HTTPS)

## Команды
```
make setup          # venv + npm ci + .env из примера
make dev-api        # бэкенд с автоперезагрузкой
make dev-bot        # бот через long polling (только разработка, без вебхука)
make dev-miniapp    # мини-апп на http://localhost:5173 (вне MAX работает с тестовым пользователем)
make test           # pytest + typecheck
make lint / make fmt
make openapi        # обновить openapi.yaml после изменения API
docker compose up --build
```

## Владение файлами
| Путь | Кто меняет |
|---|---|
| `backend/app/bot/` | дорожка BOT |
| `backend/app/api/` | дорожка API |
| `miniapp/` | дорожка FRONT |
| `seed/`, `backend/tests/fixtures/` | дорожка DATA |
| `docs/`, `README.md` | дорожка DOCS |
| `content/texts.yaml`, `miniapp/src/texts.ts` | UX/UI (люди); агентам — только по задаче |
| `backend/app/core/`, `openapi.yaml`, `compose.yaml`, Dockerfile-ы, `requirements*.txt`, `package*.json`, `.github/`, `AGENTS.md` | **только техлид** |

## Правила
1. Меняй только файлы из раздела «Можно менять» своей задачи (GitHub Issue). Нужно выйти за границы — остановись и напиши об этом в отчёте.
2. Не добавляй зависимости и не меняй модели БД сам — опиши необходимость в отчёте.
3. Секреты (токены, пароли, ключи) — только в `.env`. В коде, тестах, логах и коммитах их быть не должно.
4. Тексты для пользователя не хардкодь: `t("раздел.ключ")` в боте, `texts.*` в мини-аппе.
5. Каждое значимое действие пользователя — `ctx.track("имя_события", {...})` / `api.track(...)`.
6. Не выдумывай методы MAX API и MAX Bridge. Источник правды — `docs/max-api-notes.md` и https://dev.max.ru/docs. Не переносить API Telegram по аналогии.
7. Вебхук должен отвечать 200 быстро; тяжёлая работа — в фоне. Обработчики должны быть идемпотентными.
8. В интерфейсе всегда есть состояния загрузки, ошибки и пустого результата. После ошибки пользователь может продолжить без перезапуска.
9. Моки и тестовые данные явно помечены (`is_mock`, «ТЕСТОВЫЕ ДАННЫЕ»).
10. Новый обработчик бота → тест в `backend/tests/` на фикстуре апдейта.
11. Маленькие изменения: одна задача — один PR. Сообщения коммитов — по-русски, в повелительном наклонении: «Добавить обработку /start».

## Определение «готово»
- `make lint` и `make test` проходят (приложи вывод);
- сценарий из задачи проходит вручную;
- если менялся API — обновлён `openapi.yaml`;
- отчёт в PR: что сделано / что нет / допущения / вопросы техлиду; в описании `Closes #<номер>`.

## Порядок работы над задачей
1. `gh issue view <номер>` — прочитай задачу.
2. Составь план (≤7 шагов) и список файлов. Жди подтверждения человека.
3. Реализуй, проверь, оформи PR.
