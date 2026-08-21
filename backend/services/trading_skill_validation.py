"""PIT, walk-forward validation for registered trading skills.

This module deliberately favors an honest research report over a convenient
headline metric. Incomplete historical universe/auction data is recorded as a
gate failure and cannot promote a skill.
"""

from __future__ import annotations

import math
from collections import defaultdict
from datetime import date, datetime, timedelta
from statistics import median
from typing import Any

from sqlalchemy import func, select

from database import async_session
from models import StockDailyBar, StockUniverseSnapshot, TradingSkillValidationRun
from quant.jobs import create_job, get_job, latest_running_job, spawn, update_job
from quant.reflexivity_skill import build_reflexivity_diagnosis
from quant.trading_skill_features import build_skill_features, normalize_daily_bars
from quant.trading_skills import evaluate_skill
from services.data_collector import shanghai_now
from services.trading_skill_registry import (
    apply_validation_metrics,
    get_registered_skill,
)


HORIZONS = {
    "skill_01_price_volume_efficiency": 5,
    "skill_02_absorption_pressure": 5,
    "skill_03_abnormal_turnover": 5,
    "skill_04_false_breakdown_reclaim": 10,
    "skill_05_trend_reacceleration": 20,
    "skill_06_low_position_relaunch": 20,
    "skill_07_breakout_quality": 5,
    "skill_08_behavior_imbalance": 3,
    "skill_09_auction_intraday_confirm": 1,
    "skill_10_behavior_reflexivity": 5,
}
RISK_SKILLS = {"skill_08_behavior_imbalance"}
RISK_STAGES = {
    "INEFFICIENT_UP", "EFFICIENT_DOWN", "DISTRIBUTION_RISK", "SELL_PRESSURE",
    "FALSE_BREAKOUT_RISK", "PANIC_EXCHANGE", "PANIC", "HIGH_LEVEL_REFLEXIVITY_DECAY",
    "NEGATIVE_REFLEXIVITY_ACCELERATION",
}


def _num(value: Any) -> float | None:
    if value in (None, "", "-") or isinstance(value, bool):
        return None
    try:
        output = float(value)
    except (TypeError, ValueError):
        return None
    return output if math.isfinite(output) else None


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _max_drawdown(returns: list[float]) -> float:
    equity = 1.0
    peak = equity
    maximum = 0.0
    for value in returns:
        equity *= 1 + value
        peak = max(peak, equity)
        if peak:
            maximum = max(maximum, (peak - equity) / peak * 100)
    return maximum


def _safe_date(raw: Any) -> date | None:
    if isinstance(raw, date):
        return raw
    try:
        return date.fromisoformat(str(raw)[:10])
    except (TypeError, ValueError):
        return None


COMMISSION_RATE = 0.00025
STAMP_TAX_RATE = 0.001
SLIPPAGE_RATE = 0.002


def _limit_pct(stock_code: str) -> float:
    code = str(stock_code or "")
    if code.startswith(("300", "301", "688", "689")):
        return 0.20
    if code.startswith(("4", "8", "92")):
        return 0.30
    return 0.10


