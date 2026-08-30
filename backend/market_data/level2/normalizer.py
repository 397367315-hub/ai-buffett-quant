"""Normalize NumCat rows into the provider-neutral Level-2 dataclasses."""

from __future__ import annotations

import json
import math
import re
from datetime import date, datetime, time, timedelta
from typing import Any, Iterable
from zoneinfo import ZoneInfo

from services.data_collector import normalize_stock_code

from .models import BookLevel, OrderBookSnapshot, OrderTick, TradeTick


SHANGHAI = ZoneInfo("Asia/Shanghai")
_MISSING = object()


def row_from_fields(fields: Iterable[str] | None, row: Any) -> dict[str, Any]:
    """Turn both documented dict rows and ``fields + items`` arrays into dicts."""
    if isinstance(row, dict):
        return dict(row)
    names = [str(item) for item in (fields or [])]
    if isinstance(row, (list, tuple)):
        return {name: row[index] if index < len(row) else None for index, name in enumerate(names)}
    return {}


def _lookup(row: dict[str, Any], *names: str, default: Any = None) -> Any:
    lowered = {str(key).strip().lower().replace("-", "_"): value for key, value in row.items()}
    for name in names:
        key = str(name).strip().lower().replace("-", "_")
        if key in lowered:
            return lowered[key]
    return default


def as_number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        result = float(value)
        return result if math.isfinite(result) else None
    text = str(value).strip().replace(",", "")
    if not text or text in {"-", "--", "null", "None", "N/A", "nan"}:
        return None
    text = text.replace("%", "")
    try:
        result = float(text)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def normalize_symbol(value: Any) -> str:
    """Accept six-digit and common exchange-suffixed symbols, return six digits."""
    text = str(value or "").strip().upper()
    text = text.replace("XSHG", "SH").replace("XSHE", "SZ")
    match = re.fullmatch(r"(?:([A-Z]{2})[.:])?(\d{6})(?:\.([A-Z]{2}))?", text)
    if not match:
        raise ValueError("Level-2 requires a valid six-digit A-share symbol")
    prefix, code, suffix = match.groups()
    declared = prefix or suffix
    if declared == "SH" and not code.startswith(("6", "9")):
        raise ValueError(f"股票代码与交易所不匹配: {code} 应为 SZ/BJ")
    if declared == "SZ" and not code.startswith(("0", "2", "3")):
        raise ValueError(f"股票代码与交易所不匹配: {code} 应为 SH/BJ")
    if declared == "BJ" and not code.startswith(("4", "8", "9")):
        raise ValueError(f"股票代码与交易所不匹配: {code} 应为 BJ")
    return normalize_stock_code(code)


def parse_trade_date(value: Any) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value or "").strip()
    if re.fullmatch(r"\d{8}", text):
        return datetime.strptime(text, "%Y%m%d").date()
    return date.fromisoformat(text[:10])


def parse_timestamp(value: Any, trade_date: date) -> datetime:
    """Parse HHMMSSmmm, common colon forms, and ISO timestamps as local time."""
    if isinstance(value, datetime):
        parsed = value
        if parsed.tzinfo is not None:
            parsed = parsed.astimezone(SHANGHAI).replace(tzinfo=None)
        return parsed
    text = str(value or "").strip()
    if not text:
        return datetime.combine(trade_date, time.min)
    if "T" in text or " " in text and len(text) > 10:
        candidate = text.replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(candidate)
            if parsed.tzinfo is not None:
                parsed = parsed.astimezone(SHANGHAI).replace(tzinfo=None)
            return parsed
        except ValueError:
            pass
    compact = re.sub(r"[^0-9]", "", text)
    # A source may include YYYYMMDD before HHMMSSmmm. Keep the time portion
    # explicitly; taking the last nine characters would corrupt 14-digit
    # YYYYMMDDHHMMSS values.
    if len(compact) >= 14 and compact[:8].isdigit():
        compact = compact[8:]
    if len(compact) in {4, 6, 9}:
        hours = int(compact[:2])
        minutes = int(compact[2:4])
        seconds = int(compact[4:6]) if len(compact) >= 6 else 0
        millis = int(compact[6:9]) if len(compact) == 9 else 0
        return datetime.combine(trade_date, time(hours, minutes, seconds, millis * 1000))
    raise ValueError(f"无法解析 Level-2 时间: {text}")


def normalize_side(value: Any) -> str | None:
    text = str(value or "").strip().upper()
    if not text:
        return None
    if text in {"B", "BUY", "BID", "BUYING", "买", "买入", "主动买", "主动买入", "1"}:
        return "buy"
    if text in {"S", "SELL", "ASK", "SELLING", "卖", "卖出", "主动卖", "主动卖出", "2", "-1"}:
        return "sell"
    if text in {"N", "NEUTRAL", "中性", "不明", "未知", "0"}:
        return "neutral"
    return None


def _base_date(row: dict[str, Any], supplied_date: date | None) -> date:
    value = _lookup(row, "tradedate", "trade_date", "date")
    if value not in (None, ""):
        return parse_trade_date(value)
    if supplied_date is None:
        raise ValueError("Level-2 row is missing tradedate")
    return supplied_date


