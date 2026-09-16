help:
	@echo "make setup | dev-api | dev-bot | dev-miniapp | test | lint | fmt | openapi | up | down"

setup:
	cd backend && python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
	cd miniapp && npm ci
	[ -f .env ] || cp .env.example .env

dev-api:
	cd backend && set -a && . ../.env && set +a && CONTENT_DIR=../content SEED_DIR=../seed .venv/bin/uvicorn app.main:app --reload --port $${API_PORT:-8000}

dev-bot:
	cd backend && set -a && . ../.env && set +a && CONTENT_DIR=../content SEED_DIR=../seed .venv/bin/python -m app.polling

dev-miniapp:
	cd miniapp && npm run dev

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

.PHONY: help setup dev-api dev-bot dev-miniapp test lint fmt openapi up down
