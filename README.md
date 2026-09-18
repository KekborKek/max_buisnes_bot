# MAX Business Bot — команда «Парето»

> Рабочее название. Разделы, помеченные ✏️, заполняются после выбора идеи продукта.

Чат-бот и мини-приложение для мессенджера MAX. Хакатон MAX, трек «Эффективный бизнес».

## ✏️ Назначение решения
Для кого сервис, какую проблему решает и какой результат получает пользователь.

## ✏️ Основной пользовательский сценарий
1. …
2. …

Сейчас в шаблоне — демонстрационный сценарий: `/start` → меню → «Познакомиться» → ввод имени; мини-приложение показывает текущего пользователя.

## Состав и архитектура
```
Пользователь MAX
   │ сообщения, кнопки                 │ открывает мини-приложение
   ▼                                    ▼
MAX Bot API ──вебхук──▶ backend (FastAPI)  ◀──/api── miniapp (React + MAX UI, nginx)
                         ├─ bot: роутер, обработчики, состояние диалога в БД
                         ├─ api: эндпоинты мини-приложения, проверка initData
                         ├─ core: БД (SQLite), клиент MAX API, аналитика, адаптеры данных
                         └─ SQLite (том app-data)
На сервере перед всем стоит Caddy: HTTPS, маршрутизация /webhook, /api → backend, остальное → miniapp.
```
| Компонент | Папка | Технологии |
|---|---|---|
| Бэкенд и бот | `backend/` | Python 3.12, FastAPI, SQLAlchemy, httpx |
| Мини-приложение | `miniapp/` | React 19, @maxhub/max-ui, MAX Bridge, Vite |
| Тексты | `content/texts.yaml`, `miniapp/src/texts.ts` | — |
| Тестовые данные | `seed/` | JSON |
| Прокси и HTTPS | `deploy/Caddyfile` | Caddy 2 |

Подробнее для разработчиков и ИИ-агентов — [AGENTS.md](AGENTS.md).

## Запуск одной командой (Docker)
```bash
cp .env.example .env      # заполнить MAX_BOT_TOKEN и остальные значения
# мини-апп вне MAX (http://localhost:5173) — поставьте ALLOW_DEV_INITDATA=true
docker compose up --build
```
- API: http://localhost:8000 (документация: http://localhost:8000/docs)
- Мини-приложение: http://localhost:8080 (вне MAX работает с тестовым пользователем)

На сервере с доменом и HTTPS (в `.env` обязательно `APP_ENV=prod` и `ALLOW_DEV_INITDATA=false`):
```bash
docker compose --profile prod up -d --build
python backend/scripts/webhook.py set     # подписать вебхук на https://$DOMAIN/webhook/max
```

### Остановка и повторный запуск
```bash
docker compose down        # остановить (данные в томе app-data сохраняются)
docker compose up -d       # запустить снова
docker compose down -v     # остановить и удалить данные
```

## Переменные окружения
| Переменная | Назначение | Пример |
|---|---|---|
| `MAX_BOT_TOKEN` | Токен бота MAX | — (секрет) |
| `MAX_WEBHOOK_SECRET` | Секрет вебхука, заголовок `X-Max-Bot-Api-Secret` | `change-me-please` |
| `PUBLIC_BASE_URL` | Публичный HTTPS-адрес | `https://bot.example.com` |
| `APP_ENV` | `dev` / `prod` | `dev` |
| `DATA_MODE` | `mock` — тестовые данные, `real` — интеграции | `mock` |
| `CAPTURE_UPDATES` | Сохранять сырые апдейты для отладки | `false` |
| `ALLOW_DEV_INITDATA` | Принимать `X-Max-Init-Data: dev` без проверки подписи — мини-апп вне MAX. Работает только при `APP_ENV=dev`. В проде всегда `false` | `false` |
| `API_PORT`, `MINIAPP_PORT`, `VITE_PORT` | Порты на локальной машине | `8000`, `8080`, `5173` |
| `DOMAIN` | Домен сервера для Caddy | `bot.example.com` |
| `MAX_CA_BUNDLE` | Путь к доп. корню TLS для `httpx`. По умолчанию подхватывается `deploy/certs/russian_trusted_root_ca.crt` — трогать не нужно, см. ниже | — |

