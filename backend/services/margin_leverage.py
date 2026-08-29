"""Persistent A-share margin-financing risk and leverage analytics.

The EastMoney disclosure is end-of-day data, normally published on T+1.
Every public payload therefore carries an explicit source date and is never
labelled realtime. Missing history remains unavailable instead of becoming a
zero-risk score.
"""

from __future__ import annotations

import asyncio
import math
from datetime import date, datetime, timedelta
from statistics import median, pstdev
from typing import Any, Iterable

from sqlalchemy import and_, asc, desc, func, or_, select
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from database import async_session
from models import (
    MarginMarketDaily,
    MarginSectorDaily,
    MarginStockDaily,
    MarketSentimentDaily,
    StockDailyBar,
    StockLeverageMetric,
    StockUniverseSnapshot,
)
from quant.market_cache import load_quant_market_snapshot, save_quant_market_snapshot
from services.data_collector import (
    as_int,
    as_optional_float,
    collector,
    normalize_stock_code,
    shanghai_now,
)


EQUITY_PREFIXES = (
    "000", "001", "002", "003",
    "300", "301", "302",
    "600", "601", "603", "605", "688", "689",
    "4", "8", "92",
)
SECTOR_TYPE_CODES = {"region": "004", "industry": "005", "concept": "006"}
SECTOR_TYPE_NAMES = {value: key for key, value in SECTOR_TYPE_CODES.items()}
MARGIN_DISCLOSURE_NOTE = "两融为T日收盘数据，通常T+1披露，不是盘中实时数据。"
REFERENCE_LINE_NOTE = (
    "5%仅作为普通个股的直观参考线，不代表所有股票统一安全线。"
    "系统同时结合个股自身历史分位、融资增速、成交结构和价格表现评估风险。"
)
EXPECTED_MARGIN_MARKETS = frozenset({"融资融券_沪证", "融资融券_深证"})
MAX_SNAPSHOT_BALANCE_DEVIATION_PCT = 2.0


def _finite(value: Any) -> float | None:
    if value in (None, "", "-") or isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _round(value: Any, digits: int = 2) -> float | None:
    number = _finite(value)
    return round(number, digits) if number is not None else None


def _clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


def _date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value or "")[:10])
    except (TypeError, ValueError):
        return None


def _pct_change(current: Any, previous: Any) -> float | None:
    current_value = _finite(current)
    previous_value = _finite(previous)
    if current_value is None or previous_value in (None, 0):
        return None
    return (current_value / previous_value - 1.0) * 100.0


def _percentile_rank(values: Iterable[Any], current: Any, minimum_samples: int) -> float | None:
    clean = sorted(value for raw in values if (value := _finite(raw)) is not None)
    target = _finite(current)
    if target is None or len(clean) < minimum_samples:
        return None
    below = sum(value < target for value in clean)
    equal = sum(value == target for value in clean)
    return (below + equal * 0.5) / len(clean) * 100.0


def _piecewise(value: Any, points: list[tuple[float, float]]) -> float | None:
    number = _finite(value)
    if number is None:
        return None
    ordered = sorted(points)
    if number <= ordered[0][0]:
        return ordered[0][1]
    if number >= ordered[-1][0]:
        return ordered[-1][1]
    for (left_x, left_y), (right_x, right_y) in zip(ordered, ordered[1:]):
        if left_x <= number <= right_x:
            span = right_x - left_x
            ratio = (number - left_x) / span if span else 0.0
            return left_y + (right_y - left_y) * ratio
    return None


def _equity_code(value: Any) -> str | None:
    candidate = str(value or "").strip().zfill(6)
    if len(candidate) != 6 or not candidate.isdigit() or not candidate.startswith(EQUITY_PREFIXES):
        return None
    try:
        return normalize_stock_code(candidate)
    except ValueError:
        return None


def financing_ratio_level(value: Any) -> dict[str, Any]:
    ratio = _finite(value)
    if ratio is None:
        return {"level": "暂无两融风险评分", "severity": "unavailable", "score": None}
    if ratio >= 25:
        level, severity = "极端/监管阈值", "critical"
    elif ratio >= 20:
        level, severity = "监管风险观察区", "critical"
    elif ratio >= 15:
        level, severity = "极高风险", "very_high"
    elif ratio >= 12:
        level, severity = "高风险", "high"
    elif ratio >= 8:
        level, severity = "高杠杆", "elevated"
    elif ratio >= 5:
        level, severity = "偏高", "attention"
    elif ratio >= 3:
        level, severity = "正常", "normal"
    else:
        level, severity = "低杠杆", "low"
    score = _piecewise(ratio, [(0, 0), (3, 20), (5, 35), (8, 55), (12, 70), (15, 82), (20, 92), (25, 100)])
    return {"level": level, "severity": severity, "score": _round(score, 1)}


def lmi_level(score: Any) -> str | None:
    value = _finite(score)
    if value is None:
        return None
    if value <= 30:
        return "低杠杆"
    if value <= 50:
        return "正常"
    if value <= 65:
        return "升温"
    if value <= 80:
        return "偏热"
    if value <= 90:
        return "高拥挤"
    return "极端杠杆环境"


def lri_level(score: Any) -> str | None:
    value = _finite(score)
    if value is None:
        return None
    if value <= 30:
        return "低"
    if value <= 50:
        return "正常"
    if value <= 65:
        return "关注"
    if value <= 80:
        return "偏高"
    if value <= 90:
        return "高风险"
    return "极端拥挤"


def _component(label: str, raw: Any, score: Any, weight: float, explanation: str) -> dict[str, Any]:
    raw_number = _finite(raw)
    return {
        "label": label,
        "raw": _round(raw_number, 4) if raw_number is not None else raw,
        "score": _round(score, 2),
        "weight": weight,
        "contribution": _round((_finite(score) or 0.0) * weight, 2) if score is not None else None,
        "available": score is not None,
        "explanation": explanation,
    }


def _weighted_score(components: dict[str, dict[str, Any]], minimum_coverage: float = 0.75) -> tuple[float | None, float]:
    available_weight = sum(item["weight"] for item in components.values() if item.get("available"))
    if available_weight < minimum_coverage:
        return None, available_weight * 100.0
    weighted = sum(
        float(item["score"]) * float(item["weight"])
        for item in components.values()
        if item.get("available")
    )
    # Keep the documented relative weights when one non-critical source is
    # temporarily absent, while exposing the exact coverage to the client.
    return _clamp(weighted / available_weight), available_weight * 100.0


def _relation(
    price_change_5d: Any,
    financing_change_5d: Any,
    percentile_250: Any,
) -> tuple[str, float, str]:
    price = _finite(price_change_5d)
    financing = _finite(financing_change_5d)
    percentile = _finite(percentile_250)
    if price is None or financing is None:
        return "数据不足", 50.0, "价格或融资五日变化不足，暂不做背离定性。"
    if price >= 2 and financing >= 3 and (percentile or 0) >= 80:
        return "杠杆追涨", 85.0, "股价上涨同时融资快速增加且历史分位偏高，上涨对新增杠杆的依赖增强。"
    if price <= -2 and financing >= 2:
        return "越跌越补", 92.0, "股价下跌而融资余额继续增加，潜在浮亏融资盘正在积累。"
    if price <= -2 and financing <= -3:
        return "踩踏去杠杆", 88.0, "价格与融资余额同步快速下降，需防范偿还压力放大价格波动。"
    if price >= -0.5 and financing <= -2:
        return "健康去杠杆", 18.0, "价格保持稳定而融资余额下降，行情对杠杆资金的依赖降低。"
    if price >= 1 and -1 <= financing <= 2.5:
        return "健康趋势", 28.0, "价格上涨而融资仅温和变化，杠杆资金暂未明显主导行情。"
    return "同步/中性", 48.0, "价格与融资变化暂未形成显著背离，继续观察方向和持续性。"


