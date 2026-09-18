# Шпаргалка по MAX API (источник правды для агентов)

Сверено с https://dev.max.ru/docs 16.09.2026. Документация меняется — перед сдачей перепроверить.
Пометка **[сверить]** — структура взята из документации частично, подтвердить на реальных апдейтах.

## Bot API
- Базовый URL: `https://platform-api2.max.ru` (не platform-api).
- Авторизация: заголовок `Authorization: <token>`. Токен в query-параметрах не работает.
- Лимит: 30 запросов в секунду.
- `GET /me` — информация о боте.
- `POST /messages?user_id=<id>` или `?chat_id=<id>` — отправка. Тело: `text`, `attachments`, `format` (`markdown` | `html`).
- `PUT /messages` — редактирование, `DELETE /messages` — удаление.
- `POST /answers?callback_id=<id>` — ответ на нажатие callback-кнопки. Тело: `message` (изменить сообщение), `notification`.
- `POST /uploads` — загрузка файлов (сначала загрузка, потом вложение в сообщение).
- `GET /chats` **не поддерживается** с июня 2026: `chat_id` берём только из апдейтов.

## TLS
- `platform-api2.max.ru` подписан цепочкой Минцифры: лист → `Russian Trusted Sub CA` → `Russian Trusted Root CA`. Этого корня нет ни в `certifi`, ни в базовых Docker-образах — без него `httpx` падает с `SSL: CERTIFICATE_VERIFY_FAILED`.
- Подтверждено официальной документацией (https://dev.max.ru/docs-api, сверено 18.09.2026): «убедитесь, что добавили сертификат Минцифры в список доверенных»; отдельно — с 25.05.2026 MAX перестаёт принимать вебхуки по HTTP и с самоподписанными сертификатами.
- `update-ca-certificates` не помогает: `httpx` проверяет сертификаты по бандлу `certifi`, а не по системному хранилищу ОС.
- Решение — `backend/app/core/max_client.py` строит `ssl.SSLContext` из `certifi` + корня `deploy/certs/russian_trusted_root_ca.crt` (настройка `max_ca_bundle` в `config.py`, подхватывается автоматически). Подробности и как проверять отпечаток — в `README.md`, раздел «TLS».
- Нужен именно `Russian Trusted Root CA` (RSA, англ. CN, SHA256 `D2:6D:2D:02:31:B7:C3:9F:92:CC:73:85:12:BA:54:10:35:19:E4:40:5D:68:B5:BD:70:3E:97:88:CA:8E:CF:31`) — не путать с более новым отдельным ГОСТ-центром «Минцифры России НУЦ» (2025), он для другой цели и не подходит для этой цепочки.

## Получение апдейтов
- **Вебхук (прод):** `POST /subscriptions` c `{"url", "update_types"?, "secret"?}`.
  - `secret`: `^[a-zA-Z0-9_-]{5,256}$`, приходит в заголовке `X-Max-Bot-Api-Secret`.
  - Эндпоинт обязан вернуть **HTTP 200 в течение 30 секунд**; 8 часов без успешной доставки → подписка отключается.
  - Только HTTPS с сертификатом доверенного ЦС.
- **Long polling (только разработка):** `GET /updates?marker=&timeout=0..90&limit=1..1000&types=`. Ответ: `{"updates": [...], "marker": N}`. Без `marker` приходит только последнее событие. Нельзя одновременно с вебхуком.

## Апдейты (Update)
Общие поля: `update_type`, `timestamp` (мс).
- `bot_started`: `chat_id`, `user{user_id, name…}`, `payload` (из диплинка `?start=`) **[сверить]**
- `message_created`: `message.sender.user_id`, `message.recipient.chat_id`, `message.body.mid`, `message.body.text` **[сверить]**
- `message_callback`: `callback.callback_id`, `callback.payload`, `callback.user` **[сверить]**
- Также: `bot_added`, `bot_stopped`, `bot_removed`, `message_edited`, `message_removed`, `user_added`, `user_removed`, `chat_title_changed` и др.

Как сверить: `CAPTURE_UPDATES=true`, пройти сценарий, открыть `backend/data/captured_updates/`.

## Кнопки (inline_keyboard)
- До 210 кнопок, 30 рядов, 7 в ряду; для `link`, `open_app`, `request_contact`, `request_geo_location` — до 3 в ряду.
- Типы: `callback` (payload), `link` (url), `request_contact`, `request_geo_location`, `open_app` (мини-апп) **[сверить поля]**, `clipboard`.
- В `payload` — короткий идентификатор, не данные.
- `request_contact` возвращает контакт с hash — номер можно проверить без SMS.

## Диплинки
`https://max.ru/<botName>?start=<payload>` — payload до 128 символов, приходит в `bot_started`. Готовый механизм QR-входа из офлайна.

## Мини-приложение
- Только HTTPS. Подключается к боту, не существует отдельно.
- MAX Bridge: `<script src="https://st.max.ru/js/max-web-app.js"></script>` → `window.WebApp`.
- Данные запуска: `WebApp.initData` (строка для проверки на сервере), `WebApp.initDataUnsafe` (объект, `start_param`).
- Методы: `requestContact()`, `openLink(url)`, `openMaxLink(url)`, `downloadFile(url, name)`, `shareContent()`, `shareMaxContent()`, `openCodeReader()` (QR), `BackButton.show/hide/onClick/offClick`, `DeviceStorage`, `SecureStorage`, `BiometricManager`, `HapticFeedback`, `ScreenCapture`, `enableClosingConfirmation()`, `getLaunchContext()`, `requestScreenMaxBrightness()`.
- **Проверка initData на бэкенде** (реализовано в `core/initdata.py`):
  `secret_key = HMAC_SHA256(key="WebAppData", msg=BOT_TOKEN)`;
  строка проверки — пары `key=value` без `hash`, URL-декодированные, отсортированные, через `\n`;
  `hex(HMAC_SHA256(secret_key, строка)) == hash`.
- MAX UI: `npm i @maxhub/max-ui`, обёртка `<MaxUI>`, стили `@maxhub/max-ui/dist/styles.css`. Компоненты: Panel, Button, CellList, CellSimple, CellHeader, CellInput, CellAction, Input, Textarea, Switch, Radio, Avatar, Counter, Spinner, Typography.*, IconButton, ToolButton, Flex, Grid, Container.
