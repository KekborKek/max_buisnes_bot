"""Адаптеры внешних данных. Ядро продукта не должно зависеть от чужого API:
есть интерфейс и две реализации — Mock (демо, явно помечен) и Real (реальная интеграция)."""

from app.core.adapters.base import DataSource
from app.core.adapters.mock import MockDataSource
from app.core.config import get_settings


def get_data_source() -> DataSource:
    if get_settings().data_mode == "real":
        raise NotImplementedError("RealDataSource ещё не реализован — см. docs/decisions.md")
    return MockDataSource()


__all__ = ["DataSource", "get_data_source"]