class MarginLeverageService:
    PREWARM_LIMIT = 120
    METRIC_CALCULATION_CHUNK_SIZE = 100

    def __init__(self) -> None:
        self._sync_lock = asyncio.Lock()
        self._refresh_task: asyncio.Task | None = None
        self._stock_locks: dict[str, asyncio.Lock] = {}
        self._sector_directory: dict[str, str] = {}
        self._status: dict[str, Any] = {
            "status": "idle",
            "progress": 0,
            "stage": "等待刷新",
            "started_at": None,
            "finished_at": None,
            "data_date": None,
            "error": None,
        }

    @staticmethod
    def _insert(session, model):
        return postgresql_insert(model) if session.get_bind().dialect.name == "postgresql" else sqlite_insert(model)

    @classmethod
    async def _upsert(cls, model, rows: list[dict[str, Any]], keys: list[str], batch_size: int = 300) -> int:
        if not rows:
            return 0
        async with async_session() as session:
            for start in range(0, len(rows), batch_size):
                batch = rows[start:start + batch_size]
                statement = cls._insert(session, model).values(batch)
                updates = {
                    column.name: getattr(statement.excluded, column.name)
                    for column in model.__table__.columns
                    if column.name not in {"id", *keys}
                }
                await session.execute(statement.on_conflict_do_update(index_elements=keys, set_=updates))
            await session.commit()
        return len(rows)

    def _set_status(self, *, progress: int | None = None, stage: str | None = None, **values: Any) -> None:
        if progress is not None:
            self._status["progress"] = max(0, min(100, int(progress)))
        if stage is not None:
            self._status["stage"] = stage
        self._status.update(values)

    def refresh_status(self) -> dict[str, Any]:
        return {**self._status, "running": bool(self._refresh_task and not self._refresh_task.done())}

    @staticmethod
    def _normalise_stock_row(
        raw: dict[str, Any],
        *,
        bar: Any = None,
        sector_name: str | None = None,
    ) -> dict[str, Any] | None:
        code = _equity_code(raw.get("SCODE"))
        trade_date = _date(raw.get("DATE"))
        if code is None or trade_date is None:
            return None
        financing_balance = as_int(raw.get("RZYE"))
        float_market_cap = as_int(raw.get("SZ")) or None
        calculated_ratio = (financing_balance / float_market_cap * 100.0) if float_market_cap else None
        source_ratio = _finite(raw.get("RZYEZB"))
        financing_ratio = source_ratio if source_ratio is not None else calculated_ratio
        bar_amount = bar.get("amount") if isinstance(bar, dict) else getattr(bar, "amount", None)
        bar_turnover = bar.get("turnover") if isinstance(bar, dict) else getattr(bar, "turnover", None)
        turnover_amount = int(bar_amount) if bar_amount is not None else None
        turnover_rate = _finite(bar_turnover)
        financing_buy = as_int(raw.get("RZMRE"))
        financing_buy_ratio = (financing_buy / turnover_amount * 100.0) if turnover_amount else None
        secucode = str(raw.get("SECUCODE") or "")
        exchange = secucode.rsplit(".", 1)[-1] if "." in secucode else ("SH" if code.startswith("6") else "SZ")
        return {
            "trade_date": trade_date,
            "stock_code": code,
            "stock_name": str(raw.get("SECNAME") or code),
            "exchange": exchange,
            "trade_market": str(raw.get("TRADE_MARKET") or "") or None,
            "sector_name": sector_name or None,
            "financing_balance": financing_balance,
            "financing_buy": financing_buy,
            "financing_repay": as_int(raw.get("RZCHE")),
            "financing_net_buy": as_int(raw.get("RZJME")),
            "financing_net_buy_3d": as_int(raw.get("RZJME3D")),
            "financing_net_buy_5d": as_int(raw.get("RZJME5D")),
            "financing_net_buy_10d": as_int(raw.get("RZJME10D")),
            "securities_balance": as_int(raw.get("RQYE")),
            # The source exposes share volume for these two fields. Their unit
            # remains shares in storage and is labelled as such in the API.
            "securities_sell": as_int(raw.get("RQMCL")),
            "securities_repay": as_int(raw.get("RQCHL")),
            "margin_balance": as_int(raw.get("RZRQYE")),
            "close_price": _finite(raw.get("SPJ")),
            "pct_change": _finite(raw.get("ZDF")),
            "price_change_3d": _finite(raw.get("RCHANGE3DCP")),
            "price_change_5d": _finite(raw.get("RCHANGE5DCP")),
            "price_change_10d": _finite(raw.get("RCHANGE10DCP")),
            "turnover_amount": turnover_amount,
            "turnover_rate": turnover_rate,
            "float_market_cap": float_market_cap,
            "financing_ratio": financing_ratio,
            "financing_buy_ratio": financing_buy_ratio,
            "source": "eastmoney_RPTA_WEB_RZRQ_GGMX",
            "updated_at": datetime.utcnow(),
        }

    @staticmethod
    def _normalise_price_row(code: str, name: str, raw: dict[str, Any], source: str) -> dict[str, Any] | None:
        trade_date = _date(raw.get("trade_date"))
        if trade_date is None:
            return None
        return {
            "stock_code": code,
            "stock_name": name or None,
            "market": "SH" if code.startswith("6") else "BJ" if code.startswith(("4", "8", "92")) else "SZ",
            "trade_date": trade_date,
            "open_price": _finite(raw.get("open")),
            "close_price": _finite(raw.get("close")),
            "high_price": _finite(raw.get("high")),
            "low_price": _finite(raw.get("low")),
            "volume": int(value) if (value := _finite(raw.get("volume"))) is not None else None,
            "amount": int(value) if (value := _finite(raw.get("amount"))) is not None else None,
            "amplitude": _finite(raw.get("amplitude")),
            "change_pct": _finite(raw.get("change_pct")),
            "change_amount": _finite(raw.get("change_amount")),
            "turnover": _finite(raw.get("turnover")),
            "source": source,
            "updated_at": datetime.utcnow(),
        }

    async def _bar_map_for_date(self, target_date: date, codes: list[str]) -> dict[str, StockDailyBar]:
        if not codes:
            return {}
        async with async_session() as session:
            rows = list((await session.execute(
                select(StockDailyBar).where(
                    StockDailyBar.trade_date == target_date,
                    StockDailyBar.stock_code.in_(codes),
                )
            )).scalars().all())
        return {row.stock_code: row for row in rows}

    async def _bar_map_for_stock(self, code: str) -> dict[date, StockDailyBar]:
        async with async_session() as session:
            rows = list((await session.execute(
                select(StockDailyBar)
                .where(StockDailyBar.stock_code == code)
                .order_by(StockDailyBar.trade_date)
            )).scalars().all())
        return {row.trade_date: row for row in rows}

    async def _sector_map(
        self,
        target_date: date,
        codes: list[str],
        *,
        allow_live_fetch: bool = False,
    ) -> dict[str, str]:
        if not codes:
            return {}
        async with async_session() as session:
            latest = (await session.execute(
                select(func.max(StockUniverseSnapshot.trade_date)).where(
                    StockUniverseSnapshot.trade_date <= target_date
                )
            )).scalar_one_or_none()
            rows = list((await session.execute(
                select(StockUniverseSnapshot.stock_code, StockUniverseSnapshot.industry).where(
                    StockUniverseSnapshot.trade_date == latest,
                    StockUniverseSnapshot.stock_code.in_(codes),
                )
            )).all()) if latest else []
        mapping = {code: industry for code, industry in rows if industry}
        for code in codes:
            if code not in mapping and self._sector_directory.get(code):
                mapping[code] = self._sector_directory[code]
        if len(mapping) >= len(codes) * 0.8:
            return mapping
        cached = await load_quant_market_snapshot()
        for stock in cached.get("stocks") or []:
            code = str(stock.get("code") or "")
            sector = str(stock.get("sector") or "").strip()
            if code in codes and sector and code not in mapping:
                mapping[code] = sector
                self._sector_directory[code] = sector
        if len(mapping) < len(codes) * 0.8 and allow_live_fetch:
            fresh = await collector.fetch_quant_market_snapshot(include_special=True)
            if fresh.get("complete"):
                await save_quant_market_snapshot(fresh)
            for stock in fresh.get("stocks") or []:
                code = str(stock.get("code") or "")
                sector = str(stock.get("sector") or "").strip()
                if code and sector:
                    self._sector_directory[code] = sector
                    if code in codes and code not in mapping:
                        mapping[code] = sector
        return mapping

    async def _market_turnover_map(self, dates: list[date]) -> dict[date, int]:
        if not dates:
            return {}
        async with async_session() as session:
            rows = list((await session.execute(
                select(MarketSentimentDaily.trade_date, MarketSentimentDaily.market_amount).where(
                    MarketSentimentDaily.trade_date.in_(dates)
                )
            )).all())
        return {trade_date: int(amount) for trade_date, amount in rows if amount is not None}

    @staticmethod
    def _audit_stock_snapshot(
        raw_rows: list[dict[str, Any]],
        target_date: date,
        official_market_row: dict[str, Any],
    ) -> dict[str, Any]:
        valid = [row for row in raw_rows if _date(row.get("DATE")) == target_date]
        markets = sorted({str(row.get("MARKET") or "").strip() for row in valid if row.get("MARKET")})
        missing_markets = sorted(EXPECTED_MARGIN_MARKETS.difference(markets))
        snapshot_financing_balance = sum(as_int(row.get("RZYE")) for row in valid)
        official_financing_balance = as_int(official_market_row.get("financing_balance"))
        deviation_pct = (
            abs(snapshot_financing_balance - official_financing_balance)
            / official_financing_balance * 100.0
            if official_financing_balance > 0 else None
        )
        passed = bool(
            valid
            and not missing_markets
            and deviation_pct is not None
            and deviation_pct <= MAX_SNAPSHOT_BALANCE_DEVIATION_PCT
        )
        reasons = []
        if not valid:
            reasons.append("目标披露日没有个股记录")
        if missing_markets:
            reasons.append(f"缺少市场：{'、'.join(missing_markets)}")
        if deviation_pct is None:
            reasons.append("官方全市场融资余额不可用")
        elif deviation_pct > MAX_SNAPSHOT_BALANCE_DEVIATION_PCT:
            reasons.append(
                f"个股汇总与官方全市场融资余额偏差{deviation_pct:.2f}%，"
                f"超过{MAX_SNAPSHOT_BALANCE_DEVIATION_PCT:.0f}%阈值"
            )
        return {
            "passed": passed,
            "trade_date": target_date.isoformat(),
            "record_count": len(valid),
            "markets": markets,
            "missing_markets": missing_markets,
            "snapshot_financing_balance": snapshot_financing_balance,
            "official_financing_balance": official_financing_balance,
            "balance_deviation_pct": _round(deviation_pct, 3),
            "max_deviation_pct": MAX_SNAPSHOT_BALANCE_DEVIATION_PCT,
            "reasons": reasons,
        }

    @staticmethod
    def _sector_row(
        current: dict[str, Any],
        window_5d: dict[str, Any] | None,
        window_20d: dict[str, Any] | None,
        sector_type: str,
        quote: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        trade_date = _date(current.get("TRADE_DATE"))
        code = str(current.get("BOARD_CODE") or "").strip()
        name = str(current.get("BOARD_NAME") or "").strip()
        if trade_date is None or not code or not name:
            return None
        financing_balance = as_int(current.get("FIN_BALANCE"))
        float_market_cap = as_int(current.get("NOTLIMITED_MARKETCAP_A")) or None
        financing_ratio = _finite(current.get("FIN_BALANCE_RATIO"))
        if financing_ratio is None and float_market_cap:
            financing_ratio = financing_balance / float_market_cap * 100.0
        net_5d = as_int(window_5d.get("FIN_NETBUY_AMT")) if window_5d else None
        net_20d = as_int(window_20d.get("FIN_NETBUY_AMT")) if window_20d else None
        balance_5d = as_int(window_5d.get("FIN_BALANCE")) if window_5d else None
        balance_20d = as_int(window_20d.get("FIN_BALANCE")) if window_20d else None
        estimated_start_5d = balance_5d - net_5d if balance_5d is not None and net_5d is not None else None
        estimated_start_20d = balance_20d - net_20d if balance_20d is not None and net_20d is not None else None
        change_5d = (net_5d / estimated_start_5d * 100.0) if estimated_start_5d not in (None, 0) else None
        change_20d = (net_20d / estimated_start_20d * 100.0) if estimated_start_20d not in (None, 0) else None
        financing_buy = as_int(current.get("FIN_BUY_AMT"))
        quote_at = collector._quote_timestamp_datetime((quote or {}).get("quote_timestamp"))
        quote_is_aligned = bool(quote_at and quote_at.date() == trade_date)
        board_amount = as_int((quote or {}).get("amount")) if quote_is_aligned else 0
        price_change = _finite((quote or {}).get("change_pct")) if quote_is_aligned else None
        buy_ratio = financing_buy / board_amount * 100.0 if board_amount else None
        return {
            "trade_date": trade_date,
            "sector_type": sector_type,
            "sector_code": code,
            "sector_name": name,
            "financing_balance": financing_balance,
            "securities_balance": as_int(current.get("LOAN_BALANCE")),
            "margin_balance": as_int(current.get("MARGIN_BALANCE")),
            "financing_buy": financing_buy,
            "financing_repay": as_int(current.get("FIN_REPAY_AMT")),
            "financing_net_buy": as_int(current.get("FIN_NETBUY_AMT")),
            "financing_net_buy_5d": net_5d,
            "financing_net_buy_20d": net_20d,
            "window_end_date_5d": _date((window_5d or {}).get("END_DATE")),
            "window_end_date_20d": _date((window_20d or {}).get("END_DATE")),
            "financing_change_5d": change_5d,
            "financing_change_20d": change_20d,
            "financing_buy_ratio": buy_ratio,
            "float_market_cap": float_market_cap,
            "financing_ratio": financing_ratio,
            "price_change_pct": price_change,
            "crowding_score": None,
            "divergence_type": "待行情同日校验",
            "source": "eastmoney_margin_sector",
            "updated_at": datetime.utcnow(),
        }

    async def _sync_market_history(
        self,
        source_rows: list[dict[str, Any]],
    ) -> int:
        by_date: dict[date, dict[str, Any]] = {}
        for item in source_rows:
            trade_date = _date(item.get("trade_date"))
            if trade_date is not None:
                by_date[trade_date] = {**item, "source": "eastmoney_RPTA_RZRQ_LSHJ"}
        turnover_map = await self._market_turnover_map(list(by_date))
        async with async_session() as session:
            existing_rows = list((await session.execute(
                select(MarginMarketDaily).where(MarginMarketDaily.trade_date.in_(list(by_date)))
            )).scalars().all()) if by_date else []
        existing_lmi = {row.trade_date: row for row in existing_rows}
        rows = []
        for trade_date in sorted(by_date):
            item = by_date[trade_date]
            financing_balance = as_int(item.get("financing_balance"))
            float_market_cap = as_int(item.get("float_market_cap")) or None
            financing_ratio = _finite(item.get("financing_ratio"))
            if financing_ratio is None and float_market_cap:
                financing_ratio = financing_balance / float_market_cap * 100.0
            rows.append({
                "trade_date": trade_date,
                "margin_balance": as_int(item.get("margin_balance")),
                "financing_balance": financing_balance,
                "securities_balance": as_int(item.get("securities_balance")),
                "financing_buy": as_int(item.get("financing_buy")),
                "financing_repay": as_int(item.get("financing_repay")),
                "financing_net_buy": as_int(item.get("financing_net_buy")),
                "float_market_cap": float_market_cap,
                "market_index_close": _finite(item.get("market_index_close")),
                "market_index_change_pct": _finite(item.get("market_index_change_pct")),
                "market_turnover_amount": turnover_map.get(trade_date),
                "financing_ratio": financing_ratio,
                "lmi_score": existing_lmi.get(trade_date).lmi_score if trade_date in existing_lmi else None,
                "lmi_level": existing_lmi.get(trade_date).lmi_level if trade_date in existing_lmi else None,
                "components": existing_lmi.get(trade_date).components if trade_date in existing_lmi else {},
                "source": str(item.get("source") or "eastmoney"),
                "source_updated_at": datetime.utcnow(),
                "updated_at": datetime.utcnow(),
            })
        return await self._upsert(MarginMarketDaily, rows, ["trade_date"])

    async def _sync_stock_snapshot(self, raw_rows: list[dict[str, Any]], target_date: date) -> tuple[int, list[str]]:
        raw_by_code: dict[str, dict[str, Any]] = {}
        for item in raw_rows:
            code = _equity_code(item.get("SCODE"))
            if code and _date(item.get("DATE")) == target_date:
                raw_by_code[code] = item
        codes = sorted(raw_by_code)
        bars = await self._bar_map_for_date(target_date, codes)
        sectors = await self._sector_map(target_date, codes, allow_live_fetch=True)
        cached = await load_quant_market_snapshot()
        if str(cached.get("data_date") or "") == target_date.isoformat():
            for stock in cached.get("stocks") or []:
                code = str(stock.get("code") or "")
                if code in raw_by_code and code not in bars:
                    bars[code] = {
                        "amount": stock.get("amount"),
                        "turnover": stock.get("turnover"),
                    }
                if code in raw_by_code and not sectors.get(code) and stock.get("sector"):
                    sectors[code] = str(stock["sector"])
        rows = [
            row
            for code in codes
            if (row := self._normalise_stock_row(raw_by_code[code], bar=bars.get(code), sector_name=sectors.get(code)))
        ]
        written = await self._upsert(MarginStockDaily, rows, ["stock_code", "trade_date"])
        return written, codes

    async def _sync_sectors(self) -> int:
        written = 0
        for sector_type, type_code in SECTOR_TYPE_CODES.items():
            periods = await collector.fetch_margin_sector_rankings(type_code)
            quote_rows: list[dict[str, Any]] = []
            if sector_type == "industry":
                quote_rows = await collector.fetch_all_industry_flow()
            elif sector_type == "concept":
                quote_rows = await collector.fetch_all_concept_flow()
            quote_by_name = {str(row.get("name") or "").strip(): row for row in quote_rows}
            current_rows = periods.get("1") or []
            by_5d = {str(row.get("BOARD_CODE")): row for row in periods.get("5") or []}
            by_20d = {str(row.get("BOARD_CODE")): row for row in periods.get("20") or []}
            rows = [
                row
                for current in current_rows
                if (row := self._sector_row(
                    current,
                    by_5d.get(str(current.get("BOARD_CODE"))),
                    by_20d.get(str(current.get("BOARD_CODE"))),
                    sector_type,
                    quote_by_name.get(str(current.get("BOARD_NAME") or "").strip()),
                ))
            ]
            if rows:
                ratios = [value for row in rows if (value := _finite(row.get("financing_ratio"))) is not None]
                changes = [value for row in rows if (value := _finite(row.get("financing_change_5d"))) is not None]
                for row in rows:
                    ratio_rank = _percentile_rank(ratios, row.get("financing_ratio"), min(10, max(3, len(ratios) // 3)))
                    change_rank = _percentile_rank(changes, row.get("financing_change_5d"), min(10, max(3, len(changes) // 3)))
                    available = [value for value in (ratio_rank, change_rank) if value is not None]
                    row["crowding_score"] = sum(available) / len(available) if available else None
                    growth = _finite(row.get("financing_change_5d"))
                    price = _finite(row.get("price_change_pct"))
                    if growth is not None and price is not None and growth >= 3 and price <= 0.5:
                        row["divergence_type"] = "价格滞涨/下跌，融资快速增加"
                    elif growth is not None and price is not None and growth <= -2 and price >= 0:
                        row["divergence_type"] = "价格保持强势，融资主动下降"
                    elif growth is not None and price is not None and growth <= -3 and price <= -2:
                        row["divergence_type"] = "价格与融资同步下降，去杠杆压力"
                    elif growth is not None and growth >= 3:
                        row["divergence_type"] = "杠杆快速升温，待价格确认"
                    elif growth is not None and growth <= -3:
                        row["divergence_type"] = "板块主动去杠杆，待价格确认"
            written += await self._upsert(
                MarginSectorDaily, rows, ["trade_date", "sector_type", "sector_code"]
            )
        return written

    async def _sync_recent_stock_history(self, raw_rows: list[dict[str, Any]]) -> int:
        by_date_codes: dict[date, list[str]] = {}
        valid_raw: list[tuple[str, date, dict[str, Any]]] = []
        for item in raw_rows:
            code = _equity_code(item.get("SCODE"))
            trade_date = _date(item.get("DATE"))
            if code is None or trade_date is None:
                continue
            valid_raw.append((code, trade_date, item))
            by_date_codes.setdefault(trade_date, []).append(code)
        bars_by_key: dict[tuple[str, date], StockDailyBar] = {}
        if valid_raw:
            dates = list(by_date_codes)
            codes = sorted({code for code, _, _ in valid_raw})
            async with async_session() as session:
                bars = list((await session.execute(
                    select(StockDailyBar).where(
                        StockDailyBar.trade_date.in_(dates),
                        StockDailyBar.stock_code.in_(codes),
                    )
                )).scalars().all())
            bars_by_key = {(row.stock_code, row.trade_date): row for row in bars}
        latest_date = max(by_date_codes, default=None)
        sectors = await self._sector_map(latest_date, sorted({code for code, _, _ in valid_raw})) if latest_date else {}
        rows = [
            row
            for code, trade_date, item in valid_raw
            if (row := self._normalise_stock_row(
                item, bar=bars_by_key.get((code, trade_date)), sector_name=sectors.get(code)
            ))
        ]
        return await self._upsert(MarginStockDaily, rows, ["stock_code", "trade_date"], batch_size=250)

    async def _stock_history_date_counts(self, dates: list[date]) -> dict[date, int]:
        if not dates:
            return {}
        async with async_session() as session:
            rows = (await session.execute(
                select(MarginStockDaily.trade_date, func.count(MarginStockDaily.id))
                .where(MarginStockDaily.trade_date.in_(dates))
                .group_by(MarginStockDaily.trade_date)
            )).all()
        return {trade_date: int(count) for trade_date, count in rows}

    @staticmethod
    def _metric_from_rows(
        code: str,
        rows: list[MarginStockDaily],
        price_rows: list[StockDailyBar],
    ) -> dict[str, Any] | None:
        if not rows:
            return None
        rows = sorted(rows, key=lambda item: item.trade_date)
        latest = rows[-1]
        balances = [row.financing_balance for row in rows]
        balance = _finite(latest.financing_balance)
        if balance is None or latest.financing_ratio is None:
            return None

        def balance_change(sessions: int) -> float | None:
            if len(rows) <= sessions:
                return None
            return _pct_change(latest.financing_balance, rows[-1 - sessions].financing_balance)

        price_by_date = {row.trade_date: row for row in price_rows}
        merged_prices: list[tuple[float | None, float | None, float | None]] = []
        for margin_row in rows:
            price_row = price_by_date.get(margin_row.trade_date)
            close = _finite(price_row.close_price) if price_row else None
            change = _finite(price_row.change_pct) if price_row else None
            turnover = _finite(price_row.turnover) if price_row else None
            merged_prices.append((
                close if close is not None else _finite(margin_row.close_price),
                change if change is not None else _finite(margin_row.pct_change),
                turnover if turnover is not None else _finite(margin_row.turnover_rate),
            ))
        closes = [close for close, _, _ in merged_prices if close is not None]
        changes = [change for _, change, _ in merged_prices[-20:] if change is not None]
        turnovers = [turnover for _, _, turnover in merged_prices[-20:] if turnover is not None]
        price_change_5d = (
            _pct_change(closes[-1], closes[-6])
            if len(closes) >= 6 else _finite(latest.price_change_5d)
        )
        price_change_20d = _pct_change(closes[-1], closes[-21]) if len(closes) >= 21 else None
        volatility_20d = pstdev(changes) if len(changes) >= 10 else None
        turnover_anomaly = None
        if len(turnovers) >= 10 and turnovers[-1] is not None:
            base = median(turnovers[:-1] or turnovers)
            turnover_anomaly = _clamp((turnovers[-1] / base - 0.5) * 50) if base > 0 else None

        percentile_60 = _percentile_rank(balances[-60:], balance, 45)
        percentile_120 = _percentile_rank(balances[-120:], balance, 90)
        percentile_250 = _percentile_rank(balances[-250:], balance, 200)
        financing_change_5d = balance_change(5)
        relation_type, divergence_score, relation_text = _relation(
            price_change_5d, financing_change_5d, percentile_250
        )
        ratio_risk = financing_ratio_level(latest.financing_ratio)
        growth_score = _piecewise(
            financing_change_5d,
            [(-10, 5), (-4, 15), (-1, 30), (0, 40), (2, 55), (5, 75), (10, 92), (20, 100)],
        )
        buy_ratio_score = _piecewise(
            latest.financing_buy_ratio,
            [(0, 0), (3, 15), (5, 25), (10, 45), (15, 65), (25, 85), (40, 100)],
        )
        volatility_score = _piecewise(
            volatility_20d, [(0, 5), (1, 20), (2, 40), (3, 60), (5, 82), (8, 100)]
        )
        anomaly_values = [value for value in (volatility_score, turnover_anomaly) if value is not None]
        anomaly_score = sum(anomaly_values) / len(anomaly_values) if anomaly_values else None
        components = {
            "financing_ratio": _component(
                "融资杠杆率风险", latest.financing_ratio, ratio_risk.get("score"), 0.25,
                f"融资余额占流通市值{latest.financing_ratio:.2f}%，风险分级为{ratio_risk.get('level')}。",
            ),
            "own_history_percentile": _component(
                "自身250日融资分位", percentile_250, percentile_250, 0.20,
                "只使用该股票自身历史，不用全市场横截面排名代替。",
            ),
            "financing_growth_5d": _component(
                "融资余额5日增速", financing_change_5d, growth_score, 0.20,
                "衡量杠杆资金短期升温速度，增速越快风险分越高。",
            ),
            "financing_buy_ratio": _component(
                "融资买入/成交额", latest.financing_buy_ratio, buy_ratio_score, 0.15,
                "成交额严格按同一交易日合并；缺失时不参与评分。",
            ),
            "price_financing_divergence": _component(
                "价格-融资背离", relation_type, divergence_score, 0.10, relation_text,
            ),
            "volatility_turnover": _component(
                "波动率/换手异常", volatility_20d, anomaly_score, 0.10,
                "结合近20日波动率与最新换手相对中位数，不用单日涨跌替代。",
            ),
        }
        score, coverage = _weighted_score(components, minimum_coverage=0.80)
        if percentile_250 is None:
            score = None
        reasons = [
            item["explanation"]
            for item in components.values()
            if item.get("available") and (_finite(item.get("score")) or 0) >= 60
        ]
        if not reasons:
            reasons.append(relation_text)
        validations = [
            "价格与所属板块保持相对强势",
            "融资五日增速不继续异常扩大",
            "融资买入占成交额未进入极端区间",
        ]
        invalidations = [
            "价格跌破关键支撑且融资余额快速下降",
            "融资杠杆率与自身历史分位同步进入极端区",
            "放量下跌与融资偿还上升形成负反馈",
        ]
        return {
            "trade_date": latest.trade_date,
            "stock_code": code,
            "financing_ratio": latest.financing_ratio,
            "financing_buy_ratio": latest.financing_buy_ratio,
            "financing_change_1d": balance_change(1),
            "financing_change_3d": balance_change(3),
            "financing_change_5d": financing_change_5d,
            "financing_change_10d": balance_change(10),
            "financing_change_20d": balance_change(20),
            "percentile_60": percentile_60,
            "percentile_120": percentile_120,
            "percentile_250": percentile_250,
            "price_change_5d": price_change_5d,
            "price_change_20d": price_change_20d,
            "volatility_20d": volatility_20d,
            "turnover_anomaly_score": turnover_anomaly,
            "divergence_type": relation_type,
            "divergence_score": divergence_score,
            "lri_score": _round(score, 1),
            "lri_level": lri_level(score),
            "coverage_pct": _round(coverage, 1),
            "components": components,
            "risk_reasons": reasons,
            "validation_conditions": validations,
            "invalidation_conditions": invalidations,
            "source": "calculated_from_own_margin_history",
            "updated_at": datetime.utcnow(),
        }

    async def _calculate_metric_for_code(
        self,
        code: str,
        as_of_date: date | None = None,
    ) -> dict[str, Any] | None:
        async with async_session() as session:
            margin_query = select(MarginStockDaily).where(MarginStockDaily.stock_code == code)
            price_query = select(StockDailyBar).where(StockDailyBar.stock_code == code)
            if as_of_date is not None:
                margin_query = margin_query.where(MarginStockDaily.trade_date <= as_of_date)
                price_query = price_query.where(StockDailyBar.trade_date <= as_of_date)
            margin_rows = list((await session.execute(
                margin_query.order_by(MarginStockDaily.trade_date)
            )).scalars().all())
            price_rows = list((await session.execute(
                price_query.order_by(StockDailyBar.trade_date)
            )).scalars().all())
        metric = self._metric_from_rows(code, margin_rows, price_rows)
        if metric is not None:
            await self._upsert(StockLeverageMetric, [metric], ["stock_code", "trade_date"])
        return metric

    async def _persist_stock_history(self, code: str, raw_rows: list[dict[str, Any]]) -> int:
        bars = await self._bar_map_for_stock(code)
        latest_date = max((_date(row.get("DATE")) for row in raw_rows if _date(row.get("DATE"))), default=None)
        sectors = await self._sector_map(latest_date, [code]) if latest_date else {}
        rows = [
            row
            for raw in raw_rows
            if (row := self._normalise_stock_row(
                raw, bar=bars.get(_date(raw.get("DATE"))), sector_name=sectors.get(code)
            ))
        ]
        return await self._upsert(MarginStockDaily, rows, ["stock_code", "trade_date"])

    async def _ensure_price_history(self, code: str, stock_name: str) -> int:
        async with async_session() as session:
            count = (await session.execute(
                select(func.count(StockDailyBar.id)).where(StockDailyBar.stock_code == code)
            )).scalar_one()
        if int(count or 0) >= 40:
            return 0
        try:
            payload = await collector.fetch_stock_price_history(code, days=420)
        except Exception:
            return 0
        rows = [
            row
            for raw in payload.get("history") or []
            if (row := self._normalise_price_row(
                code, stock_name, raw, str(payload.get("source") or "tencent")
            ))
        ]
        return await self._upsert(StockDailyBar, rows, ["stock_code", "trade_date"])

    async def ensure_stock_history(self, stock_code: str, force: bool = False) -> dict[str, Any]:
        code = normalize_stock_code(stock_code)
        lock = self._stock_locks.setdefault(code, asyncio.Lock())
        async with lock:
            async with async_session() as session:
                latest = (await session.execute(
                    select(MarginStockDaily)
                    .where(MarginStockDaily.stock_code == code)
                    .order_by(desc(MarginStockDaily.trade_date))
                    .limit(1)
                )).scalar_one_or_none()
                count = (await session.execute(
                    select(func.count(MarginStockDaily.id)).where(MarginStockDaily.stock_code == code)
                )).scalar_one()
                metric = (await session.execute(
                    select(StockLeverageMetric)
                    .where(StockLeverageMetric.stock_code == code)
                    .order_by(desc(StockLeverageMetric.trade_date))
                    .limit(1)
                )).scalar_one_or_none()
            if latest is None:
                raw_rows = await collector.fetch_margin_stock_history(code, days=260)
                if not raw_rows:
                    return {
                        "status": "not_margin_eligible",
                        "message": "当前股票不是融资融券标的",
                        "stock_code": code,
                    }
                latest_name = str(raw_rows[0].get("SECNAME") or code)
                await self._ensure_price_history(code, latest_name)
                await self._persist_stock_history(code, raw_rows)
                metric_payload = await self._calculate_metric_for_code(code)
                return {"status": "synced", "stock_code": code, "metric": metric_payload}

            stale_metric = metric is None or metric.trade_date != latest.trade_date
            needs_history = int(count or 0) < 250 or stale_metric or force
            if needs_history:
                raw_rows = await collector.fetch_margin_stock_history(code, days=260)
                await self._ensure_price_history(code, latest.stock_name)
                if raw_rows:
                    await self._persist_stock_history(code, raw_rows)
                metric_payload = await self._calculate_metric_for_code(code)
                return {"status": "synced", "stock_code": code, "metric": metric_payload}
            return {"status": "cached", "stock_code": code}

    async def _calculate_recent_metrics(self, target_date: date) -> int:
        async with async_session() as session:
            codes = list((await session.execute(
                select(MarginStockDaily.stock_code)
                .where(MarginStockDaily.trade_date == target_date)
                .order_by(desc(MarginStockDaily.financing_balance))
            )).scalars().all())

        written = 0
        for start in range(0, len(codes), self.METRIC_CALCULATION_CHUNK_SIZE):
            chunk = codes[start:start + self.METRIC_CALCULATION_CHUNK_SIZE]
            async with async_session() as session:
                margin_rows = list((await session.execute(
                    select(MarginStockDaily)
                    .where(
                        MarginStockDaily.stock_code.in_(chunk),
                        MarginStockDaily.trade_date <= target_date,
                    )
                    .order_by(MarginStockDaily.stock_code, MarginStockDaily.trade_date)
                )).scalars().all())
                price_rows = list((await session.execute(
                    select(StockDailyBar)
                    .where(
                        StockDailyBar.stock_code.in_(chunk),
                        StockDailyBar.trade_date >= target_date - timedelta(days=65),
                        StockDailyBar.trade_date <= target_date,
                    )
                    .order_by(StockDailyBar.stock_code, StockDailyBar.trade_date)
                )).scalars().all())

            margin_by_code: dict[str, list[MarginStockDaily]] = {}
            prices_by_code: dict[str, list[StockDailyBar]] = {}
            for row in margin_rows:
                margin_by_code.setdefault(row.stock_code, []).append(row)
            for row in price_rows:
                prices_by_code.setdefault(row.stock_code, []).append(row)
            metrics = [
                metric
                for code in chunk
                if (metric := self._metric_from_rows(
                    code, margin_by_code.get(code, []), prices_by_code.get(code, [])
                )) is not None
            ]
            written += await self._upsert(
                StockLeverageMetric, metrics, ["stock_code", "trade_date"], batch_size=100
            )
            await asyncio.sleep(0)
        return written

    async def _prewarm_own_histories(self, target_date: date) -> int:
        async with async_session() as session:
            rows = list((await session.execute(
                select(MarginStockDaily)
                .where(MarginStockDaily.trade_date == target_date)
                .order_by(desc(MarginStockDaily.financing_ratio), desc(MarginStockDaily.financing_balance))
                .limit(self.PREWARM_LIMIT)
            )).scalars().all())
        # Blend largest balances into a ratio-first set so established blue
        # chips and genuinely crowded smaller names are both warmed.
        async with async_session() as session:
            balance_rows = list((await session.execute(
                select(MarginStockDaily)
                .where(MarginStockDaily.trade_date == target_date)
                .order_by(desc(MarginStockDaily.financing_balance))
                .limit(self.PREWARM_LIMIT)
            )).scalars().all())
        selected = list(dict.fromkeys(
            code
            for pair in zip(rows, balance_rows)
            for code in (pair[0].stock_code, pair[1].stock_code)
        ))[:self.PREWARM_LIMIT]
        async with async_session() as session:
            history_counts = dict((await session.execute(
                select(MarginStockDaily.stock_code, func.count(MarginStockDaily.id))
                .where(
                    MarginStockDaily.stock_code.in_(selected),
                    MarginStockDaily.trade_date <= target_date,
                )
                .group_by(MarginStockDaily.stock_code)
            )).all()) if selected else {}
        pending = []
        for code in selected:
            if int(history_counts.get(code) or 0) >= 250:
                await self._calculate_metric_for_code(code, as_of_date=target_date)
            else:
                pending.append(code)
        written = 0
        for start in range(0, len(pending), 20):
            batch = pending[start:start + 20]
            histories = await collector.fetch_margin_stock_histories(
                batch, days=260, end_date=target_date,
            )
            for code in batch:
                raw_rows = histories.get(code) or []
                if not raw_rows:
                    continue
                written += await self._persist_stock_history(code, raw_rows)
                await self._calculate_metric_for_code(code, as_of_date=target_date)
        return written

    async def _calculate_lmi(self, target_date: date) -> dict[str, Any] | None:
        async with async_session() as session:
            market_rows_desc = list((await session.execute(
                select(MarginMarketDaily)
                .where(MarginMarketDaily.trade_date <= target_date)
                .order_by(desc(MarginMarketDaily.trade_date))
                .limit(300)
            )).scalars().all())
            stock_rows = list((await session.execute(
                select(MarginStockDaily).where(MarginStockDaily.trade_date == target_date)
            )).scalars().all())
        market_rows = list(reversed(market_rows_desc))
        if not market_rows:
            return None
        latest = market_rows[-1]
        balances = [row.financing_balance for row in market_rows]
        percentile = _percentile_rank(balances[-250:], latest.financing_balance, 200)
        change_5d = _pct_change(latest.financing_balance, market_rows[-6].financing_balance) if len(market_rows) >= 6 else None
        change_20d = _pct_change(latest.financing_balance, market_rows[-21].financing_balance) if len(market_rows) >= 21 else None
        growth_5d_score = _piecewise(change_5d, [(-8, 5), (-3, 15), (0, 38), (1, 52), (3, 72), (6, 90), (10, 100)])
        growth_20d_score = _piecewise(change_20d, [(-15, 5), (-5, 18), (0, 40), (3, 55), (8, 75), (15, 92), (25, 100)])
        net_buy_ratio = (
            latest.financing_net_buy / latest.market_turnover_amount * 100.0
            if latest.financing_net_buy is not None and latest.market_turnover_amount not in (None, 0) else None
        )
        net_buy_score = _piecewise(net_buy_ratio, [(-3, 5), (-1, 18), (0, 40), (0.5, 55), (1, 70), (2, 88), (4, 100)])
        eligible_ratios = [row.financing_ratio for row in stock_rows if row.financing_ratio is not None]
        high_leverage_ratio = (
            sum(value >= 8 for value in eligible_ratios) / len(eligible_ratios) * 100.0
            if eligible_ratios else None
        )
        high_leverage_score = _piecewise(high_leverage_ratio, [(0, 0), (3, 20), (6, 40), (10, 60), (18, 82), (30, 100)])
        sector_totals: dict[str, int] = {}
        mapped_stock_count = 0
        for row in stock_rows:
            if row.sector_name:
                mapped_stock_count += 1
                sector_totals[row.sector_name] = (
                    sector_totals.get(row.sector_name, 0) + (row.financing_balance or 0)
                )
        sector_mapping_coverage = (
            mapped_stock_count / len(stock_rows) * 100.0 if stock_rows else None
        )
        sector_balances = (
            sorted(sector_totals.values(), reverse=True)
            if sector_mapping_coverage is not None and sector_mapping_coverage >= 80 else []
        )
        total_sector_balance = sum(sector_balances)
        concentration_ratio = (sum(sector_balances[:5]) / total_sector_balance * 100.0) if total_sector_balance else None
        concentration_score = _piecewise(concentration_ratio, [(0, 0), (20, 20), (35, 45), (50, 70), (65, 88), (80, 100)])
        financing_change_5d = change_5d
        index_change_5d = (
            _pct_change(latest.market_index_close, market_rows[-6].market_index_close)
            if len(market_rows) >= 6 else None
        )
        divergence = None
        if financing_change_5d is not None and index_change_5d is not None:
            if financing_change_5d >= 2 and index_change_5d <= 0.5:
                divergence = 90.0
            elif financing_change_5d <= -2 and index_change_5d <= -2:
                divergence = 85.0
            elif financing_change_5d <= -1 and index_change_5d >= 0:
                divergence = 18.0
            else:
                divergence = _clamp(45 + (financing_change_5d - index_change_5d) * 8)
        components = {
            "market_percentile_250": _component(
                "市场融资余额250日分位", percentile, percentile, 0.25,
                "衡量当前融资余额在近250个交易日中的位置。",
            ),
            "growth_5d": _component("融资余额5日增速", change_5d, growth_5d_score, 0.20, "短期杠杆升温速度。"),
            "growth_20d": _component("融资余额20日增速", change_20d, growth_20d_score, 0.15, "中期杠杆累积速度。"),
            "net_buy_turnover": _component("融资净买入/市场成交额", net_buy_ratio, net_buy_score, 0.15, "同日融资净买入相对全市场成交额。"),
            "high_leverage_share": _component("高杠杆个股占比", high_leverage_ratio, high_leverage_score, 0.10, "融资杠杆率达到8%以上的两融标的占比。"),
            "sector_concentration": _component(
                "板块杠杆集中度", concentration_ratio, concentration_score, 0.10,
                "按同一披露日个股融资余额聚合；行业映射覆盖不足80%时不参与评分。",
            ),
            "index_financing_divergence": _component("指数-融资背离", financing_change_5d, divergence, 0.05, "比较指数五日表现与融资余额五日变化。"),
        }
        score, coverage = _weighted_score(components, minimum_coverage=0.75)
        payload = {
            "score": _round(score, 1),
            "level": lmi_level(score),
            "coverage_pct": _round(coverage, 1),
            "components": components,
            "financing_change_5d": _round(change_5d),
            "financing_change_20d": _round(change_20d),
            "financing_net_buy_turnover_ratio": _round(net_buy_ratio, 4),
            "high_leverage_stock_ratio": _round(high_leverage_ratio),
            "sector_concentration_ratio": _round(concentration_ratio),
            "sector_mapping_coverage_pct": _round(sector_mapping_coverage),
            "index_change_5d": _round(index_change_5d),
        }
        async with async_session() as session:
            row = await session.get(MarginMarketDaily, target_date)
            if row is not None:
                row.lmi_score = _round(score, 1)
                row.lmi_level = lmi_level(score)
                row.components = payload
                row.updated_at = datetime.utcnow()
                await session.commit()
        return payload

    async def sync(self, *, full: bool = True, prewarm: bool = True) -> dict[str, Any]:
        async with self._sync_lock:
            started = shanghai_now()
            self._set_status(
                status="running", progress=3, stage="确认最新披露日",
                started_at=started.isoformat(), finished_at=None, error=None,
            )
            market_history = await collector.fetch_margin_market_history(days=300)
            market_dates = [
                trade_date
                for item in market_history
                if (trade_date := _date(item.get("trade_date"))) is not None
            ]
            target_date = max(market_dates, default=None)
            if target_date is None:
                raise RuntimeError("两融官方全市场披露日不可用")
            official_market_row = next(
                item for item in market_history if _date(item.get("trade_date")) == target_date
            )
            self._set_status(progress=8, stage="拉取最新完整个股快照", data_date=target_date.isoformat())
            snapshot = await collector.fetch_margin_stock_snapshot(target_date)
            if not snapshot.get("complete") or not snapshot.get("records"):
                raise RuntimeError("两融最新个股快照不完整")
            raw_latest = list(snapshot["records"])
            snapshot_audit = self._audit_stock_snapshot(raw_latest, target_date, official_market_row)
            if not snapshot_audit["passed"]:
                details = "；".join(snapshot_audit["reasons"]) or "未知完整性错误"
                raise RuntimeError(f"两融个股快照未通过全市场口径审计：{details}")

            self._set_status(progress=20, stage="同步市场250日趋势")
            market_written = await self._sync_market_history(market_history)

            self._set_status(progress=34, stage="持久化最新个股快照")
            stock_written, _codes = await self._sync_stock_snapshot(raw_latest, target_date)

            recent_written = 0
            recent_dates_fetched = 0
            recent_dates_cached = 0
            if full:
                market_dates = sorted(
                    trade_date
                    for item in market_history
                    if (trade_date := _date(item.get("trade_date"))) is not None
                )
                recent_dates = market_dates[-25:]
                official_by_date = {
                    trade_date: item
                    for item in market_history
                    if (trade_date := _date(item.get("trade_date"))) is not None
                }
                cached_date_counts = await self._stock_history_date_counts(recent_dates)
                for index, history_date in enumerate(recent_dates, start=1):
                    progress = 45 + int(index / max(len(recent_dates), 1) * 17)
                    if cached_date_counts.get(history_date, 0) >= 1000:
                        recent_dates_cached += 1
                        self._set_status(
                            progress=progress,
                            stage=f"复用已校验两融历史 {index}/{len(recent_dates)}",
                        )
                        continue
                    self._set_status(
                        progress=progress,
                        stage=f"同步全市场近25日两融历史 {index}/{len(recent_dates)}",
                    )
                    daily_snapshot = (
                        snapshot
                        if history_date == target_date
                        else await collector.fetch_margin_stock_snapshot(history_date)
                    )
                    if not daily_snapshot.get("complete") or not daily_snapshot.get("records"):
                        raise RuntimeError(f"{history_date.isoformat()}两融个股历史快照不完整")
                    daily_rows = list(daily_snapshot["records"])
                    daily_audit = self._audit_stock_snapshot(
                        daily_rows, history_date, official_by_date[history_date],
                    )
                    if not daily_audit["passed"]:
                        details = "；".join(daily_audit["reasons"]) or "未知完整性错误"
                        raise RuntimeError(
                            f"{history_date.isoformat()}两融历史未通过口径审计：{details}"
                        )
                    recent_written += await self._sync_recent_stock_history(daily_rows)
                    recent_dates_fetched += 1

            self._set_status(progress=64, stage="同步行业、概念与地域板块")
            sector_written = await self._sync_sectors()

            self._set_status(progress=75, stage="计算全市场短中期风险指标")
            metric_written = await self._calculate_recent_metrics(target_date)

            prewarm_written = 0
            if full and prewarm:
                self._set_status(progress=84, stage="回补重点标的250日自历史")
                prewarm_written = await self._prewarm_own_histories(target_date)

            self._set_status(progress=96, stage="计算LMI与数据审计")
            lmi = await self._calculate_lmi(target_date)
            result = {
                "status": "success",
                "data_date": target_date.isoformat(),
                "market_rows": market_written,
                "latest_stock_rows": stock_written,
                "recent_stock_rows": recent_written,
                "recent_dates_fetched": recent_dates_fetched,
                "recent_dates_cached": recent_dates_cached,
                "sector_rows": sector_written,
                "metric_rows": metric_written,
                "prewarm_rows": prewarm_written,
                "lmi": lmi,
                "snapshot_audit": snapshot_audit,
                "source": "eastmoney_margin_disclosure",
                "is_realtime": False,
                "disclosure_note": MARGIN_DISCLOSURE_NOTE,
                "elapsed_seconds": round((shanghai_now() - started).total_seconds(), 2),
            }
            self._set_status(
                status="completed", progress=100, stage="刷新完成",
                finished_at=shanghai_now().isoformat(), error=None, result=result,
            )
            return result

    async def _run_background_sync(self, full: bool, prewarm: bool) -> None:
        try:
            await self.sync(full=full, prewarm=prewarm)
        except Exception as exc:
            self._set_status(
                status="failed", stage="刷新失败，继续使用最近缓存",
                finished_at=shanghai_now().isoformat(), error=type(exc).__name__,
            )
        finally:
            self._refresh_task = None

    def start_refresh(self, *, full: bool = True, prewarm: bool = True) -> dict[str, Any]:
        if self._refresh_task and not self._refresh_task.done():
            return {**self.refresh_status(), "already_running": True}
        self._refresh_task = asyncio.create_task(self._run_background_sync(full, prewarm))
        self._set_status(
            status="queued", progress=1, stage="刷新任务已提交",
            started_at=shanghai_now().isoformat(), finished_at=None, error=None,
        )
        return {**self.refresh_status(), "already_running": False}

    @staticmethod
    def _market_row_payload(row: MarginMarketDaily) -> dict[str, Any]:
        return {
            "trade_date": row.trade_date.isoformat(),
            "margin_balance": row.margin_balance,
            "financing_balance": row.financing_balance,
            "securities_balance": row.securities_balance,
            "financing_buy": row.financing_buy,
            "financing_repay": row.financing_repay,
            "financing_net_buy": row.financing_net_buy,
            "float_market_cap": row.float_market_cap,
            "market_index_close": row.market_index_close,
            "market_index_change_pct": row.market_index_change_pct,
            "market_turnover_amount": row.market_turnover_amount,
            "financing_ratio": row.financing_ratio,
            "lmi_score": row.lmi_score,
            "lmi_level": row.lmi_level,
            "source": row.source,
        }

    @staticmethod
    def _metric_payload(metric: StockLeverageMetric | None) -> dict[str, Any] | None:
        if metric is None:
            return None
        return {
            "trade_date": metric.trade_date.isoformat(),
            "financing_ratio": metric.financing_ratio,
            "financing_buy_ratio": metric.financing_buy_ratio,
            "financing_change_1d": metric.financing_change_1d,
            "financing_change_3d": metric.financing_change_3d,
            "financing_change_5d": metric.financing_change_5d,
            "financing_change_10d": metric.financing_change_10d,
            "financing_change_20d": metric.financing_change_20d,
            "percentile_60": metric.percentile_60,
            "percentile_120": metric.percentile_120,
            "percentile_250": metric.percentile_250,
            "price_change_5d": metric.price_change_5d,
            "price_change_20d": metric.price_change_20d,
            "volatility_20d": metric.volatility_20d,
            "turnover_anomaly_score": metric.turnover_anomaly_score,
            "divergence_type": metric.divergence_type,
            "divergence_score": metric.divergence_score,
            "lri_score": metric.lri_score,
            "lri_level": metric.lri_level,
            "coverage_pct": metric.coverage_pct,
            "components": metric.components or {},
            "risk_reasons": metric.risk_reasons or [],
            "validation_conditions": metric.validation_conditions or [],
            "invalidation_conditions": metric.invalidation_conditions or [],
            "source": metric.source,
        }

    @staticmethod
    def _stock_payload(row: MarginStockDaily, metric: StockLeverageMetric | None = None) -> dict[str, Any]:
        risk = financing_ratio_level(row.financing_ratio)
        return {
            "stock_code": row.stock_code,
            "stock_name": row.stock_name,
            "sector_name": row.sector_name,
            "trade_market": row.trade_market,
            "trade_date": row.trade_date.isoformat(),
            "close_price": row.close_price,
            "pct_change": row.pct_change,
            "financing_balance": row.financing_balance,
            "financing_buy": row.financing_buy,
            "financing_repay": row.financing_repay,
            "financing_net_buy": row.financing_net_buy,
            "financing_net_buy_3d": row.financing_net_buy_3d,
            "financing_net_buy_5d": row.financing_net_buy_5d,
            "financing_net_buy_10d": row.financing_net_buy_10d,
            "securities_balance": row.securities_balance,
            "securities_sell_shares": row.securities_sell,
            "securities_repay_shares": row.securities_repay,
            "margin_balance": row.margin_balance,
            "turnover_amount": row.turnover_amount,
            "turnover_rate": row.turnover_rate,
            "float_market_cap": row.float_market_cap,
            "financing_ratio": row.financing_ratio,
            "financing_ratio_level": risk,
            "financing_buy_ratio": row.financing_buy_ratio,
            "metric": MarginLeverageService._metric_payload(metric),
            "source": row.source,
        }

    async def market(self, days: int = 250) -> dict[str, Any]:
        bounded = min(max(int(days), 20), 300)
        async with async_session() as session:
            rows_desc = list((await session.execute(
                select(MarginMarketDaily).order_by(desc(MarginMarketDaily.trade_date)).limit(bounded)
            )).scalars().all())
        rows = list(reversed(rows_desc))
        latest = rows[-1] if rows else None
        stale = bool(latest and (shanghai_now().date() - latest.trade_date).days > 3)
        return {
            "available": bool(latest),
            "latest": self._market_row_payload(latest) if latest else None,
            "history": [self._market_row_payload(row) for row in rows],
            "lmi": (latest.components or {}) if latest else None,
            "meta": {
                "data_date": latest.trade_date.isoformat() if latest else None,
                "updated_at": latest.updated_at.isoformat() if latest and latest.updated_at else None,
                "source": latest.source if latest else "unavailable",
                "is_realtime": False,
                "cache_state": "stale" if stale else "fresh" if latest else "empty",
                "disclosure_note": MARGIN_DISCLOSURE_NOTE,
            },
            "refresh": self.refresh_status(),
        }

    async def sectors(
        self,
        *,
        sector_type: str = "industry",
        sort: str = "net_buy",
        order: str = "desc",
        limit: int = 100,
    ) -> dict[str, Any]:
        if sector_type not in SECTOR_TYPE_CODES:
            raise ValueError("板块类型仅支持industry、concept、region")
        sort_map = {
            "balance": MarginSectorDaily.financing_balance,
            "net_buy": MarginSectorDaily.financing_net_buy,
            "net_buy_5d": MarginSectorDaily.financing_net_buy_5d,
            "net_buy_20d": MarginSectorDaily.financing_net_buy_20d,
            "growth_5d": MarginSectorDaily.financing_change_5d,
            "growth_20d": MarginSectorDaily.financing_change_20d,
            "ratio": MarginSectorDaily.financing_ratio,
            "crowding": MarginSectorDaily.crowding_score,
        }
        if sort not in sort_map:
            raise ValueError("不支持的板块排序字段")
        async with async_session() as session:
            latest = (await session.execute(
                select(func.max(MarginSectorDaily.trade_date)).where(
                    MarginSectorDaily.sector_type == sector_type
                )
            )).scalar_one_or_none()
            ordering = asc(sort_map[sort]) if order == "asc" else desc(sort_map[sort])
            rows = list((await session.execute(
                select(MarginSectorDaily)
                .where(
                    MarginSectorDaily.trade_date == latest,
                    MarginSectorDaily.sector_type == sector_type,
                )
                .order_by(ordering)
                .limit(min(max(limit, 1), 500))
            )).scalars().all()) if latest else []
        output = [{
            "rank": index + 1,
            "trade_date": row.trade_date.isoformat(),
            "sector_type": row.sector_type,
            "sector_code": row.sector_code,
            "sector_name": row.sector_name,
            "financing_balance": row.financing_balance,
            "financing_net_buy": row.financing_net_buy,
            "financing_net_buy_5d": row.financing_net_buy_5d,
            "financing_net_buy_20d": row.financing_net_buy_20d,
            "financing_change_5d": row.financing_change_5d,
            "financing_change_20d": row.financing_change_20d,
            "financing_ratio": row.financing_ratio,
            "crowding_score": row.crowding_score,
            "divergence_type": row.divergence_type,
            "window_end_date_5d": row.window_end_date_5d.isoformat() if row.window_end_date_5d else None,
            "window_end_date_20d": row.window_end_date_20d.isoformat() if row.window_end_date_20d else None,
        } for index, row in enumerate(rows)]
        return {
            "available": bool(rows), "rankings": output, "count": len(output),
            "meta": {
                "data_date": latest.isoformat() if latest else None,
                "is_realtime": False, "source": "eastmoney_margin_sector_cache",
                "disclosure_note": MARGIN_DISCLOSURE_NOTE,
            },
        }

    async def stock_rankings(
        self,
        *,
        metric: str = "balance",
        order: str = "desc",
        limit: int = 100,
        sector: str | None = None,
    ) -> dict[str, Any]:
        stock_metric_map = {
            "balance": MarginStockDaily.financing_balance,
            "net_buy": MarginStockDaily.financing_net_buy,
            "ratio": MarginStockDaily.financing_ratio,
            "buy_ratio": MarginStockDaily.financing_buy_ratio,
        }
        leverage_metric_map = {
            "growth_5d": StockLeverageMetric.financing_change_5d,
            "growth_20d": StockLeverageMetric.financing_change_20d,
            "percentile_250": StockLeverageMetric.percentile_250,
            "lri": StockLeverageMetric.lri_score,
            "divergence": StockLeverageMetric.divergence_score,
        }
        if metric not in {*stock_metric_map, *leverage_metric_map}:
            raise ValueError("不支持的个股榜单指标")
        async with async_session() as session:
            latest = (await session.execute(select(func.max(MarginMarketDaily.trade_date)))).scalar_one_or_none()
            if latest is None:
                latest = (await session.execute(select(func.max(MarginStockDaily.trade_date)))).scalar_one_or_none()
            if latest is None:
                return {"available": False, "rankings": [], "count": 0, "meta": {"data_date": None}}
            target = stock_metric_map[metric] if metric in stock_metric_map else leverage_metric_map[metric]
            ordering = asc(target) if order == "asc" else desc(target)
            query = (
                select(MarginStockDaily, StockLeverageMetric)
                .outerjoin(
                    StockLeverageMetric,
                    and_(
                        StockLeverageMetric.stock_code == MarginStockDaily.stock_code,
                        StockLeverageMetric.trade_date == MarginStockDaily.trade_date,
                    ),
                )
                .where(MarginStockDaily.trade_date == latest)
            )
            if sector:
                query = query.where(MarginStockDaily.sector_name == sector)
            if metric in leverage_metric_map:
                query = query.where(target.is_not(None))
            rows = (await session.execute(
                query.order_by(ordering).limit(min(max(limit, 1), 500))
            )).all()
        rankings = []
        for index, (stock, leverage) in enumerate(rows):
            item = self._stock_payload(stock, leverage)
            item["rank"] = index + 1
            item["rank_metric"] = metric
            item["rank_value"] = (
                getattr(stock, stock_metric_map[metric].key)
                if metric in stock_metric_map else getattr(leverage, leverage_metric_map[metric].key)
            )
            rankings.append(item)
        return {
            "available": bool(rankings),
            "rankings": rankings,
            "count": len(rankings),
            "metric": metric,
            "meta": {
                "data_date": latest.isoformat(),
                "is_realtime": False,
                "source": "margin_database_cache",
                "disclosure_note": MARGIN_DISCLOSURE_NOTE,
                "reference_line_note": REFERENCE_LINE_NOTE,
            },
        }

    async def stock_detail(
        self,
        stock_code: str,
        *,
        refresh: bool = False,
        history_limit: int = 260,
    ) -> dict[str, Any]:
        code = normalize_stock_code(stock_code)
        sync_state = await self.ensure_stock_history(code, force=refresh)
        if sync_state.get("status") == "not_margin_eligible":
            return {
                "available": False,
                "eligible": False,
                "stock_code": code,
                "message": "当前股票不是融资融券标的",
                "risk_message": "暂无两融风险评分",
                "meta": {
                    "data_date": None, "is_realtime": False,
                    "source": "eastmoney_margin_disclosure",
                    "disclosure_note": MARGIN_DISCLOSURE_NOTE,
                },
            }
        bounded = min(max(int(history_limit), 20), 300)
        async with async_session() as session:
            rows_desc = list((await session.execute(
                select(MarginStockDaily)
                .where(MarginStockDaily.stock_code == code)
                .order_by(desc(MarginStockDaily.trade_date))
                .limit(bounded)
            )).scalars().all())
            latest = rows_desc[0] if rows_desc else None
            metric = (await session.execute(
                select(StockLeverageMetric)
                .where(
                    StockLeverageMetric.stock_code == code,
                    StockLeverageMetric.trade_date == latest.trade_date if latest else False,
                )
                .limit(1)
            )).scalar_one_or_none() if latest else None
        if latest is None:
            return {
                "available": False, "eligible": None, "stock_code": code,
                "message": "暂无两融风险评分",
                "meta": {"data_date": None, "is_realtime": False, "source": "unavailable"},
            }
        history = [{
            "trade_date": row.trade_date.isoformat(),
            "financing_balance": row.financing_balance,
            "financing_buy": row.financing_buy,
            "financing_repay": row.financing_repay,
            "financing_net_buy": row.financing_net_buy,
            "financing_ratio": row.financing_ratio,
            "financing_buy_ratio": row.financing_buy_ratio,
            "close_price": row.close_price,
            "pct_change": row.pct_change,
        } for row in reversed(rows_desc)]
        metric_payload = self._metric_payload(metric)
        relation = (metric_payload or {}).get("divergence_type") or "数据不足"
        reasons = (metric_payload or {}).get("risk_reasons") or ["历史样本仍在回补，当前只展示已核验两融事实。"]
        return {
            "available": True,
            "eligible": True,
            "stock": self._stock_payload(latest, metric),
            "history": history,
            "history_count": len(history),
            "risk_explanation": {
                "relation": relation,
                "reasons": reasons,
                "validation_conditions": (metric_payload or {}).get("validation_conditions") or [],
                "invalidation_conditions": (metric_payload or {}).get("invalidation_conditions") or [],
            },
            "meta": {
                "data_date": latest.trade_date.isoformat(),
                "updated_at": latest.updated_at.isoformat() if latest.updated_at else None,
                "source": latest.source,
                "is_realtime": False,
                "cache_state": sync_state.get("status"),
                "disclosure_note": MARGIN_DISCLOSURE_NOTE,
                "reference_line_note": REFERENCE_LINE_NOTE,
            },
        }


margin_leverage_service = MarginLeverageService()
