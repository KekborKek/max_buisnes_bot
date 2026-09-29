# Сборка из корня репозитория: docker compose build backend
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_DEFAULT_TIMEOUT=60 PIP_RETRIES=10
# Индекс пакетов. При нестабильном доступе к PyPI — зеркало через PIP_INDEX_URL в .env
ARG PIP_INDEX_URL=https://pypi.org/simple
WORKDIR /srv/backend

COPY backend/requirements.txt ./requirements.txt
RUN pip install --index-url "$PIP_INDEX_URL" -r requirements.txt

COPY backend/app ./app
COPY backend/scripts ./scripts
COPY content /srv/content
COPY seed /srv/seed
COPY deploy/certs/russian_trusted_root_ca.crt /srv/deploy/certs/russian_trusted_root_ca.crt

ENV CONTENT_DIR=/srv/content SEED_DIR=/srv/seed DATABASE_URL=sqlite+aiosqlite:////srv/data/app.db
ENV MAX_CA_BUNDLE=/srv/deploy/certs/russian_trusted_root_ca.crt
RUN mkdir -p /srv/data && useradd --create-home appuser && chown -R appuser /srv
USER appuser

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')"
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
