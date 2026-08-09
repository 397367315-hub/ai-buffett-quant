"""Point-in-time research backtest for the cached A-share daily bars.

The older backtest used board-flow snapshots as a proxy for stock returns. That
was useful as a demo, but it could not answer when a stock was bought or what
the actual execution cost was. This module keeps the experiment deliberately
small and inspectable:

* the factor is fixed absolute momentum over the lookback window;
* the signal is calculated after T close;
* entry is T+1 open and exit is T+N close;
* commission, sell stamp tax, slippage and a size-based impact estimate are
  deducted from every selected position;
* all currently cached stocks form the equal-weight comparison universe.

The cache does not contain historical membership or delisted symbols, so the
result is a research diagnostic and is never presented as an unbiased
historical simulation.
"""

from __future__ import annotations

import math
from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import select

from database import async_session
from models import StockDailyBar
from services.data_collector import shanghai_now
from services.research_protocol import ResearchProtocol


def _number(value: Any) -> float | None:
    if value in (None, "", "-"):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _date_value(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None
    return None


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _std(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    average = _mean(values)
    return math.sqrt(sum((value - average) ** 2 for value in values) / (len(values) - 1))


def _compound(returns: list[float]) -> float:
    value = 1.0
    for item in returns:
        value *= 1 + item
    return value - 1


def _max_drawdown(returns: list[float]) -> float:
    equity = 1.0
    peak = equity
    maximum = 0.0
    for item in returns:
        equity *= 1 + item
        peak = max(peak, equity)
        if peak:
            maximum = max(maximum, (peak - equity) / peak)
    return maximum


def _pearson(left: list[float], right: list[float]) -> float:
    if len(left) != len(right) or len(left) < 2:
        return 0.0
    left_mean = _mean(left)
    right_mean = _mean(right)
    numerator = sum((a - left_mean) * (b - right_mean) for a, b in zip(left, right))
    left_sum = sum((a - left_mean) ** 2 for a in left)
    right_sum = sum((b - right_mean) ** 2 for b in right)
    denominator = math.sqrt(left_sum * right_sum)
    return numerator / denominator if denominator else 0.0


def _rank(values: list[float]) -> list[float]:
    """Return average ranks so IC is not sensitive to factor units."""
    ordered = sorted(enumerate(values), key=lambda item: item[1])
    ranks = [0.0] * len(values)
    index = 0
    while index < len(ordered):
        end = index + 1
        while end < len(ordered) and ordered[end][1] == ordered[index][1]:
            end += 1
        average_rank = (index + end - 1) / 2 + 1
        for position in range(index, end):
            ranks[ordered[position][0]] = average_rank
        index = end
    return ranks


class QuantResearchEngine:
    """Run a fixed, source-backed stock daily-bar experiment."""

    DEFAULT_LOOKBACK = 20
    DEFAULT_HOLDING = 5
    DEFAULT_TOP_N = 10
    DEFAULT_DAYS = 365
    SENSITIVITY_LOOKBACKS = (15, 20, 25)

    @classmethod
    async def _load_bars(cls, days: int, lookback_days: int, holding_days: int) -> list[dict]:
        today = shanghai_now().date()
        # Extra calendar days provide enough observations for the lookback
        # around weekends, holidays, and a few suspended sessions.
        calendar_window = days + lookback_days * 2 + holding_days * 2 + 30
        cutoff = today - timedelta(days=calendar_window)
        async with async_session() as session:
            result = await session.execute(
                select(StockDailyBar)
                .where(
                    StockDailyBar.trade_date >= cutoff,
                    StockDailyBar.trade_date <= today,
                )
                .order_by(StockDailyBar.trade_date, StockDailyBar.stock_code)
            )
            rows = result.scalars().all()
        return [
            {
                "code": row.stock_code,
                "date": row.trade_date,
                "open": row.open_price,
                "close": row.close_price,
                "amount": row.amount,
                "source": row.source,
            }
            for row in rows
        ]

    @staticmethod
    def _normalise_bars(rows: list[dict], end_date: date) -> dict[str, list[dict]]:
        grouped: dict[str, list[dict]] = defaultdict(list)
        for raw in rows:
            code = str(raw.get("code") or raw.get("stock_code") or "").strip()
            trade_date = _date_value(raw.get("date") or raw.get("trade_date"))
            open_price = _number(raw.get("open") if "open" in raw else raw.get("open_price"))
            close_price = _number(raw.get("close") if "close" in raw else raw.get("close_price"))
            if not code or not trade_date or trade_date > end_date or not open_price or not close_price:
                continue
            if open_price <= 0 or close_price <= 0:
                continue
            grouped[code].append({
                "date": trade_date,
                "open": open_price,
                "close": close_price,
                "amount": _number(raw.get("amount")),
                "source": raw.get("source") or "stock_daily_bars",
            })
        for code in grouped:
            grouped[code].sort(key=lambda item: item["date"])
        return dict(grouped)

    @classmethod
    def _cost_rate(cls, capital_per_position: float, average_amount: float | None) -> tuple[float, float]:
        if average_amount and average_amount > 0:
            impact = min(
                0.02,
                max(0.0002, 0.0002 + capital_per_position / average_amount * 0.25),
            )
        else:
            impact = 0.015
        friction = (
            ResearchProtocol.COMMISSION_RATE * 2
            + ResearchProtocol.STAMP_TAX_RATE
            + ResearchProtocol.SLIPPAGE_RATE * 2
            + impact
        )
        return friction, impact

    @classmethod
    def _simulate(
        cls,
        grouped: dict[str, list[dict]],
        *,
        evaluation_start: date,
        evaluation_end: date,
        lookback_days: int,
        holding_days: int,
        top_n: int,
        capital: float,
    ) -> dict:
        if not grouped:
            return {
                "daily_results": [],
                "_daily_results_internal": [],
                "candidate_count": 0,
                "stock_count": 0,
                "ic_values": [],
            }

        date_set = sorted({bar["date"] for bars in grouped.values() for bar in bars})
        date_index = {trade_date: index for index, trade_date in enumerate(date_set)}
        positions_by_code = {
            code: {bar["date"]: index for index, bar in enumerate(bars)}
            for code, bars in grouped.items()
        }
        portfolio_rows: list[dict] = []
        ic_values: list[float] = []
        signal_dates = [
            trade_date
            for trade_date in date_set
            if evaluation_start <= trade_date <= evaluation_end
        ]
        next_rebalance_index = 0

        for signal_date in signal_dates:
            global_index = date_index[signal_date]
            if global_index < next_rebalance_index:
                continue
            if global_index + holding_days >= len(date_set):
                break
            entry_date = date_set[global_index + 1]
            exit_date = date_set[global_index + holding_days]
            eligible: list[dict] = []

            for code, bars in grouped.items():
                position = positions_by_code[code].get(signal_date)
                if position is None or position < lookback_days:
                    continue
                if position + holding_days >= len(bars):
                    continue
                entry = bars[position + 1]
                exit_bar = bars[position + holding_days]
                # A suspension must not silently turn T+1/T+N into an unknown
                # later trade. Drop the sample and expose the missing-data risk.
                if entry["date"] != entry_date or exit_bar["date"] != exit_date:
                    continue
                trailing = bars[position - lookback_days:position + 1]
                start_close = trailing[0]["close"]
                momentum = (bars[position]["close"] / start_close - 1) if start_close else None
                if momentum is None:
                    continue
                gross_return = exit_bar["close"] / entry["open"] - 1
                amounts = [bar["amount"] for bar in trailing if bar["amount"] and bar["amount"] > 0]
                eligible.append({
                    "code": code,
                    "factor": momentum,
                    "gross_return": gross_return,
                    "average_amount": _mean(amounts) if amounts else None,
                })

            if not eligible:
                continue

            future_returns = [item["gross_return"] for item in eligible]
            factor_values = [item["factor"] for item in eligible]
            ic_values.append(_pearson(_rank(factor_values), _rank(future_returns)))
            eligible.sort(key=lambda item: (item["factor"], item["average_amount"] or 0), reverse=True)
            selected = eligible[:top_n]
            capital_per_position = capital / max(1, len(selected))
            trade_returns: list[float] = []
            gross_selected: list[float] = []
            cost_rates: list[float] = []
            impact_rates: list[float] = []
            for item in selected:
                friction, impact = cls._cost_rate(capital_per_position, item["average_amount"])
                item["friction_rate"] = friction
                item["impact_rate"] = impact
                item["net_return"] = item["gross_return"] - friction
                trade_returns.append(item["net_return"])
                gross_selected.append(item["gross_return"])
                cost_rates.append(friction)
                impact_rates.append(impact)

            benchmark_gross = _mean(future_returns)
            benchmark_cost, _ = cls._cost_rate(capital / max(1, len(eligible)), _mean([
                item["average_amount"] for item in eligible if item["average_amount"]
            ]) or None)
            benchmark_net = benchmark_gross - benchmark_cost
            portfolio_return = _mean(trade_returns)
            gross_return = _mean(gross_selected)
            commission_cost = ResearchProtocol.COMMISSION_RATE * 2
            stamp_tax_cost = ResearchProtocol.STAMP_TAX_RATE
            slippage_cost = ResearchProtocol.SLIPPAGE_RATE * 2
            previous = portfolio_rows[-1]["cumulative_return_pct"] if portfolio_rows else 0.0
            cumulative = ((1 + previous / 100) * (1 + portfolio_return) - 1) * 100
            benchmark_previous = portfolio_rows[-1]["cumulative_benchmark_pct"] if portfolio_rows else 0.0
            cumulative_benchmark = ((1 + benchmark_previous / 100) * (1 + benchmark_net) - 1) * 100
            portfolio_rows.append({
                "date": signal_date.isoformat(),
                "entry_date": entry_date.isoformat(),
                "exit_date": exit_date.isoformat(),
                "selected_count": len(selected),
                "valid_count": len(eligible),
                "avg_gross_return_pct": round(gross_return * 100, 3),
                "avg_net_return_pct": round(portfolio_return * 100, 3),
                "benchmark_return_pct": round(benchmark_gross * 100, 3),
                "benchmark_net_return_pct": round(benchmark_net * 100, 3),
                "average_cost_pct": round(_mean(cost_rates) * 100, 3),
                "commission_cost_pct": round(commission_cost * 100, 3),
                "stamp_tax_cost_pct": round(stamp_tax_cost * 100, 3),
                "slippage_cost_pct": round(slippage_cost * 100, 3),
                "impact_cost_pct": round(_mean(impact_rates) * 100, 3),
                "cumulative_return_pct": round(cumulative, 3),
                "cumulative_benchmark_pct": round(cumulative_benchmark, 3),
                "selected_codes": [item["code"] for item in selected],
            })
            next_rebalance_index = global_index + holding_days

        return {
            "daily_results": portfolio_rows,
            # Kept private by the workspace service so the API does not grow
            # with every historical period while partition metrics remain
            # reproducible inside the same locked run.
            "_daily_results_internal": list(portfolio_rows),
            "candidate_count": sum(row["valid_count"] for row in portfolio_rows),
            "stock_count": len(grouped),
            "ic_values": ic_values,
        }

    @classmethod
    def _metrics(cls, simulation: dict, holding_days: int) -> dict:
        rows = simulation["daily_results"]
        returns = [row["avg_net_return_pct"] / 100 for row in rows]
        benchmark = [row["benchmark_net_return_pct"] / 100 for row in rows]
        mean_return = _mean(returns)
        daily_scale = math.sqrt(252 / max(1, holding_days))
        standard_deviation = _std(returns)
        sharpe = mean_return / standard_deviation * daily_scale if standard_deviation else 0.0
        beta = _pearson(returns, benchmark) * (_std(returns) / _std(benchmark)) if _std(benchmark) else 0.0
        alpha = (mean_return - beta * _mean(benchmark)) * 252 / max(1, holding_days)
        ic_values = simulation["ic_values"]
        return {
            "trading_periods": len(rows),
            "total_return": round(_compound(returns) * 100, 2),
            "benchmark_return": round(_compound(benchmark) * 100, 2),
            "average_holding_return": round(mean_return * 100, 3),
            # Keep the legacy field for the existing risk panel, but the new
            # name above makes clear that periods are five trading days.
            "avg_daily_return": round(mean_return * 100, 3),
            "win_rate": round(sum(item > 0 for item in returns) / len(returns) * 100, 1) if returns else 0.0,
            "max_drawdown": round(_max_drawdown(returns) * 100, 2),
            "benchmark_max_drawdown": round(_max_drawdown(benchmark) * 100, 2),
            "sharpe_ratio": round(sharpe, 2),
            "alpha_annualized": round(alpha * 100, 2),
            "beta": round(beta, 3),
            "information_coefficient": round(_mean(ic_values), 4) if ic_values else 0.0,
            "ic_positive_rate": round(sum(item > 0 for item in ic_values) / len(ic_values) * 100, 1) if ic_values else 0.0,
        }

    @classmethod
    async def run(
        cls,
        days: int = DEFAULT_DAYS,
        top_n: int = DEFAULT_TOP_N,
        lookback_days: int = DEFAULT_LOOKBACK,
        holding_days: int = DEFAULT_HOLDING,
        capital: float = ResearchProtocol.REFERENCE_CAPITAL,
    ) -> dict:
        days = max(30, min(int(days), 730))
        top_n = max(1, min(int(top_n), 50))
        lookback_days = max(10, min(int(lookback_days), 120))
        holding_days = max(1, min(int(holding_days), 20))
        capital = max(10_000.0, min(float(capital), 100_000_000.0))
        today = shanghai_now().date()
        evaluation_start = today - timedelta(days=days)

        try:
            rows = await cls._load_bars(days, lookback_days, holding_days)
        except Exception as exc:
            return {
                "available": False,
                "error": f"股票日线缓存读取失败：{type(exc).__name__}",
                "data_quality": {"grade": "不足", "warnings": ["数据库不可用，未生成回测结果"]},
            }

        grouped = cls._normalise_bars(rows, today)
        simulation = cls._simulate(
            grouped,
            evaluation_start=evaluation_start,
            evaluation_end=today,
            lookback_days=lookback_days,
            holding_days=holding_days,
            top_n=top_n,
            capital=capital,
        )
        metrics = cls._metrics(simulation, holding_days)
        if not simulation["daily_results"]:
            return {
                "available": False,
                "error": "股票日线缓存不足以完成T+1开盘、T+N收盘的点时回测",
                "period": {"from": evaluation_start.isoformat(), "to": today.isoformat()},
                "data_quality": {
                    "grade": "不足",
                    "bar_count": len(rows),
                    "stock_count": len(grouped),
                    "warnings": ["至少需要完整的回看窗口、T+1开盘和持有期收盘数据"],
                },
            }

        sensitivity = []
        for candidate_lookback in cls.SENSITIVITY_LOOKBACKS:
            candidate = cls._simulate(
                grouped,
                evaluation_start=evaluation_start,
                evaluation_end=today,
                lookback_days=candidate_lookback,
                holding_days=holding_days,
                top_n=top_n,
                capital=capital,
            )
            candidate_metrics = cls._metrics(candidate, holding_days)
            sensitivity.append({
                "lookback_days": candidate_lookback,
                "trading_periods": candidate_metrics["trading_periods"],
                "total_return": candidate_metrics["total_return"],
                "information_coefficient": candidate_metrics["information_coefficient"],
            })

        source_names = sorted({str(row.get("source") or "stock_daily_bars") for row in rows})
        quality_grade = "充分" if len(simulation["daily_results"]) >= 30 else "一般" if len(simulation["daily_results"]) >= 12 else "不足"
        warnings = [
            "股票池来自当前可见日线缓存，未包含完整历史退市、停牌和并购股票，存在幸存者偏差。",
            "未使用无披露日期的基本面字段，不能把本结果解释为基本面Alpha。",
            "冲击成本使用成交额比例估计，不等同于逐笔成交回放。",
        ]
        audit_verdict = "证据不足" if quality_grade != "充分" else "需结合样本外和退市股票池继续审计"
        return {
            "available": True,
            "source": "stock_daily_bars",
            "source_names": source_names,
            "period": {
                "from": simulation["daily_results"][0]["date"],
                "to": simulation["daily_results"][-1]["exit_date"],
            },
            "trading_days": len(simulation["daily_results"]),
            "lookback_days": lookback_days,
            "holding_days": holding_days,
            "top_n": top_n,
            "capital": round(capital, 2),
            "factor": {
                "name": "绝对动量",
                "definition": f"T日收盘价 / {lookback_days}个交易日前收盘价 - 1",
                "execution_rule": f"T日收盘计算，T+1开盘等权买入，T+{holding_days}收盘退出",
            },
            "cost_model": {
                "commission_rate_each_side": ResearchProtocol.COMMISSION_RATE,
                "stamp_tax_rate_on_sell": ResearchProtocol.STAMP_TAX_RATE,
                "slippage_rate_each_side": ResearchProtocol.SLIPPAGE_RATE,
                "impact_cost": "0.02% + 单笔计划资金 / 回看成交额 × 25%，上限2%；成交额缺失按1.5%",
                "t_plus_one": True,
            },
            **metrics,
            "daily_details": simulation["daily_results"][-20:],
            "parameter_sensitivity": sensitivity,
            "data_quality": {
                "grade": quality_grade,
                "bar_count": len(rows),
                "stock_count": simulation["stock_count"],
                "candidate_observations": simulation["candidate_count"],
                "warnings": warnings,
            },
            "strategy_audit": {
                "overall_risk": "高" if quality_grade != "充分" else "中",
                "verdict": audit_verdict,
                "blockers": [warnings[0]] if quality_grade != "充分" else [],
                "warnings": warnings,
                "time_rule": "未使用同日收盘成交；每个持有窗口不重叠。",
                "survivorship_bias": "高",
                "credibility_score": 42.0 if quality_grade != "充分" else 62.0,
            },
        }


quant_research_engine = QuantResearchEngine()
