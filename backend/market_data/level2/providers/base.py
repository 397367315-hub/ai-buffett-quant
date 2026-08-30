"""Abstract provider contract for historical and optional live Level-2 data."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date
from typing import Any, AsyncIterator, Literal


Level2DataType = Literal["trade", "order", "quote"]


@dataclass(slots=True, frozen=True)
class ProviderCapabilities:
    supports_history_trade: bool = False
    supports_history_order: bool = False
    supports_history_quote: bool = False
    supports_realtime_polling: bool = False
    supports_streaming: bool = False
    supports_bulk_symbols: bool = False

    def as_dict(self) -> dict[str, bool]:
        return {
            "supports_history_trade": self.supports_history_trade,
            "supports_history_order": self.supports_history_order,
            "supports_history_quote": self.supports_history_quote,
            "supports_realtime_polling": self.supports_realtime_polling,
            "supports_streaming": self.supports_streaming,
            "supports_bulk_symbols": self.supports_bulk_symbols,
        }


@dataclass(slots=True)
class Level2Page:
    data_type: Level2DataType
    fields: list[str] = field(default_factory=list)
    rows: list[dict[str, Any]] = field(default_factory=list)
    page_size: int = 0
    next_cursor: str | None = None
    has_more: bool = False
    raw_metadata: dict[str, Any] = field(default_factory=dict)


class Level2Provider(ABC):
    """Stable interface used by the fetcher and service."""

    name = "unknown"

    @property
    @abstractmethod
    def configured(self) -> bool:
        """Whether credentials and required runtime configuration are present."""

    @property
    @abstractmethod
    def capabilities(self) -> ProviderCapabilities:
        """Advertised capabilities; do not infer streaming from history APIs."""

    @abstractmethod
    async def fetch_page(
        self,
        data_type: Level2DataType,
        symbol: str,
        trade_date: date,
        *,
        cursor: str | None = None,
        start_time: str | None = None,
        end_time: str | None = None,
        page_size: int | None = None,
    ) -> Level2Page:
        """Fetch one bounded page. Cursor iteration belongs to the fetcher."""

    async def iter_pages(
        self,
        data_type: Level2DataType,
        symbol: str,
        trade_date: date,
        *,
        cursor: str | None = None,
        start_time: str | None = None,
        end_time: str | None = None,
        page_size: int | None = None,
        max_pages: int = 200,
    ) -> AsyncIterator[Level2Page]:
        """Generic guarded cursor iterator shared by providers."""
        current = cursor
        seen: set[str] = set()
        for _ in range(max(1, int(max_pages))):
            page = await self.fetch_page(
                data_type,
                symbol,
                trade_date,
                cursor=current,
                start_time=start_time,
                end_time=end_time,
                page_size=page_size,
            )
            yield page
            if not page.has_more or not page.next_cursor:
                return
            if page.next_cursor in seen or page.next_cursor == current:
                raise RuntimeError("Level-2 provider returned a repeated cursor")
            seen.add(page.next_cursor)
            current = page.next_cursor
        raise RuntimeError("Level-2 provider exceeded the page safety limit")
