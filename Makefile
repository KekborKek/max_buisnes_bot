# Python 3.12 — как в Docker; если его нет, берём системный python3
PYTHON ?= $(shell command -v python3.12 || command -v python3)

help:
	@echo "make setup | dev-api | dev-bot | dev-miniapp | test | lint | fmt | openapi | up | down"

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

check-tools:
	@command -v $(PYTHON) >/dev/null || { echo "Нет Python. Mac: brew install python@3.12"; exit 1; }
	@command -v npm >/dev/null || { echo "Нет Node.js/npm. Mac: brew install node@22 && brew link --overwrite node@22"; exit 1; }
	@echo "Python: $$($(PYTHON) --version), Node: $$(node --version)"

.PHONY: check-tools help setup dev-api dev-bot dev-miniapp test lint fmt openapi up down
