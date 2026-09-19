"""Personal, manually reconciled account state for the Wildman module.

This service deliberately does not connect to a broker.  It stores a bounded
user snapshot in ``PersonalSystemConfig`` and returns explicit data-quality
and T+1 states so callers cannot mistake manual input for broker verification.
"""

from __future__ import annotations

import hashlib
import math
from datetime import date, timedelta
from typing import Any

from database import async_session
from market_data.numcat.extended_provider import numcat_extended_provider
from models import PersonalSystemConfig
from services.data_collector import shanghai_now


ACCOUNT_VERSION = 1
ACCOUNT_KEY_PREFIX = "wildman_account_v1:"
MAX_NAV_DAYS = 90
MAX_HOLDINGS = 100
MAX_HOLDING_SNAPSHOTS = 90
MANUAL_SOURCE = "manual_user_input"


def account_key(username: str) -> str:
    """Return a stable, bounded, non-disclosing PersonalSystemConfig key."""
    identity = str(username or "").strip()
    if not identity:
        raise ValueError("用户身份不能为空")
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:48]
    return f"{ACCOUNT_KEY_PREFIX}{digest}"


def _today() -> date:
    return shanghai_now().date()


def _date_value(value: Any, field: str) -> date:
    if isinstance(value, date):
        return value
    text = str(value or "").strip()
    try:
        parsed = date.fromisoformat(text)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field}必须使用YYYY-MM-DD格式") from exc
    return parsed


def _date_text(value: Any, field: str) -> str:
    return _date_value(value, field).isoformat()


def _number(value: Any, field: str, *, minimum: float | None = None) -> float:
    if isinstance(value, bool) or value is None or value == "":
        raise ValueError(f"{field}必须是数字")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field}必须是数字") from exc
    if not math.isfinite(result):
        raise ValueError(f"{field}必须是有限数字")
    if minimum is not None and result < minimum:
        raise ValueError(f"{field}不能小于{minimum}")
    return result


def _optional_number(value: Any, field: str, *, minimum: float | None = None) -> float | None:
    if value is None or value == "":
        return None
    return _number(value, field, minimum=minimum)


def _quantity(value: Any, field: str, *, default: int | None = None) -> int:
    if value is None or value == "":
        if default is not None:
            return default
        raise ValueError(f"{field}不能为空")
    number = _number(value, field, minimum=0)
    if not number.is_integer():
        raise ValueError(f"{field}必须是整数")
    return int(number)


def _source(value: Any) -> str:
    text = str(value or "").strip()
    return text[:120] or MANUAL_SOURCE


def _symbol(value: Any) -> str:
    text = str(value or "").strip().upper().replace(" ", "")
    if not text or len(text) > 32:
        raise ValueError("持仓代码不能为空且不能超过32个字符")
    parts = text.split(".")
    if len(parts) == 1:
        code, market = parts[0], ""
    elif len(parts) == 2:
        if parts[0] in {"SH", "SZ", "BJ"}:
            market, code = parts
        else:
            code, market = parts
    else:
        raise ValueError("持仓代码格式应为6位代码或6位代码.SH/.SZ/.BJ")
    if not code.isdigit() or len(code) > 6:
        raise ValueError("持仓代码必须是6位数字")
    code = code.zfill(6)
    if not market:
        if code.startswith(("4", "8", "92")):
            market = "BJ"
        elif code.startswith(("0", "2", "3")):
            market = "SZ"
        else:
            market = "SH"
    if market not in {"SH", "SZ", "BJ"}:
        raise ValueError("交易所后缀只能是.SH、.SZ或.BJ")
    return f"{code}.{market}"


def _default_account() -> dict[str, Any]:
    return {
        "version": ACCOUNT_VERSION,
        "as_of": None,
        "source": MANUAL_SOURCE,
        "broker_connected": False,
        "broker_verified": False,
        "verification_status": "manual_user_input",
        "manual_reconciliation_required": True,
        "manual_reconciliation_note": "未连接券商；请在下单前手工与券商账户核对持仓、可卖数量和净值日期。",
        "cash": None,
        "net_asset_value": None,
        "unit_nav": None,
        "holdings": [],
        "holding_snapshots": [],
        "nav_history": [],
        "nav_metrics": _empty_nav_metrics(),
        "t1": _empty_t1(),
        "scan_blocked": False,
    }


