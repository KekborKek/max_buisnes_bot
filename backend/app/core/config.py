"""Настройки приложения. Все значения берутся из переменных окружения (.env)."""

from functools import lru_cache
from pathlib import Path
from typing import Annotated

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

# backend/app/core/config.py -> repo root (тот же приём, что и для content_dir/seed_dir,
# но без зависимости от рабочего каталога — нужно совпадать и в Docker, и при `make dev-*`)
_SHIPPED_CA_BUNDLE = (
    Path(__file__).resolve().parents[3] / "deploy" / "certs" / "russian_trusted_root_ca.crt"
)

# Секрет вебхука из .env.example: с ним прод не стартует
_EXAMPLE_WEBHOOK_SECRET = "change-me-please"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # --- MAX ---
    max_bot_token: str = ""  # токен бота от организаторов; НИКОГДА не коммитить
    max_api_base: str = "https://platform-api2.max.ru"
    max_webhook_secret: str = ""  # ^[a-zA-Z0-9_-]{5,256}$, приходит в X-Max-Bot-Api-Secret
    public_base_url: str = ""  # https://<домен>, нужен для вебхука и ссылки на мини-апп
    # путь к корню Russian Trusted Root CA; пусто — подхватится deploy/certs/, если он есть,
    # иначе httpx проверяет только по certifi. Задайте явно, чтобы указать другой путь
    max_ca_bundle: str = ""
    # публичное имя бота: кнопка open_app запускает мини-апп этого бота (поле web_app).
    # Пусто — кнопку «Открыть календарь» вызывающий код не показывает
    max_bot_username: str = ""

    # --- Приложение ---
    app_env: str = "dev"  # dev | prod
    database_url: str = "sqlite+aiosqlite:///./data/app.db"
    content_dir: str = "../content"  # где лежат texts.yaml и справочники календаря
    seed_dir: str = "../seed"  # тестовые данные для MockDataSource
    capture_updates: bool = False  # dev: сохранять сырые апдейты в data/captured_updates/
    data_mode: str = "mock"  # mock | real — какой адаптер внешних данных использовать
    initdata_max_age_seconds: int = 86400
    # dev: принимать X-Max-Init-Data: dev вместо настоящей подписи. В проде всегда false
    allow_dev_initdata: bool = False

    # --- Календарь ---
    # user_id MAX через запятую: им доступна /demo_remind. Пусто — админов нет
    admin_ids: Annotated[list[int], NoDecode] = []
    # «Написать нам» и политика обработки данных (D13). Пусто — кнопка и строка скрыты
    support_url: str = ""
    privacy_url: str = ""
    # фоновый цикл напоминаний в lifespan; false — для тестов и отладки
    scheduler_enabled: bool = True
    # справочники аналитика — имена файлов относительно content_dir
    obligations_file: str = "obligations.yaml"
    workdays_file: str = "workdays.yaml"
    nds_file: str = "nds.yaml"

    @field_validator("admin_ids", mode="before")
    @classmethod
    def _parse_admin_ids(cls, value: object) -> object:
        """ADMIN_IDS=1,2,3 → [1, 2, 3]; пустая строка → []."""
        if isinstance(value, str):
            return [int(part) for part in value.split(",") if part.strip()]
        return value

    @property
    def obligations_path(self) -> Path:
        return Path(self.content_dir) / self.obligations_file

    @property
    def workdays_path(self) -> Path:
        return Path(self.content_dir) / self.workdays_file

    @property
    def nds_path(self) -> Path:
        return Path(self.content_dir) / self.nds_file

    @model_validator(mode="after")
    def _forbid_dev_initdata_in_prod(self) -> "Settings":
        """Лучше не подняться, чем молча принимать непроверенный initData в проде."""
        if self.app_env == "prod" and self.allow_dev_initdata:
            raise ValueError("ALLOW_DEV_INITDATA=true недопустим при APP_ENV=prod")
        return self

    @model_validator(mode="after")
    def _forbid_example_webhook_secret_in_prod(self) -> "Settings":
        """С пустым секретом или секретом из примера вебхук в проде открыт кому угодно."""
        if self.app_env == "prod" and self.max_webhook_secret.strip() in (
            "",
            _EXAMPLE_WEBHOOK_SECRET,
        ):
            raise ValueError(
                "При APP_ENV=prod задайте свой MAX_WEBHOOK_SECRET: "
                "пустой или change-me-please недопустим"
            )
        return self

    @model_validator(mode="after")
    def _default_ca_bundle(self) -> "Settings":
        """MAX_CA_BUNDLE не задан явно — подхватить сертификат, если он есть в репозитории."""
        if not self.max_ca_bundle and _SHIPPED_CA_BUNDLE.is_file():
            self.max_ca_bundle = str(_SHIPPED_CA_BUNDLE)
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