def normalize_trade_row(
    row: Any,
    fields: Iterable[str] | None = None,
    *,
    symbol: str | None = None,
    trade_date: date | None = None,
    source: str = "numcat",
) -> TradeTick:
    raw = row_from_fields(fields, row)
    resolved_date = _base_date(raw, trade_date)
    resolved_symbol = normalize_symbol(symbol or _lookup(raw, "symbol", "stock_code", "code"))
    price = as_number(_lookup(raw, "price"))
    volume = as_number(_lookup(raw, "volume", "qty", "quantity"))
    amount = as_number(_lookup(raw, "amount", "turnover"))
    if amount is None and price is not None and volume is not None:
        amount = price * volume
    raw_side = _lookup(raw, "bs_flag", "side", "direction")
    return TradeTick(
        symbol=resolved_symbol,
        trade_date=resolved_date,
        timestamp=parse_timestamp(_lookup(raw, "time", "timestamp", "trade_time"), resolved_date),
        trade_id=_string_id(_lookup(raw, "trade_id", "trade_no", "seq")),
        price=price,
        volume=volume,
        amount=amount,
        side=normalize_side(raw_side),
        direction_method="explicit_bs_flag" if normalize_side(raw_side) else "unclassified",
        direction_confidence=0.95 if normalize_side(raw_side) else 0.0,
        trade_code=_string_id(_lookup(raw, "trade_code", "trade_type")),
        buy_order_id=_string_id(_lookup(raw, "buy_order_id", "buy_order_no")),
        sell_order_id=_string_id(_lookup(raw, "sell_order_id", "sell_order_no")),
        source=source,
        raw=_json_safe(raw),
    )


def normalize_order_row(
    row: Any,
    fields: Iterable[str] | None = None,
    *,
    symbol: str | None = None,
    trade_date: date | None = None,
    source: str = "numcat",
) -> OrderTick:
    raw = row_from_fields(fields, row)
    resolved_date = _base_date(raw, trade_date)
    resolved_symbol = normalize_symbol(symbol or _lookup(raw, "symbol", "stock_code", "code"))
    price = as_number(_lookup(raw, "price"))
    volume = as_number(_lookup(raw, "volume", "qty", "quantity"))
    amount = as_number(_lookup(raw, "amount", "turnover"))
    if amount is None and price is not None and volume is not None:
        amount = price * volume
    return OrderTick(
        symbol=resolved_symbol,
        trade_date=resolved_date,
        timestamp=parse_timestamp(_lookup(raw, "time", "timestamp", "order_time"), resolved_date),
        order_id=_string_id(_lookup(raw, "order_id", "seq")),
        price=price,
        volume=volume,
        amount=amount,
        side=normalize_side(_lookup(raw, "side", "bs_flag", "direction")),
        order_type=_string_id(_lookup(raw, "order_type", "type")),
        order_no=_string_id(_lookup(raw, "order_no", "exchange_order_no", "order_number")),
        source=source,
        raw=_json_safe(raw),
    )


def normalize_quote_row(
    row: Any,
    fields: Iterable[str] | None = None,
    *,
    symbol: str | None = None,
    trade_date: date | None = None,
    source: str = "numcat",
) -> OrderBookSnapshot:
    raw = row_from_fields(fields, row)
    resolved_date = _base_date(raw, trade_date)
    resolved_symbol = normalize_symbol(symbol or _lookup(raw, "symbol", "stock_code", "code"))
    bids: list[BookLevel] = []
    asks: list[BookLevel] = []
    for level in range(1, 11):
        bid_price = as_number(_lookup(raw, f"bid{level}", f"bid_{level}"))
        bid_volume = as_number(_lookup(raw, f"bid_vol{level}", f"bid_volume{level}", f"bid_vol_{level}"))
        ask_price = as_number(_lookup(raw, f"ask{level}", f"ask_{level}"))
        ask_volume = as_number(_lookup(raw, f"ask_vol{level}", f"ask_volume{level}", f"ask_vol_{level}"))
        bids.append(BookLevel(bid_price, bid_volume, level))
        asks.append(BookLevel(ask_price, ask_volume, level))
    return OrderBookSnapshot(
        symbol=resolved_symbol,
        trade_date=resolved_date,
        timestamp=parse_timestamp(_lookup(raw, "time", "timestamp", "quote_time"), resolved_date),
        last_price=as_number(_lookup(raw, "close", "last_price", "price")),
        open_price=as_number(_lookup(raw, "open", "open_price")),
        high_price=as_number(_lookup(raw, "high", "high_price")),
        low_price=as_number(_lookup(raw, "low", "low_price")),
        pre_close=as_number(_lookup(raw, "pre_close", "preclose", "prev_close")),
        volume=as_number(_lookup(raw, "volume", "total_volume")),
        amount=as_number(_lookup(raw, "amount", "turnover", "total_amount")),
        bids=bids,
        asks=asks,
        source=source,
        raw=_json_safe(raw),
    )


def _string_id(value: Any) -> str | None:
    if value in (None, ""):
        return None
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    try:
        return json.loads(json.dumps(value, ensure_ascii=True))
    except (TypeError, ValueError):
        return str(value)


def timestamp_key(timestamp: datetime, suffix: str = "") -> str:
    """Stable fallback ID for rows where the source omits a sequence number."""
    import hashlib

    value = f"{timestamp.isoformat()}|{suffix}".encode("utf-8")
    return hashlib.sha1(value).hexdigest()
