"""Position controls and evidence-based performance attribution for the personal workspace."""

from __future__ import annotations

import math
from collections import defaultdict, deque
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import select

from database import async_session
from models import PersonalInvestmentLog, PersonalSystemConfig, StockDailyBar
from services.data_collector import collector, shanghai_now
from services.personal_portfolio import personal_portfolio_service


DEFAULT_ACCOUNT_CONFIG = {
    "total_assets": None,
    "equity_ceiling_pct": 70.0,
    "cash_floor_pct": 20.0,
    "single_stock_limit_pct": 30.0,
    "sector_limit_pct": 30.0,
    "daily_add_limit_pct": 10.0,
    "loss_add_block_pct": -5.0,
    "benchmark": "上证指数",
}


def _number(value: object) -> float | None:
    if value in (None, "", "-"):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _rounded(value: float | None, digits: int = 2) -> float | None:
    return round(value, digits) if value is not None and math.isfinite(value) else None


def _period_start(period: str, today: date) -> tuple[str, date, str]:
    normalized = str(period or "year").lower()
    if normalized == "3m":
        return normalized, today - timedelta(days=92), "近3个月"
    if normalized == "12m":
        return normalized, today - timedelta(days=365), "近12个月"
    return "year", date(today.year, 1, 1), f"{today.year}年至今"


