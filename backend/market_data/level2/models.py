"""Provider-neutral Level-2 records.

The upstream API returns rows as a field list plus a two-dimensional item
array.  These small dataclasses are the only shapes consumed by the
microstructure engine, so a future provider can be added without changing the
calculation layer.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any


@dataclass(slots=True, frozen=True)
class BookLevel:
    price: float | None
    volume: float | None
    level: int

    def as_dict(self) -> dict[str, Any]:
        return {"price": self.price, "volume": self.volume, "level": self.level}


@dataclass(slots=True)
class TradeTick:
    symbol: str
    trade_date: date
    timestamp: datetime
    trade_id: str | None = None
    price: float | None = None
    volume: float | None = None
    amount: float | None = None
    side: str | None = None
    direction_method: str = "unclassified"
    direction_confidence: float = 0.0
    trade_code: str | None = None
    buy_order_id: str | None = None
    sell_order_id: str | None = None
    source: str = "numcat"
    raw: dict[str, Any] = field(default_factory=dict)

@dataclass(slots=True)
class OrderTick:
    symbol: str
    trade_date: date
    timestamp: datetime
    order_id: str | None = None
    price: float | None = None
    volume: float | None = None
    amount: float | None = None
    side: str | None = None
    order_type: str | None = None
    order_no: str | None = None
    source: str = "numcat"
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class OrderBookSnapshot:
    symbol: str
    trade_date: date
    timestamp: datetime
    last_price: float | None = None
    open_price: float | None = None
    high_price: float | None = None
    low_price: float | None = None
    pre_close: float | None = None
    volume: float | None = None
    amount: float | None = None
    bids: list[BookLevel] = field(default_factory=list)
    asks: list[BookLevel] = field(default_factory=list)
    source: str = "numcat"
    raw: dict[str, Any] = field(default_factory=dict)
