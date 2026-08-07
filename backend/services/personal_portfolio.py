"""Personal investment workspace backed by the verified market data path."""

from __future__ import annotations

import asyncio
from collections import defaultdict
from datetime import date, datetime
from typing import Any

from sqlalchemy import select

from database import async_session
from models import PersonalInvestmentLog, PersonalPoolItem, PersonalSystemConfig, StockDailyBar
from services.a_stock_data import calculate_indicators
from services.data_collector import collector, normalize_stock_code, shanghai_now
from services.quote_cache import quote_snapshot_service


POOL_DEFINITIONS: dict[str, dict[str, Any]] = {
    "core": {"label": "核心持仓池", "description": "3-5年重点持有，必须有成本和退出纪律", "max_count": 10},
    "watchlist": {"label": "长期观察池", "description": "持续研究，等待估值和买点", "max_count": 30},
    "leaders": {"label": "行业龙头池", "description": "每个行业保留1-2个研究锚点", "max_count": 30},
    "etf": {"label": "ETF池", "description": "用宽基、行业和主题ETF覆盖方向", "max_count": 20},
    "blacklist": {"label": "黑名单", "description": "明确不投资的标的和规则", "max_count": None},
}
POOL_ALIASES = {
    **{key: key for key in POOL_DEFINITIONS},
    **{value["label"]: key for key, value in POOL_DEFINITIONS.items()},
    "核心持仓": "core",
    "长期观察": "watchlist",
    "行业龙头": "leaders",
    "ETF": "etf",
}
LOG_ACTIONS = {"buy", "sell", "hold", "review", "move"}
DELETION_CONFIG_KEY = "personal_pool_deletions_v1"


def _deletion_token(pool_key: str, code: str) -> str:
    return f"{pool_key}:{code}"


async def _update_deletion_tokens(
    session,
    *,
    add: set[str] | None = None,
    remove: set[str] | None = None,
) -> None:
    row = await session.get(PersonalSystemConfig, DELETION_CONFIG_KEY)
    payload = dict(row.payload) if row and isinstance(row.payload, dict) else {}
    tokens = {str(item) for item in payload.get("items") or [] if item}
    tokens.update(add or set())
    tokens.difference_update(remove or set())
    updated = {
        "items": sorted(tokens),
        "updated_at": datetime.utcnow().isoformat(),
    }
    if row is None:
        session.add(PersonalSystemConfig(key=DELETION_CONFIG_KEY, payload=updated))
    else:
        row.payload = updated


def normalize_pool(value: object, default: str = "watchlist") -> str:
    candidate = str(value or "").strip()
    if not candidate:
        return default
    key = POOL_ALIASES.get(candidate)
    if key is None:
        raise ValueError("股票池必须是 core、watchlist、leaders、etf 或 blacklist")
    return key


def _number(value: object) -> float | None:
    if value in (None, "", "-"):
        return None
    try:
        result = float(value)
        return result if result == result else None
    except (TypeError, ValueError):
        return None


def _integer(value: object) -> int | None:
    number = _number(value)
    return int(number) if number is not None else None


def _date(value: object) -> date | None:
    if not value:
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        raise ValueError("日期必须使用 YYYY-MM-DD 格式")


def _date_text(value: date | None) -> str | None:
    return value.isoformat() if value else None


