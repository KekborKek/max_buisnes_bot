# Фикстуры апдейтов MAX

Тесты бота работают без живого вебхука: апдейты берутся из этих JSON.
Структура составлена по документации; **после получения токена замените их реальными**:
включите `CAPTURE_UPDATES=true`, пройдите сценарий в MAX, скопируйте файлы из
`backend/data/captured_updates/` сюда (удалив личные данные).

## Что именно нужно сверить **[сверить]**

Структуры ниже взяты из https://dev.max.ru/docs-api и на живых апдейтах не подтверждены.
Если сверка их не подтвердит, поедет адресация ответов и служебная аналитика.

| Фикстура | Что под вопросом |
|---|---|
| `message_created.json`, `message_created_group.json` | имя поля `message.recipient.chat_type` и его значения (`dialog` / `chat` / `channel` — из объекта `Chat`); в самом `Recipient` документация их не описывает |
| `callback_start_check.json` | `message.recipient` у `message_callback`: есть ли он и тот же ли `chat_type`. Тесты онбординга подставляют в неё свои `payload` и `callback_id` |
| `bot_stopped.json` | состав служебного апдейта: `chat_id`, `user`, `is_channel` |
| `bot_started.json` | `payload` из диплинка `?start=` (у нас — `start_param` в событии `bot_started`) |
| `message_created_start.json` | команда `/start` текстом: приходит ли она как `body.text` |

Как читается `chat_type` в коде — `backend/app/bot/context.py`, `Ctx.from_update` и `Ctx._target`.
При `chat_type: null` бот считает апдейт диалогом и отвечает в `user_id`.
