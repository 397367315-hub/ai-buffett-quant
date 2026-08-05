"""Strategy backtests built from cached OHLCV bars and explicit execution rules."""

from __future__ import annotations

import asyncio
import hashlib
import math
from collections import defaultdict
from datetime import date, timedelta
from typing import Any

from sqlalchemy import select

from config import settings
from database import async_session
from models import StockDailyBar
from quant.engine import get_strategy, match_stock
from quant.indicators import enrich_with_indicators, normalize_snapshot_stock, number
from quant.jobs import create_job, get_job, spawn, update_job
from quant.market_cache import load_quant_market_snapshot, save_quant_market_snapshot
from quant.rules import TECHNICAL_RULE_TYPES, evaluate_rules, static_group_can_match
from quant.storage import quant_store
from services.data_collector import collector, shanghai_now
from services.history_cache import history_cache
from services.research_protocol import ResearchProtocol
from services.stock_features import required_feature_fields, stock_feature_service


COMMISSION_RATE = ResearchProtocol.COMMISSION_RATE
STAMP_TAX_RATE = ResearchProtocol.STAMP_TAX_RATE
SLIPPAGE_RATE = 0.002  # PRD specifies 0.2% on each side for the visual strategy module.


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _std(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    average = _mean(values)
    return math.sqrt(sum((item - average) ** 2 for item in values) / (len(values) - 1))


def _max_drawdown(values: list[float]) -> float:
    peak = values[0] if values else 0.0
    maximum = 0.0
    for value in values:
        peak = max(peak, value)
        if peak > 0:
            maximum = max(maximum, (peak - value) / peak)
    return maximum


def _all_rules(strategy: dict) -> list[dict]:
    return [
        *((strategy.get("filter") or {}).get("rules") or []),
        *((strategy.get("entry") or {}).get("rules") or []),
        *((strategy.get("exit") or {}).get("rules") or []),
    ]


def _has_rule(strategy: dict, types: set[str]) -> bool:
    return any(rule.get("type") in types for rule in _all_rules(strategy))


def _snapshot_rule_types(strategy: dict) -> list[str]:
    # Those fields are not stored with a historical disclosure timestamp in
    # StockDailyBar. They remain visible but are flagged in the audit report.
    types = {
        "pe_ttm", "pb", "roe", "market_cap", "sector", "large_order_inflow_pct",
        "gross_margin", "revenue_growth", "deducted_profit_growth", "ocf_to_profit",
        "debt_ratio", "receivable_to_revenue", "is_profitable", "market_breadth",
        "sector_strength", "no_lockup_expiry", "holder_concentration", "holder_decline_streak",
    }
    return sorted({rule["type"] for rule in _all_rules(strategy) if rule.get("type") in types})


def _history_is_sufficient(bars: list[dict], start: date, end: date) -> bool:
    if len(bars) < 60:
        return False
    try:
        first = date.fromisoformat(str(bars[0]["date"])[:10])
        last = date.fromisoformat(str(bars[-1]["date"])[:10])
    except (KeyError, TypeError, ValueError):
        return False
    return first <= start + timedelta(days=30) and last >= end - timedelta(days=15)


class StrategyBacktestService:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()

    async def start_backtest(self, strategy_id: str, request: dict) -> dict:
        strategy = get_strategy(strategy_id)
        if strategy is None:
            raise KeyError("策略不存在")
        running = [
            item for item in quant_store.read("jobs").get("backtest", {}).values()
            if item.get("strategy_id") == strategy_id and item.get("status") in {"queued", "running"}
        ]
        if running:
            return max(running, key=lambda item: item.get("created_at") or "")
        job = create_job("backtest", "bt", {"strategy_id": strategy_id, "strategy_name": strategy["name"]})
        spawn(self._run_job(job["job_id"], strategy, request))
        return job

    async def _run_job(self, job_id: str, strategy: dict, request: dict) -> None:
        async with self._lock:
            update_job(
                "backtest", job_id, status="running", phase="loading_data", progress=3,
                message="正在读取一年历史日线", started_at=shanghai_now().isoformat(),
            )
            try:
                result = await self.run(strategy, request, job_id=job_id)
                result["job_id"] = job_id
                result["strategy_id"] = strategy["id"]
                result["strategy_name"] = strategy["name"]
                result["completed_at"] = shanghai_now().isoformat()
                quant_store.write_backtest_result(job_id, result)
                update_job(
                    "backtest", job_id, status="completed", phase="completed", progress=100,
                    message="回测完成", completed_at=result["completed_at"], result={
                        "total_return": result.get("total_return"), "win_rate": result.get("win_rate"),
                        "trade_count": result.get("trade_count"), "passed": result.get("passed"),
                    },
                )
            except Exception as exc:
                update_job(
                    "backtest", job_id, status="failed", phase="failed", progress=100,
                    message="回测失败", error=str(exc)[:300], completed_at=shanghai_now().isoformat(),
                )

    async def _snapshot(self, job_id: str | None) -> tuple[list[dict], list[str]]:
        cached = quant_store.read("market_snapshot")
        warnings: list[str] = []
        if cached.get("stocks"):
            return [normalize_snapshot_stock(item) for item in cached["stocks"]], warnings
        if job_id:
            update_job("backtest", job_id, phase="market_snapshot", progress=8, message="历史股票池缺少快照，正在获取当前市场目录")
        try:
            snapshot = await collector.fetch_quant_market_snapshot()
            await save_quant_market_snapshot(snapshot)
            quant_store.write("market_snapshot", {"version": 1, **snapshot})
            return [normalize_snapshot_stock(item) for item in snapshot.get("stocks") or []], warnings
        except Exception:
            persistent = await load_quant_market_snapshot()
            if persistent.get("stocks"):
                quant_store.write("market_snapshot", {"version": 1, **persistent})
                warnings.append(f"实时行情不可用，使用 {persistent.get('data_date')} 的最近交易日缓存股票池。")
                return [normalize_snapshot_stock(item) for item in persistent["stocks"]], warnings
            warnings.append("当前全市场快照不可用，仅能使用已缓存日线中的技术规则；估值、板块和资金快照规则可能无法评估。")
            try:
                async with async_session() as session:
                    rows = (await session.execute(
                        select(StockDailyBar.stock_code, StockDailyBar.stock_name).distinct()
                    )).all()
                fallback = [
                    normalize_snapshot_stock({"code": code, "name": name or "", "sectors": []})
                    for code, name in rows if code
                ]
                return fallback, warnings
            except Exception:
                return [], warnings

    @staticmethod
    def _candidate_pool(strategy: dict, snapshot: list[dict]) -> tuple[list[dict], bool]:
        eligible = [item for item in snapshot if static_group_can_match(strategy.get("filter") or {}, item)]
        eligible.sort(key=lambda item: hashlib.sha256(f"{strategy['id']}:{item['code']}".encode()).hexdigest())
        truncated = len(eligible) > settings.quant_backtest_max_stocks
        return eligible[:settings.quant_backtest_max_stocks], truncated

    async def _load_cached_bars(self, codes: list[str], start: date, end: date) -> dict[str, list[dict]]:
        cutoff = start - timedelta(days=150)
        grouped: dict[str, list[dict]] = defaultdict(list)
        if not codes:
            return grouped
        async with async_session() as session:
            rows = (await session.execute(
                select(StockDailyBar)
                .where(
                    StockDailyBar.stock_code.in_(codes),
                    StockDailyBar.trade_date >= cutoff,
                    StockDailyBar.trade_date <= end,
                )
                .order_by(StockDailyBar.stock_code, StockDailyBar.trade_date)
            )).scalars().all()
        for row in rows:
            grouped[row.stock_code].append({
                "date": row.trade_date.isoformat(), "open": row.open_price, "close": row.close_price,
                "high": row.high_price, "low": row.low_price, "volume": row.volume,
                "amount": row.amount, "turnover": row.turnover, "source": row.source,
            })
        return dict(grouped)

    async def _fill_missing_history(
        self, candidates: list[dict], bars: dict[str, list[dict]], start: date, end: date, job_id: str | None,
    ) -> tuple[dict[str, list[dict]], list[str]]:
        missing = [item for item in candidates if not _history_is_sufficient(bars.get(item["code"], []), start, end)]
        warnings: list[str] = []
        if not missing:
            return bars, warnings
        if job_id:
            update_job(
                "backtest", job_id, phase="filling_history", progress=20,
                message=f"正在补齐 {len(missing)} 只股票的历史日线", total_stocks=len(missing), processed_stocks=0,
            )
        semaphore = asyncio.Semaphore(6)
        requested_days = min(800, max(180, (end - start).days + 150))

        async def fetch_one(stock: dict):
            try:
                async with semaphore:
                    payload = await collector.fetch_stock_price_history(stock["code"], requested_days)
                return stock, payload
            except Exception:
                return stock, None

        fetched = []
        for offset in range(0, len(missing), 12):
            batch = missing[offset:offset + 12]
            results = await asyncio.gather(*(fetch_one(item) for item in batch))
            fetched.extend((stock, payload) for stock, payload in results if payload and payload.get("history"))
            if job_id:
                done = min(offset + len(batch), len(missing))
                update_job(
                    "backtest", job_id, progress=20 + round(done / len(missing) * 20),
                    message=f"正在补齐历史日线 {done}/{len(missing)}", processed_stocks=done,
                )
        if fetched:
            try:
                await history_cache.cache_stock_price_histories(fetched)
            except Exception:
                warnings.append("按需获取的日线未能写入缓存，本次回测仍使用内存数据。")
            for stock, payload in fetched:
                converted = []
                for bar in payload.get("history") or []:
                    try:
                        bar_date = date.fromisoformat(str(bar.get("trade_date"))[:10])
                    except (TypeError, ValueError):
                        continue
                    if start - timedelta(days=150) <= bar_date <= end:
                        converted.append({
                            "date": bar_date.isoformat(), "open": bar.get("open"), "close": bar.get("close"),
                            "high": bar.get("high"), "low": bar.get("low"), "volume": bar.get("volume"),
                            "amount": bar.get("amount"), "turnover": bar.get("turnover"),
                            "source": payload.get("source", "tencent"),
                        })
                if converted:
                    bars[stock["code"]] = converted
        unresolved = [item["code"] for item in missing if not _history_is_sufficient(bars.get(item["code"], []), start, end)]
        if unresolved:
            warnings.append(f"{len(unresolved)} 只候选股票日线不足 60 条或未覆盖所选首尾日期，已从回测样本排除。")
        return bars, warnings

    async def _load_historical_flows(
        self, codes: list[str], start: date, end: date, job_id: str | None,
    ) -> tuple[dict[str, dict[str, float]], list[str]]:
        if not codes:
            return {}, []
        if job_id:
            update_job("backtest", job_id, phase="loading_flow", progress=43, message="正在读取历史主力资金流")
        semaphore = asyncio.Semaphore(8)

        async def fetch_one(code: str):
            try:
                async with semaphore:
                    return code, await collector.fetch_stock_fund_flow(code)
            except Exception:
                return code, []

        output: dict[str, dict[str, float]] = {}
        results = await asyncio.gather(*(fetch_one(code) for code in codes))
        for code, rows in results:
            values = {}
            for row in rows:
                raw_date = str(row.get("date") or "")[:10]
                try:
                    row_date = date.fromisoformat(raw_date)
                except ValueError:
                    continue
                if start <= row_date <= end and number(row.get("main_net_inflow")) is not None:
                    values[raw_date] = float(row["main_net_inflow"]) / 1e4
            if values:
                output[code] = values
        warnings = []
        if len(output) < len(codes):
            warnings.append("部分历史主力资金流不可用，相关日期会使用当前快照字段并标记为前视风险。")
        return output, warnings

    @staticmethod
    def _daily_context(base: dict, bars: list[dict], index: int, flows: dict[str, float]) -> dict:
        current = bars[index]
        context = dict(base)
        context.update({
            "price": number(current.get("close")),
            "vol_ratio": None,
            "turnover": number(current.get("turnover")) if number(current.get("turnover")) is not None else base.get("turnover"),
        })
        previous = number(bars[index - 1].get("close")) if index else None
        if previous and context["price"]:
            context["change_pct"] = (context["price"] / previous - 1) * 100
        if current.get("date") in flows:
            context["main_inflow"] = flows[current["date"]]
        return enrich_with_indicators(context, bars[:index + 1])

    @staticmethod
    def _position_budget(cash: float, equity: float, position: dict) -> float:
        method = position.get("method", "equal_weight")
        max_pct = float(position.get("max_position_pct", 20)) / 100
        max_holdings = max(1, int(position.get("max_holdings", 5)))
        if method == "fixed_amount" and position.get("fixed_amount"):
            target = float(position["fixed_amount"])
        elif method == "kelly":
            # No out-of-sample probability estimate is available at order time;
            # conservative half-Kelly is bounded by configured max exposure.
            target = equity * min(max_pct, 0.5 / max_holdings)
        else:
            target = equity * min(max_pct, 1 / max_holdings)
        return max(0.0, min(cash, target))

    def _simulate(
        self,
        strategy: dict,
        candidates: list[dict],
        grouped: dict[str, list[dict]],
        flow_history: dict[str, dict[str, float]],
        start: date,
        end: date,
        initial_capital: float,
        job_id: str | None,
    ) -> dict:
        by_code = {item["code"]: item for item in candidates if item["code"] in grouped}
        index_by_code = {
            code: {bar["date"]: index for index, bar in enumerate(bars)}
            for code, bars in grouped.items() if code in by_code
        }
        trading_dates = sorted({
            bar["date"] for code, bars in grouped.items() if code in by_code
            for bar in bars
            if start.isoformat() <= bar["date"] <= end.isoformat()
        })
        if len(trading_dates) < 60:
            raise ValueError(f"回测区间交易日不足 60 天（当前 {len(trading_dates)} 天）")

        cash = float(initial_capital)
        holdings: dict[str, dict] = {}
        pending: list[dict] = []
        trades: list[dict] = []
        cancelled_orders = 0
        values: list[dict] = []
        last_close: dict[str, float] = {}
        max_holdings = int((strategy.get("position") or {}).get("max_holdings", 5))

        def equity_value() -> float:
            return cash + sum(holding["shares"] * last_close.get(code, holding["entry_price"]) for code, holding in holdings.items())

        for day_index, day in enumerate(trading_dates):
            # Every decision was made at the preceding close. Orders can only
            # execute at the next common trading day's verified opening price.
            due, pending = [item for item in pending if item["due_date"] == day], [item for item in pending if item["due_date"] != day]
            for order in due:
                code = order["code"]
                bar_index = index_by_code.get(code, {}).get(day)
                if bar_index is None or number(grouped[code][bar_index].get("open")) is None:
                    cancelled_orders += 1
                    continue
                opening = float(grouped[code][bar_index]["open"])
                if order["action"] == "buy":
                    if code in holdings or len(holdings) >= max_holdings:
                        cancelled_orders += 1
                        continue
                    execution_price = opening * (1 + SLIPPAGE_RATE)
                    budget = self._position_budget(cash, equity_value(), strategy.get("position") or {})
                    shares = int(budget / execution_price / 100) * 100
                    amount = execution_price * shares
                    commission = amount * COMMISSION_RATE
                    if shares <= 0 or amount + commission > cash:
                        cancelled_orders += 1
                        continue
                    cash -= amount + commission
                    holdings[code] = {
                        "shares": shares, "entry_price": execution_price, "entry_cash": amount + commission,
                        "buy_date": day, "buy_day_index": day_index, "stock_name": order["stock_name"],
                        "strategy_id": strategy["id"],
                    }
                    trades.append({
                        "date": day, "signal_date": order["signal_date"], "action": "buy", "stock_code": code,
                        "stock_name": order["stock_name"], "price": round(execution_price, 4), "shares": shares,
                        "amount": round(amount, 2), "commission": round(commission, 2), "tax": 0.0,
                        "reason": order["reason"], "execution": "T+1 开盘", "profit_pct": None,
                    })
                else:
                    holding = holdings.pop(code, None)
                    if holding is None:
                        cancelled_orders += 1
                        continue
                    execution_price = opening * (1 - SLIPPAGE_RATE)
                    amount = execution_price * holding["shares"]
                    commission = amount * COMMISSION_RATE
                    tax = amount * STAMP_TAX_RATE
                    net = amount - commission - tax
                    cash += net
                    profit_pct = (net / holding["entry_cash"] - 1) * 100
                    trades.append({
                        "date": day, "signal_date": order["signal_date"], "action": "sell", "stock_code": code,
                        "stock_name": holding["stock_name"], "price": round(execution_price, 4), "shares": holding["shares"],
                        "amount": round(amount, 2), "commission": round(commission, 2), "tax": round(tax, 2),
                        "reason": order["reason"], "execution": "T+1 开盘", "profit_pct": round(profit_pct, 3),
                    })

            daily_contexts: dict[str, dict] = {}
            for code, base in by_code.items():
                bar_index = index_by_code.get(code, {}).get(day)
                if bar_index is None:
                    continue
                context = self._daily_context(base, grouped[code], bar_index, flow_history.get(code, {}))
                daily_contexts[code] = context
                if number(context.get("price")) is not None:
                    last_close[code] = float(context["price"])

            if day_index + 1 < len(trading_dates):
                next_day = trading_dates[day_index + 1]
                pending_codes = {item["code"] for item in pending}
                exit_rules = (strategy.get("exit") or {}).get("rules") or []
                stop_loss = float((strategy.get("exit") or {}).get("stop_loss_pct", 5))
                take_profit = float((strategy.get("exit") or {}).get("take_profit_pct", 15))
                max_days = int((strategy.get("exit") or {}).get("max_holding_days", 20))
                for code, holding in list(holdings.items()):
                    context = daily_contexts.get(code)
                    if not context or code in pending_codes:
                        continue
                    close = number(context.get("price"))
                    if close is None:
                        continue
                    raw_profit = (close / holding["entry_price"] - 1) * 100
                    custom_exit = evaluate_rules(exit_rules, context, "OR")[0] if exit_rules else False
                    held_days = day_index - holding["buy_day_index"]
                    reason = ""
                    if raw_profit <= -stop_loss:
                        reason = f"止损（{raw_profit:.2f}%）"
                    elif raw_profit >= take_profit:
                        reason = f"止盈（{raw_profit:.2f}%）"
                    elif custom_exit:
                        reason = "自定义离场规则"
                    elif held_days >= max_days:
                        reason = f"最长持有期（{max_days}个交易日）"
                    if reason:
                        pending.append({
                            "action": "sell", "code": code, "stock_name": holding["stock_name"],
                            "signal_date": day, "due_date": next_day, "reason": reason,
                        })
                        pending_codes.add(code)

                available_slots = max(0, max_holdings - len(holdings) - sum(item["action"] == "buy" for item in pending))
                entries = []
                if available_slots:
                    for code, context in daily_contexts.items():
                        if code in holdings or code in pending_codes:
                            continue
                        signal = match_stock(strategy, context)
                        if signal:
                            entries.append(signal)
                    entries.sort(
                        key=lambda item: (item.get("match_score", 0), item.get("main_inflow") or 0, item.get("stock_code")),
                        reverse=True,
                    )
                    for signal in entries[:available_slots]:
                        pending.append({
                            "action": "buy", "code": signal["stock_code"], "stock_name": signal["stock_name"],
                            "signal_date": day, "due_date": next_day,
                            "reason": "；".join(signal.get("matched_rules") or [])[:200],
                        })
                        pending_codes.add(signal["stock_code"])

            value = equity_value()
            values.append({"date": day, "value": round(value, 2), "cash": round(cash, 2), "holding_count": len(holdings)})
            if job_id and day_index % 10 == 0:
                update_job(
                    "backtest", job_id, phase="simulating", progress=55 + round(day_index / len(trading_dates) * 38),
                    message=f"正在按 T+1 规则模拟 {day_index + 1}/{len(trading_dates)} 个交易日",
                    processed_days=day_index + 1, total_days=len(trading_dates),
                )

        completed = [item for item in trades if item["action"] == "sell"]
        profits = [float(item["profit_pct"]) for item in completed if item.get("profit_pct") is not None]
        returns = [values[index]["value"] / values[index - 1]["value"] - 1 for index in range(1, len(values)) if values[index - 1]["value"]]
        final_value = values[-1]["value"] if values else initial_capital
        total_return = (final_value / initial_capital - 1) * 100
        calendar_days = max(1, (end - start).days)
        annual_return = ((final_value / initial_capital) ** (365 / calendar_days) - 1) * 100 if final_value > 0 else -100
        wins = [value for value in profits if value > 0]
        losses = [value for value in profits if value <= 0]
        average_loss = abs(_mean(losses))
        ratio = _mean(wins) / average_loss if average_loss else (0.0 if not wins else _mean(wins))
        warnings = []
        if cancelled_orders:
            warnings.append(f"{cancelled_orders} 笔订单因停牌、无开盘价或仓位限制取消，没有被替换为更晚日期成交。")
        return {
            "total_return": round(total_return, 2), "annual_return": round(annual_return, 2),
            "win_rate": round(len(wins) / len(profits) * 100, 2) if profits else 0.0,
            "profit_loss_ratio": round(ratio, 2), "max_drawdown": round(_max_drawdown([item["value"] for item in values]) * 100, 2),
            "sharpe_ratio": round(_mean(returns) / _std(returns) * math.sqrt(252), 2) if _std(returns) else 0.0,
            "trade_count": len(trades), "completed_trade_count": len(completed),
            "passed": bool(total_return > 0 and profits and len(wins) / len(profits) >= 0.4),
            "trades": trades[-500:], "daily_values": values[-520:], "open_positions": [
                {"stock_code": code, "stock_name": value["stock_name"], "shares": value["shares"], "entry_price": value["entry_price"], "buy_date": value["buy_date"]}
                for code, value in holdings.items()
            ], "warnings": warnings,
            "params": {"initial_capital": initial_capital, "final_value": round(final_value, 2), "trading_days": len(trading_dates)},
        }

    async def run(self, strategy: dict, request: dict, job_id: str | None = None) -> dict:
        start = date.fromisoformat(str(request["start_date"])[:10])
        end = date.fromisoformat(str(request["end_date"])[:10])
        initial_capital = float(request["initial_capital"])
        if end > shanghai_now().date():
            raise ValueError("回测结束日期不能晚于当前日期")
        snapshot, warnings = await self._snapshot(job_id)
        feature_fields = required_feature_fields([strategy])
        if feature_fields:
            if job_id:
                update_job(
                    "backtest", job_id, phase="feature_data", progress=11,
                    message="正在读取当前已披露财务与事件特征",
                )
            try:
                feature_result = await stock_feature_service.enrich(
                    snapshot,
                    feature_fields,
                    full_market=len(snapshot) >= 4000,
                )
                snapshot = feature_result["stocks"]
                warnings.extend(feature_result.get("warnings") or [])
            except Exception as exc:
                warnings.append(f"高级特征不可用，相关候选会按数据不足排除（{type(exc).__name__}）。")
        candidates, truncated = self._candidate_pool(strategy, snapshot)
        if not candidates:
            raise ValueError("当前股票池没有满足策略静态筛选条件的股票；请检查规则或等待行情快照恢复。")
        if truncated:
            warnings.append(f"静态候选池超过 {settings.quant_backtest_max_stocks} 只，使用按股票代码哈希确定的固定样本回测。")
        if job_id:
            update_job("backtest", job_id, phase="loading_data", progress=15, message=f"正在读取 {len(candidates)} 只候选股票的历史日线")
        grouped = await self._load_cached_bars([item["code"] for item in candidates], start, end)
        grouped, fill_warnings = await self._fill_missing_history(candidates, grouped, start, end, job_id)
        warnings.extend(fill_warnings)
        grouped = {code: bars for code, bars in grouped.items() if _history_is_sufficient(bars, start, end)}
        if not grouped:
            raise ValueError("历史日线不足，至少需要 60 个交易日；请先执行一年历史数据回补。")

        flow_history: dict[str, dict[str, float]] = {}
        if _has_rule(strategy, {"main_inflow"}):
            flow_history, flow_warnings = await self._load_historical_flows(list(grouped), start, end, job_id)
            warnings.extend(flow_warnings)
        snapshot_types = _snapshot_rule_types(strategy)
        if snapshot_types:
            warnings.append(
                "历史日线不含带披露日期的 " + "、".join(snapshot_types) +
                "，这些规则以当前快照固定筛选，存在前视和幸存者偏差。"
            )
        if _has_rule(strategy, {"turnover"}):
            warnings.append("腾讯补源日线可能缺少历史换手率；缺失日期会使用当前快照换手率，相关结果仅供研究。")
        if _has_rule(strategy, {"main_inflow"}) and len(flow_history) < len(grouped):
            warnings.append("主力资金历史覆盖不完整，未覆盖股票的资金规则会使用当前快照，不能视为严格点时回测。")
        if job_id:
            update_job("backtest", job_id, phase="simulating", progress=55, message="正在按 T+1 开盘成交和成本模型回测")
        result = self._simulate(strategy, candidates, grouped, flow_history, start, end, initial_capital, job_id)
        audit_eligible = not snapshot_types and not _has_rule(strategy, {"turnover"}) and (
            not _has_rule(strategy, {"main_inflow"}) or len(flow_history) == len(grouped)
        )
        return {
            **result,
            "available": True, "period": {"from": start.isoformat(), "to": end.isoformat()},
            "candidate_count": len(candidates), "stock_count": len(grouped),
            "execution_rule": "T日收盘计算规则，T+1开盘成交；买入和卖出均含0.2%滑点、0.025%佣金，卖出含0.1%印花税。",
            "cost_model": {"commission_rate": COMMISSION_RATE, "stamp_tax_rate_on_sell": STAMP_TAX_RATE, "slippage_rate_each_side": SLIPPAGE_RATE},
            "data_quality": {
                "grade": "严格" if audit_eligible else "研究级", "audit_eligible": audit_eligible,
                "cached_bar_stocks": len(grouped), "snapshot_rule_types": snapshot_types, "warnings": warnings + result["warnings"],
            },
        }

    @staticmethod
    def get_status(job_id: str) -> dict | None:
        return get_job("backtest", job_id)

    @staticmethod
    def get_results(strategy_id: str) -> list[dict]:
        results = [item for item in quant_store.list_backtest_results() if item.get("strategy_id") == strategy_id]
        return sorted(results, key=lambda item: item.get("completed_at") or "", reverse=True)

    @classmethod
    def compare(cls, strategy_ids: list[str]) -> list[dict]:
        output = []
        for strategy_id in strategy_ids:
            result = next(iter(cls.get_results(strategy_id)), None)
            strategy = get_strategy(strategy_id)
            output.append({
                "strategy_id": strategy_id, "strategy_name": (strategy or {}).get("name", strategy_id),
                "available": bool(result), "result": result,
            })
        return output


quant_backtest_service = StrategyBacktestService()
