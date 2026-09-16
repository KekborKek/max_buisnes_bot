from typing import Protocol


class DataSource(Protocol):
    """Интерфейс внешних данных. Методы добавляются под выбранную идею продукта."""

    is_mock: bool

    async def lookup_company(self, inn: str) -> dict | None: ...