def _empty_nav_metrics() -> dict[str, Any]:
    return {
        "as_of": None,
        "current_nav_as_of": None,
        "basis": None,
        "basis_label": "暂无可计算净值口径",
        "current_nav": None,
        "peak_nav": None,
        "current_drawdown_pct": None,
        "max_drawdown_pct": None,
        "eligible_points": 0,
        "excluded_points": [],
        "quality": "insufficient_history",
        "note": "至少需要一个同口径净值点；含出入金的点需要单位净值，否则会被排除。",
    }


def _empty_t1() -> dict[str, Any]:
    return {
        "status": "insufficient_history",
        "t_allowed": False,
        "as_of": None,
        "reference_date": None,
        "reference_date_source": None,
        "validation_mode": None,
        "violations": [],
        "manual_check_required": True,
        "note": "缺少当日或前一日持仓快照，不能把T+1核验标记为通过。",
    }


class WildmanAccountService:
    """CRUD and audit calculations for one authenticated user's account."""

    async def _load_payload(self, username: str) -> dict[str, Any] | None:
        async with async_session() as session:
            row = await session.get(PersonalSystemConfig, account_key(username))
            return dict(row.payload) if row and isinstance(row.payload, dict) else None

    async def _save_payload(self, username: str, payload: dict[str, Any]) -> None:
        async with async_session() as session:
            key = account_key(username)
            row = await session.get(PersonalSystemConfig, key)
            if row is None:
                session.add(PersonalSystemConfig(key=key, payload=payload))
            else:
                row.payload = payload
            await session.commit()

    def _normalise_holding(
        self,
        item: Any,
        *,
        default_as_of: str,
        snapshot: bool = False,
    ) -> dict[str, Any]:
        if not isinstance(item, dict):
            raise ValueError("每条持仓必须是对象")
        symbol = _symbol(item.get("symbol", item.get("code")))
        quantity = _quantity(item.get("quantity", item.get("shares")), f"{symbol}数量")
        sellable = _quantity(
            item.get("sellable_quantity", item.get("sellable")),
            f"{symbol}可卖数量",
            default=quantity if snapshot else None,
        )
        if sellable > quantity:
            raise ValueError(f"{symbol}可卖数量不能大于持仓数量")
        previous_quantity = (
            _quantity(item.get("previous_quantity"), f"{symbol}前一交易日持仓")
            if item.get("previous_quantity") is not None and item.get("previous_quantity") != ""
            else None
        )
        bought_today_shares = (
            _quantity(item.get("bought_today_shares"), f"{symbol}今日新增数量")
            if item.get("bought_today_shares") is not None and item.get("bought_today_shares") != ""
            else None
        )
        if bought_today_shares is not None and bought_today_shares > quantity:
            raise ValueError(f"{symbol}今日新增数量不能大于持仓数量")
        return {
            "symbol": symbol,
            "name": str(item.get("name") or "").strip()[:100],
            "quantity": quantity,
            "sellable_quantity": sellable,
            "market_value": _optional_number(item.get("market_value"), f"{symbol}市值", minimum=0),
            "cost": _optional_number(item.get("cost"), f"{symbol}成本", minimum=0),
            "as_of": _date_text(item.get("as_of") or default_as_of, f"{symbol}持仓日期"),
            "source": _source(item.get("source")),
            "previous_quantity": previous_quantity,
            "bought_today_shares": bought_today_shares,
        }

    def _normalise_nav(self, item: Any, *, fallback_source: str) -> dict[str, Any]:
        if not isinstance(item, dict):
            raise ValueError("每条净值记录必须是对象")
        as_of = _date_text(item.get("as_of") or item.get("date"), "净值日期")
        net_asset_value = _optional_number(
            item.get("net_asset_value", item.get("nav")), "净资产值", minimum=0
        )
        unit_nav = _optional_number(item.get("unit_nav"), "单位净值", minimum=0)
        cash_in = _optional_number(item.get("cash_in"), "入金", minimum=0) or 0.0
        cash_out = _optional_number(item.get("cash_out"), "出金", minimum=0) or 0.0
        if net_asset_value is None and unit_nav is None:
            raise ValueError(f"{as_of}至少需要净资产值或单位净值")
        return {
            "as_of": as_of,
            "nav": net_asset_value,
            "net_asset_value": net_asset_value,
            "unit_nav": unit_nav,
            "cash_in": cash_in,
            "cash_out": cash_out,
            "source": _source(item.get("source") or fallback_source),
        }

    def _validate_dates_not_future(self, payload: dict[str, Any], today: date) -> None:
        for field in ("as_of",):
            if payload.get(field):
                value = _date_value(payload[field], field)
                if value > today:
                    raise ValueError(f"{field}不能晚于上海日期{today.isoformat()}")
        for collection, field in ((payload.get("holdings"), "持仓"), (payload.get("nav_history"), "净值记录"), (payload.get("holding_snapshots"), "持仓快照")):
            if not isinstance(collection, list):
                continue
            for item in collection:
                if not isinstance(item, dict):
                    continue
                value = item.get("as_of", item.get("date"))
                if value and _date_value(value, f"{field}日期") > today:
                    raise ValueError(f"{field}日期不能晚于上海日期{today.isoformat()}")

    def _normalise(
        self,
        incoming: dict[str, Any],
        *,
        previous_trading_date: str | None = None,
        previous_date_source: str | None = None,
        enforce_current_as_of: bool = False,
    ) -> dict[str, Any]:
        if not isinstance(incoming, dict):
            raise ValueError("账户数据必须是对象")
        today = _today()
        self._validate_dates_not_future(incoming, today)
        as_of = _date_text(incoming.get("as_of"), "as_of")
        if enforce_current_as_of and as_of != today.isoformat():
            raise ValueError(f"as_of必须是上海当日{today.isoformat()}，不能用过期快照做当日T")
        source = _source(incoming.get("source"))

        raw_holdings = incoming.get("holdings") or []
        if not isinstance(raw_holdings, list):
            raise ValueError("holdings必须是数组")
        if len(raw_holdings) > MAX_HOLDINGS:
            raise ValueError(f"持仓数量不能超过{MAX_HOLDINGS}条")
        holdings = [self._normalise_holding(item, default_as_of=as_of) for item in raw_holdings]
        if len({item["symbol"] for item in holdings}) != len(holdings):
            raise ValueError("持仓代码不能重复")

        raw_snapshots = incoming.get("holding_snapshots") or []
        if not isinstance(raw_snapshots, list):
            raise ValueError("holding_snapshots必须是数组")
        snapshots_by_date: dict[str, dict[str, Any]] = {}
        for raw in raw_snapshots:
            if not isinstance(raw, dict):
                raise ValueError("每条持仓快照必须是对象")
            snapshot_date = _date_text(raw.get("as_of", raw.get("date")), "持仓快照日期")
            snapshot_holdings = raw.get("holdings") or []
            if not isinstance(snapshot_holdings, list) or len(snapshot_holdings) > MAX_HOLDINGS:
                raise ValueError(f"每个持仓快照最多{MAX_HOLDINGS}条持仓")
            normalised_holdings = [
                self._normalise_holding(item, default_as_of=snapshot_date, snapshot=True)
                for item in snapshot_holdings
            ]
            if len({item["symbol"] for item in normalised_holdings}) != len(normalised_holdings):
                raise ValueError(f"{snapshot_date}持仓代码不能重复")
            snapshots_by_date[snapshot_date] = {
                "as_of": snapshot_date,
                "holdings": normalised_holdings,
                "source": _source(raw.get("source") or source),
            }
        holding_snapshots = [snapshots_by_date[key] for key in sorted(snapshots_by_date)[-MAX_HOLDING_SNAPSHOTS:]]

        raw_nav = incoming.get("nav_history") or []
        if not isinstance(raw_nav, list):
            raise ValueError("nav_history必须是数组")
        nav_by_date: dict[str, dict[str, Any]] = {}
        for raw in raw_nav:
            item = self._normalise_nav(raw, fallback_source=source)
            nav_by_date[item["as_of"]] = item
        top_net_asset_value = _optional_number(incoming.get("net_asset_value"), "净资产值", minimum=0)
        top_unit_nav = _optional_number(incoming.get("unit_nav"), "单位净值", minimum=0)
        if top_net_asset_value is not None or top_unit_nav is not None:
            nav_by_date[as_of] = {
                "as_of": as_of,
                "nav": top_net_asset_value,
                "net_asset_value": top_net_asset_value,
                "unit_nav": top_unit_nav,
                "cash_in": 0.0,
                "cash_out": 0.0,
                "source": source,
            }
        nav_history = [nav_by_date[key] for key in sorted(nav_by_date)[-MAX_NAV_DAYS:]]

        cash = _optional_number(incoming.get("cash"), "现金", minimum=0)
        payload: dict[str, Any] = {
            "version": ACCOUNT_VERSION,
            "as_of": as_of,
            "source": source,
            # This version intentionally has no broker adapter.
            "broker_connected": False,
            "broker_verified": False,
            "verification_status": "manual_user_input",
            "manual_reconciliation_required": True,
            "manual_reconciliation_note": "未连接券商；请在下单前手工与券商账户核对持仓、可卖数量和净值日期。",
            "cash": cash,
            "net_asset_value": top_net_asset_value,
            "unit_nav": top_unit_nav,
            "holdings": holdings,
            "holding_snapshots": holding_snapshots,
            "nav_history": nav_history,
            "review_metrics": incoming.get("review_metrics") if isinstance(incoming.get("review_metrics"), dict) else {},
            "scan_blocked": False,
        }
        payload["nav_metrics"] = self._nav_metrics(payload)
        payload["t1"] = self._t1_status(payload, today, previous_trading_date, previous_date_source)
        return payload

    def _nav_metrics(self, payload: dict[str, Any]) -> dict[str, Any]:
        history = payload.get("nav_history") or []
        if not history:
            metrics = _empty_nav_metrics()
            metrics["as_of"] = payload.get("as_of")
            return metrics
        has_cash_flow = any(float(item.get("cash_in") or 0) or float(item.get("cash_out") or 0) for item in history)
        all_have_unit = all(item.get("unit_nav") is not None for item in history)
        basis = "unit_nav" if has_cash_flow or all_have_unit else "net_asset_value"
        excluded: list[dict[str, str]] = []
        points: list[tuple[str, float]] = []
        for item in history:
            if item["as_of"] > payload["as_of"]:
                excluded.append({"as_of": item["as_of"], "reason": "净值记录晚于账户as_of，未参与回撤"})
                continue
            value = item.get(basis)
            if value is None or float(value) <= 0:
                reason = "含出入金但缺少单位净值" if has_cash_flow else "缺少同口径净值"
                excluded.append({"as_of": item["as_of"], "reason": reason})
                continue
            points.append((item["as_of"], float(value)))
        if not points:
            metrics = _empty_nav_metrics()
            metrics["as_of"] = payload.get("as_of")
            metrics["basis"] = basis
            metrics["basis_label"] = "单位净值" if basis == "unit_nav" else "净资产值"
            metrics["excluded_points"] = excluded
            metrics["note"] = "没有可用于回撤的同口径净值；含出入金的原始点已排除。"
            return metrics
        peak = points[0][1]
        max_drawdown = 0.0
        for _, value in points:
            peak = max(peak, value)
            max_drawdown = min(max_drawdown, (value / peak - 1) * 100)
        latest_date, latest_value = points[-1]
        is_current = latest_date == payload["as_of"]
        current_value = latest_value
        peak_value = max(value for _, value in points)
        current_drawdown = (current_value / peak_value - 1) * 100
        return {
            "as_of": payload.get("as_of"),
            "basis": basis,
            "basis_label": "单位净值" if basis == "unit_nav" else "净资产值",
            "current_nav": round(current_value, 8) if is_current else None,
            "current_nav_as_of": latest_date,
            "latest_nav": round(latest_value, 8),
            "peak_nav": round(peak_value, 8),
            "current_drawdown_pct": round(current_drawdown, 4) if is_current else None,
            "max_drawdown_pct": round(max_drawdown, 4),
            "eligible_points": len(points),
            "excluded_points": excluded,
            "quality": "partial" if excluded or not is_current else "complete",
            "note": (
                "最近有效净值早于账户as_of，已计算历史最大回撤但未把旧值冒充当前回撤。"
                if not is_current else "回撤使用同一净值口径。"
            ) + ("含出入金且没有单位净值的记录未参与计算。" if excluded else ""),
        }

    def _t1_status(
        self,
        payload: dict[str, Any],
        today: date,
        previous_trading_date: str | None,
        previous_date_source: str | None,
    ) -> dict[str, Any]:
        as_of = _date_value(payload["as_of"], "as_of")
        if as_of != today:
            return {
                "status": "stale",
                "t_allowed": False,
                "as_of": payload["as_of"],
                "reference_date": previous_trading_date,
                "reference_date_source": previous_date_source,
                "validation_mode": None,
                "violations": [],
                "manual_check_required": True,
                "note": "账户快照早于上海当日，过期快照不能允许当日T+1操作。",
            }
        snapshots = {item["as_of"]: item for item in payload.get("holding_snapshots") or []}
        reference_date = previous_trading_date
        previous = snapshots.get(reference_date)
        previous_by_symbol = {item["symbol"]: item for item in previous["holdings"]} if previous else {}
        violations: list[dict[str, Any]] = []
        manual_mode = False
        missing_history = False
        for item in payload.get("holdings") or []:
            previous_quantity = item.get("previous_quantity")
            bought_today = item.get("bought_today_shares")
            if previous_quantity is not None and bought_today is not None:
                manual_mode = True
            elif previous is not None:
                previous_quantity = int(previous_by_symbol.get(item["symbol"], {}).get("quantity", 0))
                bought_today = max(item["quantity"] - previous_quantity, 0)
            else:
                missing_history = True
                continue
            max_sellable = min(item["quantity"] - bought_today, previous_quantity)
            if max_sellable < 0 or item["sellable_quantity"] > max_sellable:
                violations.append({
                    "symbol": item["symbol"],
                    "sellable_quantity": item["sellable_quantity"],
                    "previous_quantity": previous_quantity,
                    "bought_today_shares": bought_today,
                    "max_sellable_quantity": max_sellable,
                    "reason": "可卖数量超过min(持仓数量-今日新增数量,前一交易日持仓)",
                })
        if missing_history:
            return {
                "status": "insufficient_history",
                "t_allowed": False,
                "as_of": payload["as_of"],
                "reference_date": reference_date,
                "reference_date_source": previous_date_source,
                "validation_mode": "manual_input" if manual_mode else None,
                "violations": violations,
                "manual_check_required": True,
                "note": "缺少前一交易日持仓快照或previous_quantity/bought_today_shares，不能确认T+1可卖数量。",
            }
        return {
            "status": "failed" if violations else "passed",
            "t_allowed": not violations,
            "as_of": payload["as_of"],
            "reference_date": reference_date,
            "reference_date_source": previous_date_source,
            "validation_mode": "manual_input" if manual_mode else "holding_snapshot",
            "violations": violations,
            "manual_check_required": True,
            "note": "T+1规则通过手工输入/快照形式校验；仍需用户手工与券商账户核对。" if not violations else "T+1规则未通过，今日新增股份不可卖。",
        }

    def _with_target(self, payload: dict[str, Any], target: str | None) -> dict[str, Any]:
        if not target:
            return payload
        symbol = _symbol(target)
        holding = next((item for item in payload["holdings"] if item["symbol"] == symbol), None)
        violation = next((item for item in payload["t1"]["violations"] if item["symbol"] == symbol), None)
        position = {
            "symbol": symbol,
            "held": holding is not None,
            "quantity": holding["quantity"] if holding else 0,
            "sellable_quantity": holding["sellable_quantity"] if holding else 0,
            "as_of": payload["as_of"],
            "source": holding["source"] if holding else payload["source"],
            "t_allowed": bool(payload["t1"]["t_allowed"] and violation is None and holding is not None),
            "manual_check_required": True,
            "note": "未连接券商；可卖数量为用户手工输入，不能称为券商已核验。",
        }
        return {**payload, "target": position}

    @staticmethod
    def _calendar_date(value: Any) -> date | None:
        text = str(value or "").strip()
        if len(text) >= 8 and text[:8].isdigit() and (len(text) == 8 or text[8] not in "-:"):
            text = f"{text[:4]}-{text[4:6]}-{text[6:8]}"
        try:
            return date.fromisoformat(text[:10])
        except (TypeError, ValueError):
            return None

    async def _resolve_previous_trading_date(
        self,
        as_of: date,
        *,
        snapshots: list[dict[str, Any]] | None = None,
        explicit: bool = False,
        supplied: Any = None,
    ) -> tuple[str | None, str | None]:
        if explicit:
            supplied_date = self._calendar_date(supplied)
            return (supplied_date.isoformat() if supplied_date else None), "manual_input_fields"
        try:
            rows = await numcat_extended_provider.calendar(
                mode="by_date",
                params={"tradedate": as_of.strftime("%Y%m%d")},
            )
            for row in rows:
                if not isinstance(row, dict):
                    continue
                for key in ("pretrade_date", "prev_trade_date", "previous_trade_date", "pretrade", "prev_tradedate"):
                    previous = self._calendar_date(row.get(key))
                    if previous and previous < as_of and previous.weekday() < 5:
                        return previous.isoformat(), "numcat_tradecal"
            observed = []
            for row in rows:
                if not isinstance(row, dict):
                    continue
                is_open = row.get("is_open", row.get("is_trading", row.get("open")))
                if is_open in {False, 0, "0", "false", "False"}:
                    continue
                candidate = self._calendar_date(row.get("cal_date") or row.get("trade_date") or row.get("tradedate"))
                if candidate and candidate < as_of and candidate.weekday() < 5:
                    observed.append(candidate)
            if observed:
                return max(observed).isoformat(), "numcat_tradecal"
        except Exception:
            # Account entry must remain usable when the calendar provider is
            # unavailable, but the fallback is deliberately disclosed below.
            pass

        snapshot_dates = []
        for item in snapshots or []:
            candidate = self._calendar_date(item.get("as_of") if isinstance(item, dict) else None)
            if candidate and candidate < as_of and candidate.weekday() < 5:
                snapshot_dates.append(candidate)
        if snapshot_dates:
            return max(snapshot_dates).isoformat(), "stored_snapshot_latest_before_as_of"

        candidate = as_of - timedelta(days=1)
        while candidate.weekday() >= 5:
            candidate -= timedelta(days=1)
        return candidate.isoformat(), "weekday_fallback_not_holiday_aware"

    @staticmethod
    def _has_manual_t1_fields(payload: dict[str, Any]) -> bool:
        holdings = payload.get("holdings")
        return bool(
            isinstance(holdings, list)
            and holdings
            and all(
                isinstance(item, dict)
                and item.get("previous_quantity") is not None
                and item.get("bought_today_shares") is not None
                for item in holdings
            )
        )

    async def get_account(self, username: str, target: str | None = None) -> dict[str, Any]:
        stored = await self._load_payload(username)
        if stored is None:
            return self._with_target(_default_account(), target)
        # Recalculate against current Shanghai date on every read so stale
        # state cannot remain accidentally usable after midnight.
        as_of = _date_value(stored.get("as_of"), "as_of")
        previous_date, previous_source = await self._resolve_previous_trading_date(
            as_of,
            snapshots=stored.get("holding_snapshots"),
            explicit=self._has_manual_t1_fields(stored),
            supplied=stored.get("previous_trading_date"),
        )
        return self._with_target(
            self._normalise(
                stored,
                previous_trading_date=previous_date,
                previous_date_source=previous_source,
            ),
            target,
        )

    async def update_account(self, username: str, incoming: dict[str, Any]) -> dict[str, Any]:
        as_of = _date_value(incoming.get("as_of"), "as_of")
        previous_date, previous_source = await self._resolve_previous_trading_date(
            as_of,
            snapshots=incoming.get("holding_snapshots"),
            explicit=self._has_manual_t1_fields(incoming),
            supplied=incoming.get("previous_trading_date"),
        )
        payload = self._normalise(
            incoming,
            previous_trading_date=previous_date,
            previous_date_source=previous_source,
            enforce_current_as_of=True,
        )
        await self._save_payload(username, payload)
        return payload


wildman_account_service = WildmanAccountService()


__all__ = [
    "ACCOUNT_KEY_PREFIX",
    "ACCOUNT_VERSION",
    "MANUAL_SOURCE",
    "MAX_HOLDINGS",
    "MAX_NAV_DAYS",
    "WildmanAccountService",
    "account_key",
    "wildman_account_service",
]