def _future_label(rows: list[dict[str, Any]], index: int, horizon: int, market_returns: dict[date, float], stock_code: str) -> dict[str, float | None]:
    if index >= len(rows) - 1:
        return {"return": None, "excess": None, "mfe": None, "mae": None}
    future = rows[index + 1:min(len(rows), index + horizon + 1)]
    first = future[0]
    # Signal is formed after T close; the earliest executable price is T+1
    # open. Missing/open-unfillable rows are a data gap, not a T-close fill.
    entry = _num(first.get("open"))
    if entry in (None, 0) or not future:
        return {"return": None, "excess": None, "mfe": None, "mae": None}
    previous_close = _num(rows[index].get("close"))
    opening_high = _num(first.get("high"))
    opening_low = _num(first.get("low"))
    if first.get("is_suspended"):
        return {"return": None, "excess": None, "mfe": None, "mae": None}
    if previous_close and opening_high is not None and opening_low is not None and opening_high == opening_low == entry:
        if abs(entry / previous_close - 1) >= _limit_pct(stock_code) - 0.005:
            return {"return": None, "excess": None, "mfe": None, "mae": None}
    final = future[-1]["close"] / entry - 1
    highs = [(_num(item.get("high")) or item["close"]) / entry - 1 for item in future]
    lows = [(_num(item.get("low")) or item["close"]) / entry - 1 for item in future]
    benchmark = 1.0
    for item in future:
        benchmark *= 1 + (market_returns.get(item["trade_date"]) or 0)
    benchmark -= 1
    round_trip_cost = COMMISSION_RATE * 2 + STAMP_TAX_RATE + SLIPPAGE_RATE * 2
    net_return = final - round_trip_cost
    return {"return": net_return, "excess": net_return - benchmark, "mfe": max(highs) - SLIPPAGE_RATE, "mae": min(lows) - SLIPPAGE_RATE}


def _metrics(cases: list[dict[str, Any]], *, risk: bool = False) -> dict[str, Any]:
    if not cases:
        return {"sample_size": 0, "precision": None, "recall": None, "hit_rate": None, "avg_excess_return": None, "profit_loss_ratio": None, "max_drawdown": None, "brier_score": None}
    predicted = [case for case in cases if case["predicted"]]
    actual_positive = [case for case in cases if case["actual_positive"]]
    true_positive = [case for case in predicted if case["actual_positive"]]
    event_returns = [case["excess"] for case in predicted if case["excess"] is not None]
    wins = [value for value in event_returns if value > 0]
    losses = [value for value in event_returns if value <= 0]
    avg_loss = abs(_mean(losses) or 0)
    brier_values = []
    for case in cases:
        probability = max(0, min(1, (case.get("score") or 0) / 100))
        brier_values.append((probability - (1 if case["actual_positive"] else 0)) ** 2)
    return {
        "sample_size": len(predicted), "evaluated_cases": len(cases),
        "precision": len(true_positive) / len(predicted) if predicted else None,
        "recall": len(true_positive) / len(actual_positive) if actual_positive else None,
        "hit_rate": len(true_positive) / len(predicted) if predicted else None,
        "avg_excess_return": _mean(event_returns),
        "median_excess_return": median(event_returns) if event_returns else None,
        "profit_loss_ratio": (_mean(wins) or 0) / avg_loss if avg_loss else None,
        "max_drawdown": _max_drawdown(event_returns) if event_returns else None,
        "mfe": _mean([case["mfe"] for case in predicted if case.get("mfe") is not None]),
        "mae": _mean([case["mae"] for case in predicted if case.get("mae") is not None]),
        "brier_score": _mean(brier_values),
        "risk_target": risk,
    }


