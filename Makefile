# Python 3.12 — как в Docker; если его нет, берём системный python3
PYTHON ?= $(shell command -v python3.12 || command -v python3)

help:
	@echo "make setup | dev-api | dev-bot | dev-miniapp | test | lint | fmt | openapi | up | down"
	@echo "make db-reset — удалить ЛОКАЛЬНУЮ базу и том app-data (на проде не запускать)"

setup: check-tools
	cd backend && $(PYTHON) -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
	cd miniapp && npm ci
	[ -f .env ] || cp .env.example .env

dev-api:
	cd backend && set -a && . ../.env && set +a && CONTENT_DIR=../content SEED_DIR=../seed .venv/bin/uvicorn app.main:app --reload --port $${API_PORT:-8000}

dev-bot:
	cd backend && set -a && . ../.env && set +a && CONTENT_DIR=../content SEED_DIR=../seed .venv/bin/python -m app.polling

dev-miniapp:
	cd miniapp && VITE_BOT_URL="$$(sed -n 's/^VITE_BOT_URL=//p' ../.env 2>/dev/null)" npm run dev

test:
	cd backend && .venv/bin/pytest -q
	cd miniapp && npm run typecheck

lint:
	cd backend && .venv/bin/ruff check . && .venv/bin/ruff format --check .

fmt:
	cd backend && .venv/bin/ruff check --fix . && .venv/bin/ruff format .

openapi:
	cd backend && .venv/bin/python scripts/export_openapi.py

up:
	docker compose up --build

down:
	docker compose down

# Удаляет ЛОКАЛЬНУЮ базу SQLite (путь из DATABASE_URL в .env, по умолчанию backend/data/app.db)
# и том Docker app-data. Спрашивает подтверждение. НА ПРОДЕ НЕ ЗАПУСКАТЬ: там живые пользователи,
# схема обновляется лёгкой миграцией при старте (docs/decisions.md, 27.09.2026).
# При APP_ENV=prod в .env цель отказывается работать.
db-reset:
	@if grep -Eq '^[[:space:]]*APP_ENV=["'"'"']?prod' .env 2>/dev/null; then \
		echo "APP_ENV=prod в .env: make db-reset на проде запрещён — там живые пользователи."; exit 1; \
	fi
	@url="$$(sed -n 's/^[[:space:]]*DATABASE_URL=//p' .env 2>/dev/null | tail -n 1)"; \
	url="$${url:-$${DATABASE_URL:-sqlite+aiosqlite:///./data/app.db}}"; \
	case "$$url" in sqlite*:///*) ;; *) echo "DATABASE_URL не SQLite-файл: $$url"; exit 1;; esac; \
	db="$${url#*:///}"; \
	case "$$db" in /*) ;; *) db="backend/$${db#./}";; esac; \
	vol="$${COMPOSE_PROJECT_NAME:-max-business-bot}_app-data"; \
	files=""; for f in "$$db" "$$db-wal" "$$db-shm" "$$db-journal"; do [ -e "$$f" ] && files="$$files $$f"; done; \
	has_vol=""; command -v docker >/dev/null 2>&1 && docker volume inspect "$$vol" >/dev/null 2>&1 && has_vol=1; \
	if [ -z "$$files" ] && [ -z "$$has_vol" ]; then echo "Удалять нечего: нет ни $$db, ни тома $$vol."; exit 0; fi; \
	echo "ВНИМАНИЕ: только для локальной разработки, на проде не запускать. Будет удалено:"; \
	for f in $$files; do echo "  файл $$f"; done; \
	[ -n "$$has_vol" ] && echo "  том Docker $$vol (контейнер backend будет остановлен)"; \
	printf "Все данные в них пропадут. Введите yes для подтверждения: "; \
	read answer; [ "$$answer" = "yes" ] || { echo "Отменено, ничего не удалено."; exit 1; }; \
	for f in $$files; do rm -f "$$f" && echo "Удалён $$f"; done; \
	if [ -n "$$has_vol" ]; then docker compose rm -sf backend >/dev/null 2>&1 && docker volume rm "$$vol" >/dev/null && echo "Удалён том $$vol"; fi; \
	echo "Готово. Таблицы создадутся заново при следующем старте бэкенда."

check-tools:
	@command -v $(PYTHON) >/dev/null || { echo "Нет Python. Mac: brew install python@3.12"; exit 1; }
	@command -v npm >/dev/null || { echo "Нет Node.js/npm. Mac: brew install node@22 && brew link --overwrite node@22"; exit 1; }
	@echo "Python: $$($(PYTHON) --version), Node: $$(node --version)"

.PHONY: check-tools help setup dev-api dev-bot dev-miniapp test lint fmt openapi up down db-reset