## TLS: сертификат Минцифры для обращений к MAX API

`platform-api2.max.ru` подписан цепочкой Минцифры (`Russian Trusted Sub CA` → `Russian Trusted Root CA`), корня которой нет в стандартном бандле `certifi`. Без него любой запрос к MAX (`/me`, `/messages`, long polling) падает с `SSL: CERTIFICATE_VERIFY_FAILED`. Это подтверждено и самой документацией MAX (https://dev.max.ru/docs-api): «убедитесь, что добавили сертификат Минцифры в список доверенных».

Решение не в `update-ca-certificates` (он не влияет на проверку сертификатов в `httpx`, только на `curl`/`openssl` внутри контейнера), а в явном добавлении корня в бандл `httpx` — сделано в `backend/app/core/max_client.py` (см. `docs/max-api-notes.md`, раздел TLS). Корневой сертификат лежит в `deploy/certs/russian_trusted_root_ca.crt` и подхватывается автоматически — ни в Docker, ни при `make dev-api`/`make dev-bot` ничего вручную настраивать не нужно.

**Важно:** это именно английский `Russian Trusted Root CA` (RSA, отпечаток SHA256 `D2:6D:2D:02:31:B7:C3:9F:92:CC:73:85:12:BA:54:10:35:19:E4:40:5D:68:B5:BD:70:3E:97:88:CA:8E:CF:31`), а не более новый ГОСТ-центр «Минцифры России НУЦ» (2025) — это два разных, не связанных удостоверяющих центра. При замене файла в `deploy/certs/` всегда сверяйте отпечаток:
```bash
openssl x509 -in deploy/certs/russian_trusted_root_ca.crt -noout -fingerprint -sha256
```

## Порты
| Порт | Сервис |
|---|---|
| 8000 | backend (API, вебхук, `/docs`) |
| 8080 | miniapp (nginx) |
| 80, 443 | caddy (только профиль `prod`) |

## Зависимости
- Бэкенд: `backend/requirements.txt` (версии зафиксированы), для разработки — `backend/requirements-dev.txt`.
- Мини-приложение: `miniapp/package.json` + `miniapp/package-lock.json`.
- Для запуска нужен только Docker (Docker Desktop на Mac/Windows).

## Внешние сервисы и интеграции
| Сервис | Зачем | Можно ли воспроизвести в Docker |
|---|---|---|
| MAX Bot API (`platform-api2.max.ru`) | Приём и отправка сообщений | Нет, нужен токен бота |
| MAX Bridge (`st.max.ru/js/max-web-app.js`) | Данные запуска мини-приложения | Нет, работает внутри MAX |
| ✏️ … | … | … |

## Работа с данными
- Хранятся: id пользователя MAX, имя, состояние диалога, события аналитики (`backend/app/core/models.py`).
- Номер телефона — только после явного согласия пользователя (кнопка «Поделиться контактом»).
- Запросы мини-приложения проверяются по подписи `initData`.
- Секреты — только в переменных окружения, не в репозитории.

## Тестовые данные
`DATA_MODE=mock` — данные из `seed/*.json`. Все записи вымышленные и помечены «ТЕСТОВЫЕ ДАННЫЕ». ✏️ Описать, какие данные моделируются вместо реальных систем.

## ✏️ Пошаговый сценарий проверки
1. Открыть бота по ссылке: …
2. …

## ✏️ Примеры ожидаемого поведения
| Действие | Ответ системы |
|---|---|
| … | … |

## ✏️ Известные ограничения
- …

## Разработка без Docker
```bash
make setup           # один раз
make dev-api         # терминал 1
make dev-bot         # терминал 2 — бот через long polling (без вебхука)
make dev-miniapp     # терминал 3 — http://localhost:5173
make test && make lint
```

Мини-апп вне MAX работает с тестовым пользователем только при `ALLOW_DEV_INITDATA=true` в `.env`. `make setup` не трогает уже существующий `.env` — если он создан раньше, допишите переменную руками, иначе API будет отвечать 401.