class TradingSkillValidationService:
    async def _record_blocked_report(
        self,
        report: dict[str, Any],
        *,
        skill_id: str,
        definition: dict[str, Any],
        start: date,
        end: date,
    ) -> dict[str, Any]:
        """Persist blocked/failed attempts too; data gaps must be auditable."""
        now = shanghai_now().replace(tzinfo=None)
        report.setdefault("experiment_id", f"skill_validation_{skill_id}_{now.strftime('%Y%m%d%H%M%S%f')}")
        report["report_hash"] = _hash_report(report)
        try:
            async with async_session() as session:
                session.add(TradingSkillValidationRun(
                    experiment_id=report["experiment_id"], skill_id=skill_id,
                    skill_version=str(definition.get("skill_version") or "1.0.0"),
                    status=str(report.get("status") or "INSUFFICIENT_DATA"),
                    lifecycle_before=str(definition.get("lifecycle_state") or "EXPERIMENTAL"),
                    lifecycle_after=str(definition.get("lifecycle_state") or "EXPERIMENTAL"),
                    start_date=start, end_date=end, data_cutoff_time=now,
                    sample_size=int(report.get("sample_size") or 0), parameters=report.get("parameters") or {},
                    metrics=report.get("metrics") or {}, partitions=report.get("partitions") or {},
                    walk_forward=report.get("walk_forward") or [], decay_monitor=report.get("decay_monitor") or {},
                    audit=report.get("audit") or {}, report_hash=report["report_hash"],
                    started_at=now, completed_at=now,
                ))
                await session.commit()
        except Exception as exc:
            report["persistence_warning"] = type(exc).__name__
        return report

    async def _load_data(self, start: date, end: date, max_stocks: int) -> tuple[dict[str, list[dict[str, Any]]], dict[str, Any]]:
        async with async_session() as session:
            code_rows = (await session.execute(
                select(StockDailyBar.stock_code)
                .where(StockDailyBar.trade_date.between(start, end))
                .group_by(StockDailyBar.stock_code)
                .order_by(StockDailyBar.stock_code)
                .limit(max_stocks)
            )).all()
            codes = [str(row[0]) for row in code_rows]
            rows = (await session.execute(
                select(StockDailyBar).where(
                    StockDailyBar.stock_code.in_(codes),
                    StockDailyBar.trade_date.between(start, end),
                ).order_by(StockDailyBar.stock_code, StockDailyBar.trade_date)
            )).scalars().all() if codes else []
            universe_sessions = int((await session.execute(
                select(func.count(func.distinct(StockUniverseSnapshot.trade_date))).where(
                    StockUniverseSnapshot.trade_date.between(start, end)
                )
            )).scalar_one() or 0)
            universe_members = int((await session.execute(
                select(func.count(func.distinct(StockUniverseSnapshot.stock_code))).where(
                    StockUniverseSnapshot.trade_date.between(start, end)
                )
            )).scalar_one() or 0)
        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            grouped[row.stock_code].append({
                "trade_date": row.trade_date, "open_price": row.open_price,
                "close_price": row.close_price, "high_price": row.high_price,
                "low_price": row.low_price, "volume": row.volume,
                "amount": row.amount, "turnover": row.turnover,
                "stock_name": row.stock_name,
                "available_time": row.updated_at.isoformat() if row.updated_at else row.trade_date.isoformat(),
            })
        normalized = {
            code: normalize_daily_bars(items)
            for code, items in grouped.items()
            if "ST" not in str(items[0].get("stock_name") or "").upper()
            and "退" not in str(items[0].get("stock_name") or "")
        }
        return normalized, {
            "stock_count": len(normalized), "bar_count": sum(len(items) for items in normalized.values()),
            "universe_sessions": universe_sessions, "universe_members": universe_members,
            "historical_universe_complete": universe_sessions >= 250 and universe_members > 0,
        }

    @staticmethod
    def _market_returns(grouped: dict[str, list[dict[str, Any]]]) -> dict[date, float]:
        daily: dict[date, list[float]] = defaultdict(list)
        for rows in grouped.values():
            for index in range(1, len(rows)):
                previous, current = rows[index - 1]["close"], rows[index]["close"]
                if previous > 0:
                    daily[rows[index]["trade_date"]].append(current / previous - 1)
        return {day: sum(values) / len(values) for day, values in daily.items() if values}

    async def run_validation(self, request: dict[str, Any], progress=None) -> dict[str, Any]:
        skill_id = str(request.get("skill_id") or "").strip()
        definition = await get_registered_skill(skill_id)
        if definition is None:
            raise ValueError("交易Skill不存在")
        requested_start = _safe_date(request.get("start_date")) or (shanghai_now().date() - timedelta(days=365))
        requested_end = min(_safe_date(request.get("end_date")) or shanghai_now().date(), shanghai_now().date())
        if skill_id == "skill_09_auction_intraday_confirm":
            return await self._record_blocked_report({
                "status": "REALTIME_SHADOW_ONLY", "skill": definition,
                "available": False, "sample_size": 0,
                "reason": "历史竞价/开盘分钟样本不足；系统只使用前向真实快照运行Shadow，不伪造回测。",
                "audit": {"no_future_data": True, "promotion_blocked": True},
            }, skill_id=skill_id, definition=definition, start=requested_start, end=requested_end)
        start = requested_start
        end = requested_end
        end = min(end, shanghai_now().date())
        if end <= start:
            raise ValueError("验证结束日期必须晚于开始日期")
        max_stocks = max(20, min(int(request.get("max_stocks") or 150), 500))
        if progress:
            await progress(12, "loading_data", "读取点时日线与历史股票池覆盖")
        grouped, inventory = await self._load_data(start, end, max_stocks)
        horizon = HORIZONS.get(skill_id, 5)
        min_sessions = 60
        eligible = {code: rows for code, rows in grouped.items() if len(rows) >= min_sessions + horizon}
        if progress:
            await progress(30, "building_features", f"已载入{len(eligible)}只满足历史长度的股票")
        if not eligible:
            return await self._record_blocked_report({
                "status": "INSUFFICIENT_DATA", "skill": definition, "available": False, "sample_size": 0,
                "inventory": inventory, "reason": "没有股票达到60个历史日线加前瞻标签窗口。",
                "audit": {"no_future_data": True, "promotion_blocked": True},
            }, skill_id=skill_id, definition=definition, start=start, end=end)
        market_returns = self._market_returns(eligible)
        cases: list[dict[str, Any]] = []
        for code, rows in eligible.items():
            for index in range(min_sessions, len(rows) - horizon):
                as_of = rows[index]["trade_date"]
                context = {"market_return_1d": market_returns.get(as_of), "market_state": "historical_unavailable"}
                point_rows = rows[:index + 1]
                if skill_id == "skill_10_behavior_reflexivity":
                    # Use the complete six-dimensional PIT calculator for
                    # Skill 10.  The legacy feature adapter remains useful
                    # for the generic registry, but it cannot validate the
                    # liquidity map, psychology transition or pressure
                    # dynamics promised by this skill.
                    diagnosis = build_reflexivity_diagnosis(
                        point_rows,
                        as_of=as_of,
                        context=context,
                        symbol=code,
                    )
                    result = diagnosis.get("skill_result") or {}
                    result["diagnosis_level"] = diagnosis.get("diagnosis_level")
                    result["candidate_type"] = diagnosis.get("candidate_type")
                else:
                    features = build_skill_features(point_rows, as_of=as_of, context=context)
                    result = evaluate_skill(skill_id, features)
                if result.get("signal_type") == "INSUFFICIENT_DATA" or result.get("score") is None:
                    continue
                outcome = _future_label(rows, index, horizon, market_returns, code)
                if outcome["excess"] is None:
                    continue
                risk = skill_id in RISK_SKILLS
                actual_positive = outcome["excess"] <= 0 if risk else outcome["excess"] > 0
                predicted = bool(result.get("detected"))
                # A risk-labelled stage predicts adverse outcome; a structural
                # skill's negative stage is retained as a rejected event.
                if not risk and result.get("stage") in RISK_STAGES:
                    predicted = False
                cases.append({
                    "date": as_of.isoformat(), "code": code, "predicted": predicted,
                    "actual_positive": actual_positive, "score": result.get("score"),
                    "excess": outcome["excess"], "mfe": outcome["mfe"], "mae": outcome["mae"],
                    "stage": result.get("stage"), "diagnosis_level": result.get("diagnosis_level"),
                })
        if progress:
            await progress(72, "walk_forward", f"已生成{len(cases)}个点时评估样本")
        cases.sort(key=lambda item: item["date"])
        if not cases:
            metrics = _metrics([])
            status = "INSUFFICIENT_DATA"
            partitions: dict[str, Any] = {}
            windows: list[dict[str, Any]] = []
        else:
            dates = sorted({_safe_date(item["date"]) for item in cases if _safe_date(item["date"])})
            cut1 = dates[max(0, int(len(dates) * 0.6) - 1)]
            cut2 = dates[max(0, int(len(dates) * 0.8) - 1)]
            partitions = {
                "train": _metrics([item for item in cases if _safe_date(item["date"]) <= cut1], risk=skill_id in RISK_SKILLS),
                "validation": _metrics([item for item in cases if cut1 < _safe_date(item["date"]) <= cut2], risk=skill_id in RISK_SKILLS),
                "out_of_sample": _metrics([item for item in cases if _safe_date(item["date"]) > cut2], risk=skill_id in RISK_SKILLS),
            }
            windows = []
            chunk = max(1, len(dates) // 3)
            for index in range(3):
                window_dates = dates[index * chunk:(index + 1) * chunk if index < 2 else len(dates)]
                if not window_dates:
                    continue
                window_cases = [item for item in cases if _safe_date(item["date"]) in window_dates]
                windows.append({"from": window_dates[0].isoformat(), "to": window_dates[-1].isoformat(), **_metrics(window_cases, risk=skill_id in RISK_SKILLS)})
            metrics = _metrics(cases, risk=skill_id in RISK_SKILLS)
            decay = {}
            end_day = dates[-1]
            for days in (30, 60, 120):
                subset = [item for item in cases if _safe_date(item["date"]) and _safe_date(item["date"]) >= end_day - timedelta(days=days)]
                decay[f"{days}d"] = _metrics(subset, risk=skill_id in RISK_SKILLS)
            metrics["decay"] = decay
            # Historical universe coverage is a promotion gate even when
            # observed daily bars can produce a descriptive baseline.
            status = "LOW_SAMPLE" if metrics.get("sample_size", 0) < 300 or not inventory["historical_universe_complete"] else "VALIDATION_READY"
        now = shanghai_now().replace(tzinfo=None)
        lifecycle_after = definition.get("lifecycle_state") or "EXPERIMENTAL"
        reason = "竞价Skill不进入历史回测" if skill_id == "skill_09_auction_intraday_confirm" else "等待注册表生命周期门槛"
        if cases:
            updated, reason = await apply_validation_metrics(skill_id, metrics, windows, completed_at=now)
            lifecycle_after = updated.get("lifecycle_state") or lifecycle_after
        report = {
            "experiment_id": f"skill_validation_{skill_id}_{now.strftime('%Y%m%d%H%M%S')}",
            "status": status, "available": bool(cases), "skill": updated if cases else definition,
            "skill_id": skill_id, "start_date": start.isoformat(), "end_date": end.isoformat(),
            "data_cutoff_time": now.isoformat(), "sample_size": metrics.get("sample_size", 0),
            "metrics": metrics, "partitions": partitions, "walk_forward": windows,
            "inventory": inventory, "lifecycle_before": definition.get("lifecycle_state"),
            "lifecycle_after": lifecycle_after, "lifecycle_reason": reason,
            "audit": {
                "no_future_data": True, "available_time_rule": "trade_date/updated_at <= signal date",
                "t_plus_execution": "信号日收盘形成，T+1开盘成交；未使用同日收盘成交",
                "execution_cost_model": {
                    "entry": "T+1开盘", "commission_rate": COMMISSION_RATE,
                    "stamp_tax_rate_on_sell": STAMP_TAX_RATE,
                    "slippage_rate_each_side": SLIPPAGE_RATE,
                },
                "limit_fill_policy": "疑似一字涨跌停且无可成交价格的T+1样本剔除，不替换成交日",
                "adjustment_policy": "StockDailyBar未携带可审计复权/分红版本时不宣称复权严格性",
                "historical_universe_complete": inventory["historical_universe_complete"],
                "promotion_blocked": not inventory["historical_universe_complete"] or metrics.get("sample_size", 0) < 300,
                "warnings": [
                    "当前历史股票池仅前向积累，日线观测基线可能存在幸存者/停牌偏差。",
                    "未有公告日财务、板块历史成分和L2数据时不补造。",
                ],
            },
        }
        report["report_hash"] = _hash_report(report)
        try:
            async with async_session() as session:
                session.add(TradingSkillValidationRun(
                    experiment_id=report["experiment_id"], skill_id=skill_id,
                    skill_version=str((report.get("skill") or {}).get("skill_version") or "1.0.0"),
                    status=status, lifecycle_before=str(report.get("lifecycle_before") or "EXPERIMENTAL"),
                    lifecycle_after=str(lifecycle_after), start_date=start, end_date=end,
                    data_cutoff_time=now, sample_size=int(report.get("sample_size") or 0),
                    parameters={"max_stocks": max_stocks, "horizon": horizon}, metrics=metrics,
                    partitions=partitions, walk_forward=windows,
                    decay_monitor=metrics.get("decay") or {}, audit=report["audit"], report_hash=report["report_hash"],
                    started_at=now, completed_at=now,
                ))
                await session.commit()
        except Exception as exc:
            report["persistence_warning"] = type(exc).__name__
        if progress:
            await progress(100, "completed", "Skill验证报告已生成，未通过门槛的技能保持研究状态")
        return report

    async def start(self, request: dict[str, Any]) -> dict[str, Any]:
        if not str(request.get("skill_id") or "").strip():
            raise ValueError("skill_id不能为空")
        running = latest_running_job("skill_validation")
        if running:
            return {**running, "already_running": True}
        job = create_job("skill_validation", "skill_validation", {"request": dict(request)})
        spawn(self._run_job(job["job_id"], dict(request)))
        return job

    async def _run_job(self, job_id: str, request: dict[str, Any]) -> None:
        update_job("skill_validation", job_id, status="running", phase="loading_data", progress=5, message="正在锁定PIT数据清单", started_at=shanghai_now().isoformat())

        async def progress(value: int, phase: str, message: str) -> None:
            update_job("skill_validation", job_id, progress=value, phase=phase, message=message)

        try:
            report = await self.run_validation(request, progress=progress)
            update_job("skill_validation", job_id, status="completed", phase="completed", progress=100, message="验证报告完成", result=report, completed_at=shanghai_now().isoformat())
        except Exception as exc:
            update_job("skill_validation", job_id, status="failed", phase="failed", progress=100, message="验证失败", error=f"{type(exc).__name__}: {exc}", completed_at=shanghai_now().isoformat())

    def job(self, job_id: str) -> dict[str, Any] | None:
        return get_job("skill_validation", job_id)

    async def latest(self, skill_id: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
        async with async_session() as session:
            statement = select(TradingSkillValidationRun).order_by(TradingSkillValidationRun.completed_at.desc()).limit(limit)
            if skill_id:
                statement = statement.where(TradingSkillValidationRun.skill_id == skill_id)
            rows = (await session.execute(statement)).scalars().all()
        return [{
            "experiment_id": row.experiment_id, "skill_id": row.skill_id, "status": row.status,
            "lifecycle_before": row.lifecycle_before, "lifecycle_after": row.lifecycle_after,
            "start_date": row.start_date.isoformat(), "end_date": row.end_date.isoformat(),
            "sample_size": row.sample_size, "metrics": row.metrics, "partitions": row.partitions,
            "walk_forward": row.walk_forward, "decay_monitor": row.decay_monitor,
            "audit": row.audit, "report_hash": row.report_hash,
            "completed_at": row.completed_at.isoformat() if row.completed_at else None,
        } for row in rows]


def _hash_report(report: dict[str, Any]) -> str:
    import hashlib
    import json
    payload = json.dumps(report, ensure_ascii=False, sort_keys=True, default=str).encode()
    return hashlib.sha256(payload).hexdigest()


trading_skill_validation_service = TradingSkillValidationService()