class PersonalAnalyticsService:
    async def account_config(self) -> dict[str, Any]:
        async with async_session() as session:
            row = await session.get(PersonalSystemConfig, "account")
        payload = row.payload if row and isinstance(row.payload, dict) else {}
        return {**DEFAULT_ACCOUNT_CONFIG, **payload}

    async def update_account_config(self, payload: dict[str, Any]) -> dict[str, Any]:
        current = await self.account_config()
        allowed = set(DEFAULT_ACCOUNT_CONFIG)
        for key in allowed:
            if key not in payload:
                continue
            if key == "benchmark":
                current[key] = str(payload[key] or "上证指数").strip()[:30]
                continue
            value = _number(payload[key])
            if key == "total_assets":
                if value is not None and value <= 0:
                    raise ValueError("总资产必须大于0")
            elif key == "loss_add_block_pct":
                if value is None or not -100 <= value <= 0:
                    raise ValueError("loss_add_block_pct 必须在-100到0之间")
            elif value is None or not 0 <= value <= 100:
                raise ValueError(f"{key} 必须在0到100之间")
            current[key] = value

        if current["cash_floor_pct"] > 100:
            raise ValueError("现金下限不能超过100%")
        async with async_session() as session:
            row = await session.get(PersonalSystemConfig, "account")
            if row is None:
                session.add(PersonalSystemConfig(key="account", payload=current))
            else:
                row.payload = current
            await session.commit()
        return current

    @staticmethod
    async def _portfolio_series(positions: list[dict], days: int = 380) -> dict[str, Any]:
        weights = {
            item["code"]: float(item["position_pct"]) / 100
            for item in positions
            if _number(item.get("position_pct")) not in (None, 0)
        }
        if not weights:
            return {"data_points": 0, "max_drawdown_pct": None, "volatility_pct": None, "sharpe": None}
        cutoff = shanghai_now().date() - timedelta(days=days)
        async with async_session() as session:
            rows = (await session.execute(
                select(StockDailyBar)
                .where(StockDailyBar.stock_code.in_(weights), StockDailyBar.trade_date >= cutoff)
                .order_by(StockDailyBar.stock_code, StockDailyBar.trade_date)
            )).scalars().all()

        by_code: dict[str, list[tuple[date, float]]] = defaultdict(list)
        for row in rows:
            if row.close_price is not None and row.close_price > 0:
                by_code[row.stock_code].append((row.trade_date, float(row.close_price)))
        daily: dict[date, float] = defaultdict(float)
        for code, history in by_code.items():
            for index in range(1, len(history)):
                previous = history[index - 1][1]
                current = history[index][1]
                if previous > 0:
                    daily[history[index][0]] += weights.get(code, 0) * (current / previous - 1)
        returns = [daily[key] for key in sorted(daily)]
        if len(returns) < 2:
            return {"data_points": len(returns), "max_drawdown_pct": None, "volatility_pct": None, "sharpe": None}

        equity = 1.0
        peak = 1.0
        max_drawdown = 0.0
        for value in returns:
            equity *= 1 + value
            peak = max(peak, equity)
            if peak > 0:
                max_drawdown = min(max_drawdown, equity / peak - 1)
        average = sum(returns) / len(returns)
        variance = sum((value - average) ** 2 for value in returns) / max(1, len(returns) - 1)
        daily_std = math.sqrt(max(0.0, variance))
        volatility = daily_std * math.sqrt(252) * 100
        annual_return = average * 252
        sharpe = (annual_return - 0.02) / (daily_std * math.sqrt(252)) if daily_std > 0 else None
        return {
            "data_points": len(returns),
            "max_drawdown_pct": round(max_drawdown * 100, 2),
            "volatility_pct": round(volatility, 2),
            "sharpe": round(sharpe, 2) if sharpe is not None else None,
        }

    async def allocation(self) -> dict[str, Any]:
        overview = await personal_portfolio_service.overview()
        config = await self.account_config()
        positions = [
            item for item in overview["items"]
            if item.get("status") in {"holding", "reduce"}
            and (_number(item.get("position_pct")) or 0) > 0
        ]
        total_assets = _number(config.get("total_assets"))
        equity_pct = round(sum(float(item["position_pct"]) for item in positions), 2)
        cash_pct = round(100 - equity_pct, 2)
        industry_weights: dict[str, float] = defaultdict(float)
        position_rows = []
        checks = []

        for item in positions:
            weight = float(item["position_pct"])
            industry = item.get("industry") or item.get("sector") or "未分类"
            industry_weights[industry] += weight
            single_ok = weight <= float(config["single_stock_limit_pct"])
            checks.append({
                "rule": "single_stock",
                "code": item["code"],
                "label": f"{item['display_name']} 单股仓位",
                "value": weight,
                "limit": float(config["single_stock_limit_pct"]),
                "status": "ok" if single_ok else "danger",
                "detail": f"{weight:.1f}% / 上限 {float(config['single_stock_limit_pct']):.1f}%",
            })
            pnl_pct = _number(item.get("pnl_pct"))
            add_allowed = pnl_pct is None or pnl_pct > float(config["loss_add_block_pct"])
            position_rows.append({
                "code": item["code"],
                "name": item["display_name"],
                "industry": industry,
                "weight_pct": weight,
                "market_value": round(total_assets * weight / 100, 2) if total_assets else None,
                "price": item.get("price"),
                "pnl_pct": pnl_pct,
                "add_allowed": add_allowed,
                "add_block_reason": None if add_allowed else f"当前记录浮亏 {pnl_pct:.1f}%，触发亏损加仓限制",
            })

        industry_rows = []
        for industry, weight in sorted(industry_weights.items(), key=lambda row: row[1], reverse=True):
            passed = weight <= float(config["sector_limit_pct"])
            industry_rows.append({"industry": industry, "weight_pct": round(weight, 2), "status": "ok" if passed else "danger"})
            checks.append({
                "rule": "sector",
                "label": f"{industry} 行业仓位",
                "value": round(weight, 2),
                "limit": float(config["sector_limit_pct"]),
                "status": "ok" if passed else "danger",
                "detail": f"{weight:.1f}% / 上限 {float(config['sector_limit_pct']):.1f}%",
            })

        checks.extend([
            {
                "rule": "equity_ceiling",
                "label": "股票总仓位",
                "value": equity_pct,
                "limit": float(config["equity_ceiling_pct"]),
                "status": "ok" if equity_pct <= float(config["equity_ceiling_pct"]) else "danger",
                "detail": f"{equity_pct:.1f}% / 上限 {float(config['equity_ceiling_pct']):.1f}%",
            },
            {
                "rule": "cash_floor",
                "label": "现金安全垫",
                "value": cash_pct,
                "limit": float(config["cash_floor_pct"]),
                "status": "ok" if cash_pct >= float(config["cash_floor_pct"]) else "danger",
                "detail": f"{cash_pct:.1f}% / 下限 {float(config['cash_floor_pct']):.1f}%",
            },
        ])

        today = shanghai_now().date()
        async with async_session() as session:
            buy_logs = (await session.execute(
                select(PersonalInvestmentLog).where(PersonalInvestmentLog.action == "buy")
            )).scalars().all()
        today_buys: dict[str, float] = defaultdict(float)
        for row in buy_logs:
            if not row.created_at or row.created_at.date() != today or not row.code:
                continue
            if row.price is not None and row.shares is not None:
                today_buys[row.code] += float(row.price) * int(row.shares)
        daily_add = []
        for code, amount in today_buys.items():
            pct = amount / total_assets * 100 if total_assets else None
            daily_add.append({
                "code": code,
                "amount": round(amount, 2),
                "pct": _rounded(pct),
                "status": "ok" if pct is None or pct <= float(config["daily_add_limit_pct"]) else "danger",
            })

        metrics = await self._portfolio_series(positions)
        danger_checks = [item for item in checks if item["status"] == "danger"]
        advice = [
            item["detail"] for item in danger_checks[:4]
        ] or ["当前记录仓位未触发硬性上限；交易前仍需复核公告、流动性和止损。"]
        return {
            "account": {
                "total_assets": total_assets,
                "equity_value": round(total_assets * equity_pct / 100, 2) if total_assets else None,
                "cash_value": round(total_assets * cash_pct / 100, 2) if total_assets else None,
                "equity_pct": equity_pct,
                "cash_pct": cash_pct,
                "configured": total_assets is not None,
            },
            "limits": config,
            "positions": position_rows,
            "industries": industry_rows,
            "checks": checks,
            "daily_additions": daily_add,
            "risk_metrics": metrics,
            "new_buy_blocked": any(item["rule"] in {"equity_ceiling", "cash_floor"} and item["status"] == "danger" for item in checks),
            "advice": advice,
            "quote": overview["quote"],
            "methodology": "仓位按个人池记录的 position_pct 计算；风险指标使用缓存日线与现金零收益假设。",
        }

    @staticmethod
    def _closed_trades(logs: list[PersonalInvestmentLog], start: date) -> tuple[list[dict], list[str]]:
        lots: dict[str, deque[dict]] = defaultdict(deque)
        trades: list[dict] = []
        gaps: list[str] = []
        for row in sorted(logs, key=lambda item: item.created_at or datetime.min):
            if not row.code or row.price is None or row.shares is None or row.shares <= 0:
                if row.action in {"buy", "sell"}:
                    gaps.append(f"日志#{row.id}缺少代码、价格或股数")
                continue
            if row.action == "buy":
                lots[row.code].append({"shares": int(row.shares), "price": float(row.price), "date": row.created_at.date()})
                continue
            if row.action != "sell":
                continue
            remaining = int(row.shares)
            while remaining > 0 and lots[row.code]:
                lot = lots[row.code][0]
                matched = min(remaining, lot["shares"])
                if row.created_at.date() >= start:
                    pnl = (float(row.price) - lot["price"]) * matched
                    trades.append({
                        "code": row.code,
                        "name": row.name or row.code,
                        "entry_date": lot["date"].isoformat(),
                        "exit_date": row.created_at.date().isoformat(),
                        "shares": matched,
                        "entry_price": lot["price"],
                        "exit_price": float(row.price),
                        "pnl": round(pnl, 2),
                        "return_pct": round((float(row.price) / lot["price"] - 1) * 100, 2) if lot["price"] > 0 else None,
                    })
                lot["shares"] -= matched
                remaining -= matched
                if lot["shares"] <= 0:
                    lots[row.code].popleft()
            if remaining > 0:
                gaps.append(f"日志#{row.id}卖出股数超过已记录买入股数")
        return trades, list(dict.fromkeys(gaps))

    async def attribution(self, period: str = "year") -> dict[str, Any]:
        today = shanghai_now().date()
        period_id, start, period_label = _period_start(period, today)
        overview = await personal_portfolio_service.overview()
        config = await self.account_config()
        total_assets = _number(config.get("total_assets"))
        async with async_session() as session:
            logs = (await session.execute(
                select(PersonalInvestmentLog).order_by(PersonalInvestmentLog.created_at.asc())
            )).scalars().all()
        closed_trades, gaps = self._closed_trades(logs, start)
        realized_pnl = sum(item["pnl"] for item in closed_trades)

        holdings = [
            item for item in overview["items"]
            if item.get("status") in {"holding", "reduce"}
            and (_number(item.get("position_pct")) or 0) > 0
        ]
        cutoff = start - timedelta(days=10)
        codes = [item["code"] for item in holdings]
        history_by_code: dict[str, list[StockDailyBar]] = defaultdict(list)
        if codes:
            async with async_session() as session:
                rows = (await session.execute(
                    select(StockDailyBar)
                    .where(StockDailyBar.stock_code.in_(codes), StockDailyBar.trade_date >= cutoff)
                    .order_by(StockDailyBar.stock_code, StockDailyBar.trade_date)
                )).scalars().all()
            for row in rows:
                if row.close_price is not None and row.close_price > 0:
                    history_by_code[row.stock_code].append(row)

        stock_rows = []
        industry_contribution: dict[str, float] = defaultdict(float)
        open_contribution = 0.0
        for item in holdings:
            history = history_by_code.get(item["code"], [])
            base_row = next((row for row in history if row.trade_date >= start), None)
            current_price = _number(item.get("price"))
            stock_return = None
            if base_row and current_price and base_row.close_price:
                stock_return = (current_price / float(base_row.close_price) - 1) * 100
            elif item.get("entry_date") and date.fromisoformat(str(item["entry_date"])[:10]) >= start:
                stock_return = _number(item.get("pnl_pct"))
            weight = float(item["position_pct"])
            contribution = weight * stock_return / 100 if stock_return is not None else None
            if contribution is not None:
                open_contribution += contribution
                industry_contribution[item.get("industry") or item.get("sector") or "未分类"] += contribution
            stock_rows.append({
                "code": item["code"],
                "name": item["display_name"],
                "industry": item.get("industry") or item.get("sector") or "未分类",
                "weight_pct": weight,
                "return_pct": _rounded(stock_return),
                "contribution_pct": _rounded(contribution),
                "source": "缓存日线+最新行情" if base_row else "建仓成本" if stock_return is not None else "数据不足",
            })

        realized_return = realized_pnl / total_assets * 100 if total_assets else None
        estimated_return = open_contribution + (realized_return or 0)
        try:
            benchmark_rows = await collector.fetch_shanghai_index_history(days=max(30, (today - start).days + 10))
        except Exception:
            benchmark_rows = []
        benchmark_window = [row for row in benchmark_rows if str(row.get("date") or "") >= start.isoformat()]
        benchmark_return = None
        if len(benchmark_window) >= 2 and _number(benchmark_window[0].get("close")):
            benchmark_return = (
                float(benchmark_window[-1]["close"]) / float(benchmark_window[0]["close"]) - 1
            ) * 100
        alpha = estimated_return - benchmark_return if benchmark_return is not None else None

        winners = [item for item in closed_trades if item["pnl"] > 0]
        losers = [item for item in closed_trades if item["pnl"] < 0]
        average_win = sum(item["return_pct"] for item in winners) / len(winners) if winners else None
        average_loss = sum(item["return_pct"] for item in losers) / len(losers) if losers else None
        payoff = abs(average_win / average_loss) if average_win is not None and average_loss not in (None, 0) else None
        monthly: dict[str, float] = defaultdict(float)
        for trade in closed_trades:
            monthly[trade["exit_date"][:7]] += trade["pnl"]

        month_start = date(today.year, today.month, 1)
        recent_buys = sum(
            row.action == "buy" and row.created_at and row.created_at.date() >= month_start
            for row in logs
        )
        warnings = []
        if recent_buys >= 3:
            warnings.append(f"本月已记录 {recent_buys} 笔买入，请检查是否存在频繁交易。")
        if gaps:
            warnings.append(f"有 {len(gaps)} 条交易日志字段不完整，已从已实现收益中排除。")

        risk_metrics = await self._portfolio_series(holdings, days=max(120, (today - start).days + 30))
        return {
            "period": {"id": period_id, "label": period_label, "start": start.isoformat(), "end": today.isoformat()},
            "summary": {
                "estimated_return_pct": round(estimated_return, 2),
                "benchmark_return_pct": _rounded(benchmark_return),
                "alpha_pct": _rounded(alpha),
                "realized_pnl": round(realized_pnl, 2),
                "realized_return_pct": _rounded(realized_return),
                "open_contribution_pct": round(open_contribution, 2),
                "win_count": len(winners),
                "loss_count": len(losers),
                "closed_trade_count": len(closed_trades),
                "win_rate_pct": round(len(winners) / len(closed_trades) * 100, 1) if closed_trades else None,
                "average_win_pct": _rounded(average_win),
                "average_loss_pct": _rounded(average_loss),
                "payoff_ratio": _rounded(payoff),
                **risk_metrics,
            },
            "by_stock": sorted(stock_rows, key=lambda item: item["contribution_pct"] if item["contribution_pct"] is not None else -999, reverse=True),
            "by_industry": [
                {"industry": key, "contribution_pct": round(value, 2)}
                for key, value in sorted(industry_contribution.items(), key=lambda item: item[1], reverse=True)
            ],
            "by_month": [
                {"month": key, "realized_pnl": round(value, 2)}
                for key, value in sorted(monthly.items())
            ],
            "closed_trades": closed_trades[-50:],
            "warnings": warnings,
            "data_quality": {
                "total_assets_configured": total_assets is not None,
                "complete_trade_logs": len(gaps) == 0,
                "excluded_log_count": len(gaps),
                "benchmark_points": len(benchmark_window),
                "method": "持仓贡献使用期初缓存收盘价与最新行情；已实现收益使用投资日志FIFO配对。",
                "limitation": "未记录手续费、分红和盘中资金变动时，结果是决策复盘估算而非券商对账单。",
            },
        }


personal_analytics_service = PersonalAnalyticsService()
