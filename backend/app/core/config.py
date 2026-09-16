"""Настройки приложения. Все значения берутся из переменных окружения (.env)."""

from functools import lru_cache

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # --- MAX ---
    max_bot_token: str = ""  # токен бота от организаторов; НИКОГДА не коммитить
    max_api_base: str = "https://platform-api2.max.ru"
    max_webhook_secret: str = ""  # ^[a-zA-Z0-9_-]{5,256}$, приходит в X-Max-Bot-Api-Secret
    public_base_url: str = ""  # https://<домен>, нужен для вебхука и ссылки на мини-апп

    # --- Приложение ---
    app_env: str = "dev"  # dev | prod
    database_url: str = "sqlite+aiosqlite:///./data/app.db"
    content_dir: str = "../content"  # где лежит texts.yaml
    seed_dir: str = "../seed"  # тестовые данные для MockDataSource
    capture_updates: bool = False  # dev: сохранять сырые апдейты в data/captured_updates/
    data_mode: str = "mock"  # mock | real — какой адаптер внешних данных использовать
    initdata_max_age_seconds: int = 86400
    # dev: принимать X-Max-Init-Data: dev вместо настоящей подписи. В проде всегда false
    allow_dev_initdata: bool = False

    @model_validator(mode="after")
    def _forbid_dev_initdata_in_prod(self) -> "Settings":
        """Лучше не подняться, чем молча принимать непроверенный initData в проде."""
        if self.app_env == "prod" and self.allow_dev_initdata:
            raise ValueError("ALLOW_DEV_INITDATA=true недопустим при APP_ENV=prod")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