def _datetime_text(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _clean_targets(value: object) -> list[float]:
    if value in (None, ""):
        return []
    if not isinstance(value, (list, tuple)):
        raise ValueError("目标价必须是数字数组")
    return [round(number, 4) for item in value if (number := _number(item)) is not None and number > 0]


def _quote_time(value: object) -> str | None:
    parsed = collector._quote_timestamp_datetime(value)
    return parsed.isoformat() if parsed else None


def _basic_item(row: PersonalPoolItem) -> dict[str, Any]:
    return {
        "id": row.id,
        "pool": row.pool_key,
        "pool_label": POOL_DEFINITIONS.get(row.pool_key, {}).get("label", row.pool_key),
        "code": row.code,
        "name": row.name,
        "asset_type": row.asset_type,
        "industry": row.industry or "",
        "status": row.status,
        "cost": row.cost,
        "entry_date": _date_text(row.entry_date),
        "position_pct": row.position_pct,
        "stop_loss": row.stop_loss,
        "targets": list(row.targets or []),
        "max_position": row.max_position,
        "thesis": row.thesis or "",
        "risk_note": row.risk_note or "",
        "warning": row.warning or "",
        "etf_type": row.etf_type or "",
        "tags": list(row.tags or []),
        "source": row.source,
        "created_at": _datetime_text(row.created_at),
        "updated_at": _datetime_text(row.updated_at),
        "code_verified": True,
    }


def _enrich_item(
    row: PersonalPoolItem,
    quote: dict[str, Any] | None,
    technical: dict[str, Any] | None,
) -> dict[str, Any]:
    item = _basic_item(row)
    quote = quote or {}
    price = _number(quote.get("price"))
    cost = _number(row.cost)
    stop_loss = _number(row.stop_loss)
    targets = [number for value in (row.targets or []) if (number := _number(value)) is not None and number > 0]
    item.update({
        "live_name": str(quote.get("name") or ""),
        "display_name": str(quote.get("name") or row.name),
        "sector": str(quote.get("sector") or row.industry or ""),
        "price": price,
        "change_pct": _number(quote.get("change_pct")),
        "change_amount": _number(quote.get("change_amount")),
        "previous_close": _number(quote.get("previous_close")),
        "high": _number(quote.get("high")),
        "low": _number(quote.get("low")),
        "turnover": _number(quote.get("turnover")),
        "pe": _number(quote.get("pe")),
        "pb": _number(quote.get("pb")),
        "market_cap": _number(quote.get("market_cap")),
        "quote_timestamp": _quote_time(quote.get("quote_timestamp")),
        "quote_available": price is not None and price > 0,
        "name_verified": bool(quote.get("name")),
        "technical": technical or {},
    })
    item["pnl_pct"] = ((price - cost) / cost * 100) if price is not None and cost and cost > 0 else None
    item["stop_distance_pct"] = ((price - stop_loss) / price * 100) if price and stop_loss else None
    item["stop_state"] = (
        "triggered" if price is not None and stop_loss is not None and price <= stop_loss
        else "near" if price is not None and stop_loss is not None and price <= stop_loss * 1.03
        else "normal"
    )
    reached_targets = [target for target in targets if price is not None and price >= target]
    item["reached_targets"] = reached_targets
    item["next_target"] = min((target for target in targets if target > (price or 0)), default=None)
    item["target_distance_pct"] = (
        (item["next_target"] - price) / price * 100
        if item["next_target"] is not None and price else None
    )
    return item


async def _load_history(codes: list[str]) -> dict[str, dict]:
    if not codes:
        return {}
    try:
        async with async_session() as session:
            rows = (await session.execute(
                select(StockDailyBar)
                .where(StockDailyBar.stock_code.in_(codes))
                .order_by(StockDailyBar.stock_code.asc(), StockDailyBar.trade_date.desc())
            )).scalars().all()
    except Exception as exc:
        print(f"Personal technical history unavailable: {type(exc).__name__}")
        return {}

    grouped: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        bucket = grouped[row.stock_code]
        if len(bucket) >= 90:
            continue
        bucket.append({
            "trade_date": _date_text(row.trade_date),
            "open": row.open_price,
            "close": row.close_price,
            "high": row.high_price,
            "low": row.low_price,
            "volume": row.volume,
        })
    return {
        code: calculate_indicators(list(reversed(history)))
        for code, history in grouped.items()
        if history
    }


def _health(items: list[dict[str, Any]], quote_meta: dict[str, Any]) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []
    pool_counts: dict[str, int] = defaultdict(int)
    for item in items:
        pool_counts[item["pool"]] += 1

    for key, definition in POOL_DEFINITIONS.items():
        count = pool_counts.get(key, 0)
        limit = definition.get("max_count")
        passed = limit is None or count <= limit
        checks.append({
            "id": f"count_{key}",
            "label": f"{definition['label']}数量",
            "status": "ok" if passed else "danger",
            "detail": f"{count} / {limit if limit is not None else '不限'}",
        })
        if not passed:
            issues.append({"level": "danger", "title": definition["label"] + "超限", "detail": f"当前 {count} 项，建议控制在 {limit} 项以内"})

    held = [
        item for item in items
        if item["position_pct"] is not None and item["position_pct"] > 0
        and item["status"] in {"holding", "reduce"}
    ]
    total_position = sum(item["position_pct"] for item in held) if held else None
    cash_reserve = 100 - total_position if total_position is not None else None
    missing_controls = [item for item in held if item["stop_loss"] is None or not item["targets"]]
    controls_ok = not missing_controls
    checks.append({
        "id": "risk_controls",
        "label": "持仓退出纪律",
        "status": "ok" if controls_ok else "warning",
        "detail": "所有持仓都有止损和目标" if controls_ok else f"{len(missing_controls)} 项缺少止损或目标价",
    })
    if missing_controls:
        issues.append({"level": "warning", "title": "持仓风控参数不完整", "detail": "、".join(item["display_name"] for item in missing_controls)})

    sector_weights: dict[str, float] = defaultdict(float)
    for item in held:
        sector_weights[item["industry"] or item["sector"] or "未分类"] += item["position_pct"]
    concentration = sorted(
        [{"sector": sector, "position_pct": round(weight, 2)} for sector, weight in sector_weights.items()],
        key=lambda value: value["position_pct"], reverse=True,
    )
    top_sector = concentration[0] if concentration else None
    concentration_ok = not top_sector or top_sector["position_pct"] <= 40
    checks.append({
        "id": "concentration",
        "label": "行业集中度",
        "status": "ok" if concentration_ok else "warning",
        "detail": f"{top_sector['sector']} {top_sector['position_pct']:.1f}%" if top_sector else "暂无持仓仓位数据",
    })
    if top_sector and not concentration_ok:
        issues.append({"level": "warning", "title": "行业集中度偏高", "detail": f"{top_sector['sector']} 占记录仓位 {top_sector['position_pct']:.1f}%"})

    cash_ok = cash_reserve is None or cash_reserve >= 20
    checks.append({
        "id": "cash_reserve",
        "label": "现金安全垫",
        "status": "ok" if cash_ok else "warning",
        "detail": f"{cash_reserve:.1f}%" if cash_reserve is not None else "未记录仓位",
    })
    if cash_reserve is not None and not cash_ok:
        issues.append({"level": "warning", "title": "现金安全垫不足", "detail": f"按记录仓位剩余 {cash_reserve:.1f}%，低于投资宪法建议的20%"})

    unavailable = [item for item in items if not item["quote_available"]]
    if unavailable:
        issues.append({"level": "info", "title": "部分行情未返回", "detail": f"{len(unavailable)} 项暂时没有可验证现价，未据此生成风险结论"})

    score = 100
    score -= min(30, 15 * sum(check["status"] == "danger" for check in checks))
    score -= min(25, 12 * sum(check["status"] == "warning" for check in checks))
    score -= min(15, len(unavailable) * 2)
    score = max(0, score)
    level = "良好" if score >= 85 else "需关注" if score >= 65 else "需整改"
    return {
        "score": score,
        "level": level,
        "pool_counts": dict(pool_counts),
        "holding_count": len(held),
        "total_position_pct": round(total_position, 2) if total_position is not None else None,
        "cash_reserve_pct": round(cash_reserve, 2) if cash_reserve is not None else None,
        "concentration": concentration,
        "checks": checks,
        "issues": issues,
        "quote_complete": quote_meta.get("complete", False),
    }


def _alerts(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    alerts: list[dict[str, Any]] = []
    for item in items:
        if item["stop_state"] == "triggered":
            alerts.append({"level": "danger", "type": "stop_loss", "code": item["code"], "name": item["display_name"], "message": f"现价已低于或触及止损价 ¥{item['stop_loss']:.2f}，请先复核投资逻辑"})
        elif item["stop_state"] == "near":
            alerts.append({"level": "warning", "type": "stop_loss_near", "code": item["code"], "name": item["display_name"], "message": f"距离止损价约3%以内（止损 ¥{item['stop_loss']:.2f}）"})
        if item["reached_targets"]:
            targets = "、".join(f"¥{target:.2f}" for target in item["reached_targets"])
            alerts.append({"level": "info", "type": "target", "code": item["code"], "name": item["display_name"], "message": f"已达到记录目标 {targets}，按计划复核仓位，不自动交易"})
        if item["warning"]:
            alerts.append({"level": "warning", "type": "thesis", "code": item["code"], "name": item["display_name"], "message": item["warning"]})
        change = item.get("change_pct")
        if change is not None and abs(change) >= 5:
            alerts.append({"level": "warning", "type": "volatility", "code": item["code"], "name": item["display_name"], "message": f"当日波动 {change:+.2f}%，需要结合公告和资金流复核"})
    order = {"danger": 0, "warning": 1, "info": 2}
    return sorted(alerts, key=lambda item: order.get(item["level"], 9))


class PersonalPortfolioService:
    async def _rows(self) -> tuple[list[PersonalPoolItem], dict]:
        async with async_session() as session:
            rows = (await session.execute(
                select(PersonalPoolItem).order_by(PersonalPoolItem.pool_key.asc(), PersonalPoolItem.id.asc())
            )).scalars().all()
            config = await session.get(PersonalSystemConfig, "default")
        return list(rows), (config.payload if config and isinstance(config.payload, dict) else {})

    async def overview(self) -> dict[str, Any]:
        rows, config = await self._rows()
        codes = list(dict.fromkeys(row.code for row in rows))
        quotes: dict[str, dict] = {}
        quote_error = None
        quote_payload: dict[str, Any] = {}
        quote_result, technical_result = await asyncio.gather(
            quote_snapshot_service.fetch(codes, async_session) if codes else asyncio.sleep(0, result={}),
            _load_history(codes),
            return_exceptions=True,
        )
        if isinstance(quote_result, Exception):
            quote_error = type(quote_result).__name__
        else:
            quote_payload = quote_result
            quotes = {str(item["code"]): item for item in quote_payload.get("stocks") or []}
        technical = {} if isinstance(technical_result, Exception) else technical_result
        items = [_enrich_item(row, quotes.get(row.code), technical.get(row.code)) for row in rows]
        quote_meta = {
            "available": bool(quotes),
            "source": quote_payload.get("source", "eastmoney"),
            "data_date": quote_payload.get("data_date"),
            "source_updated_at": quote_payload.get("source_updated_at"),
            "is_realtime": bool(quote_payload.get("is_realtime")),
            "fetched_at": quote_payload.get("fetched_at") or shanghai_now().isoformat(),
            "complete": bool(codes) and len(quotes) == len(codes),
            "cache_used": bool(quote_payload.get("cache_used")),
            "stale": bool(quote_payload.get("stale")),
            "error": quote_error,
        }

        pools = []
        for key, definition in POOL_DEFINITIONS.items():
            pool_items = [item for item in items if item["pool"] == key]
            pools.append({
                "key": key,
                **definition,
                "count": len(pool_items),
                "items": pool_items,
            })
        health = _health(items, quote_meta)
        return {
            "items": items,
            "pools": pools,
            "summary": {
                "total_items": len(items),
                "holding_count": health["holding_count"],
                "watch_count": sum(1 for item in items if item["pool"] == "watchlist"),
                "etf_count": sum(1 for item in items if item["pool"] == "etf"),
                "total_position_pct": health["total_position_pct"],
                "cash_reserve_pct": health["cash_reserve_pct"],
                "health_score": health["score"],
            },
            "health": health,
            "alerts": _alerts(items),
            "quote": quote_meta,
            "config": config,
            "disclaimer": "个人池数据用于研究和纪律管理，不构成投资建议；实时行情可能存在源端延迟。",
        }

    async def create_item(self, payload: dict[str, Any]) -> dict[str, Any]:
        code = normalize_stock_code(payload.get("code"))
        pool_key = normalize_pool(payload.get("pool") or payload.get("pool_key"))
        name = str(payload.get("name") or code).strip()
        if not name:
            raise ValueError("股票名称不能为空")
        values = self._item_values(payload, code=code, pool_key=pool_key, name=name)
        async with async_session() as session:
            existing = (await session.execute(
                select(PersonalPoolItem).where(
                    PersonalPoolItem.pool_key == pool_key,
                    PersonalPoolItem.code == code,
                )
            )).scalar_one_or_none()
            if existing:
                # Cross-module "add" is idempotent: preserve manual controls,
                # while refreshing the evidence that led to the add action.
                for field in ("name", "industry", "thesis", "source"):
                    if values.get(field):
                        setattr(existing, field, values[field])
                await _update_deletion_tokens(
                    session,
                    remove={_deletion_token(pool_key, code)},
                )
                await session.commit()
                return {"item": _basic_item(existing), "created": False}
            row = PersonalPoolItem(**values)
            session.add(row)
            await _update_deletion_tokens(
                session,
                remove={_deletion_token(pool_key, code)},
            )
            await session.commit()
            await session.refresh(row)
            return {"item": _basic_item(row), "created": True}

    async def update_item(self, item_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        async with async_session() as session:
            row = await session.get(PersonalPoolItem, item_id)
            if row is None:
                raise LookupError("个人池条目不存在")
            code = normalize_stock_code(payload.get("code", row.code))
            pool_key = normalize_pool(payload.get("pool", payload.get("pool_key", row.pool_key)), row.pool_key)
            duplicate = (await session.execute(
                select(PersonalPoolItem).where(
                    PersonalPoolItem.pool_key == pool_key,
                    PersonalPoolItem.code == code,
                    PersonalPoolItem.id != item_id,
                )
            )).scalar_one_or_none()
            if duplicate:
                raise ValueError("同一股票已经在目标股票池中")
            values = self._item_values(payload, code=code, pool_key=pool_key, name=str(payload.get("name") or row.name))
            original_token = _deletion_token(row.pool_key, row.code)
            target_token = _deletion_token(pool_key, code)
            provided_fields = set(payload)
            if "pool" in provided_fields:
                provided_fields.add("pool_key")
            if "logic" in provided_fields:
                provided_fields.add("thesis")
            if "risk" in provided_fields:
                provided_fields.add("risk_note")
            if "sector" in provided_fields:
                provided_fields.add("industry")
            for field, value in values.items():
                if field in provided_fields or field in {"code"}:
                    setattr(row, field, value)
            await _update_deletion_tokens(
                session,
                add={original_token} if original_token != target_token else set(),
                remove={target_token},
            )
            await session.commit()
            await session.refresh(row)
            return _basic_item(row)

    async def delete_item(self, item_id: int) -> None:
        async with async_session() as session:
            row = await session.get(PersonalPoolItem, item_id)
            if row is None:
                raise LookupError("个人池条目不存在")
            await _update_deletion_tokens(
                session,
                add={_deletion_token(row.pool_key, row.code)},
            )
            await session.delete(row)
            await session.commit()

    async def move_to_watchlist(self, item_id: int) -> dict[str, Any]:
        return await self.update_item(item_id, {"pool": "watchlist", "status": "watching"})

    async def list_logs(self, limit: int = 30) -> list[dict[str, Any]]:
        async with async_session() as session:
            rows = (await session.execute(
                select(PersonalInvestmentLog)
                .order_by(PersonalInvestmentLog.created_at.desc())
                .limit(max(1, min(limit, 100)))
            )).scalars().all()
        return [self._log_dict(row) for row in rows]

    async def create_log(self, payload: dict[str, Any]) -> dict[str, Any]:
        action = str(payload.get("action") or "review").strip()
        if action not in LOG_ACTIONS:
            raise ValueError("日志动作必须是 buy、sell、hold、review 或 move")
        reason = str(payload.get("reason") or "").strip()
        if not reason:
            raise ValueError("投资日志必须填写理由")
        code = None
        if payload.get("code"):
            code = normalize_stock_code(payload.get("code"))
        async with async_session() as session:
            row = PersonalInvestmentLog(
                action=action,
                code=code,
                name=str(payload.get("name") or "").strip() or None,
                price=_number(payload.get("price")),
                shares=_integer(payload.get("shares")),
                reason=reason,
                pre_check=payload.get("pre_check") if isinstance(payload.get("pre_check"), dict) else {},
                violations=payload.get("violations") if isinstance(payload.get("violations"), list) else [],
                reflection=str(payload.get("reflection") or "").strip() or None,
            )
            session.add(row)
            await session.commit()
            await session.refresh(row)
        return self._log_dict(row)

    @staticmethod
    def _log_dict(row: PersonalInvestmentLog) -> dict[str, Any]:
        return {
            "id": row.id,
            "action": row.action,
            "code": row.code,
            "name": row.name or "",
            "price": row.price,
            "shares": row.shares,
            "reason": row.reason,
            "pre_check": row.pre_check or {},
            "violations": row.violations or [],
            "reflection": row.reflection or "",
            "created_at": _datetime_text(row.created_at),
        }

    @staticmethod
    def _item_values(payload: dict[str, Any], *, code: str, pool_key: str, name: str) -> dict[str, Any]:
        targets = _clean_targets(payload.get("targets", []))
        return {
            "pool_key": pool_key,
            "code": code,
            "name": name,
            "asset_type": str(payload.get("asset_type") or ("etf" if pool_key == "etf" else "stock")),
            "industry": str(payload.get("industry") or payload.get("sector") or "").strip(),
            "status": str(payload.get("status") or ("watching" if pool_key == "watchlist" else "planned")).strip(),
            "cost": _number(payload.get("cost")),
            "entry_date": _date(payload.get("entry_date")),
            "position_pct": _number(payload.get("position_pct")),
            "stop_loss": _number(payload.get("stop_loss")),
            "targets": targets,
            "max_position": _number(payload.get("max_position")),
            "thesis": str(payload.get("thesis") or payload.get("logic") or "").strip(),
            "risk_note": str(payload.get("risk_note") or payload.get("risk") or "").strip(),
            "warning": str(payload.get("warning") or "").strip(),
            "etf_type": str(payload.get("etf_type") or payload.get("type") or "").strip() or None,
            "tags": payload.get("tags") if isinstance(payload.get("tags"), list) else [],
            "source": str(payload.get("source") or "user").strip(),
        }


personal_portfolio_service = PersonalPortfolioService()
