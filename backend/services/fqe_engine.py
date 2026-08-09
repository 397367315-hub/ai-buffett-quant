"""Auditable fundamental long-horizon stock selection engines.

This module implements the two engines described in the FQE guide without
introducing pandas/cvxpy as runtime requirements.  It deliberately reports
missing point-in-time fields instead of treating them as zero or as a pass.
The current quote universe is suitable for a present-day research snapshot.
The comparison service enriches it from a dated security master, while a true
historical backtest still requires dated financial and quote snapshots.
"""

from __future__ import annotations

import asyncio
import math
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import select

from config import settings
from database import async_session
from models import StockDailyBar
from quant.indicators import normalize_snapshot_stock
from quant.jobs import create_job, get_job, latest_running_job, spawn, update_job
from quant.market_cache import load_quant_market_snapshot, save_quant_market_snapshot
from quant.storage import quant_store
from services.data_collector import collector, shanghai_now
from services.fqe_reference_data import fqe_reference_data
from services.stock_features import stock_feature_service


FQE_FINANCIAL_FIELDS = {
    "gross_margin",
    "revenue_growth",
    "deducted_profit_growth",
    "ocf_to_profit",
    "revenue_ttm",
    "net_profit_ttm",
    "deducted_profit_ttm",
    "operating_cf_ttm",
    "ocf_to_profit_ttm",
    "debt_ratio",
    "receivable_to_revenue",
    "net_profit",
    "is_profitable_non_st",
}


def _number(value: Any) -> float | None:
    if value in (None, "", "-") or isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _percentage(value: Any) -> float | None:
    """Return percentage points while accepting guide-style decimal inputs."""
    result = _number(value)
    if result is None:
        return None
    return result * 100 if 0 < abs(result) <= 1 else result


def _market_cap_yi(stock: dict) -> float | None:
    value = _number(stock.get("market_cap_yi"))
    if value is None:
        value = _number(stock.get("market_cap"))
    if value is None:
        value = _number(stock.get("total_mv"))
    if value is None:
        return None
    # Normalized quant snapshots use 亿元; source-native records use yuan.
    return value / 1e8 if value > 1_000_000 else value


def _pe(stock: dict) -> float | None:
    return _number(stock.get("pe_ttm") if "pe_ttm" in stock else stock.get("pe"))


def _roe_ttm(stock: dict) -> float | None:
    return _percentage(stock.get("roe_ttm") if "roe_ttm" in stock else stock.get("roe"))


def _sector(stock: dict) -> str:
    return str(stock.get("industry") or stock.get("sector") or "").strip() or "未知行业"


def _is_special(stock: dict) -> bool:
    name = str(stock.get("name") or "").upper()
    return "ST" in name or "退" in str(stock.get("name") or "")


def _zscore(values: list[float | None]) -> list[float | None]:
    usable = [value for value in values if value is not None and math.isfinite(value)]
    if not usable:
        return [None for _ in values]
    mean = sum(usable) / len(usable)
    variance = sum((value - mean) ** 2 for value in usable) / len(usable)
    deviation = math.sqrt(variance)
    if deviation <= 1e-12:
        return [0.0 if value is not None else None for value in values]
    return [((value - mean) / deviation) if value is not None else None for value in values]


def _solve_linear(matrix: list[list[float]], vector: list[float]) -> list[float]:
    """Solve a small regularized linear system with Gaussian elimination."""
    size = len(vector)
    if size == 0:
        return []
    if len(matrix) != size or any(len(row) != size for row in matrix):
        raise ValueError("线性方程组维度不一致")
    augmented = [
        [float(value) for value in matrix[row]] + [float(vector[row])]
        for row in range(size)
    ]
    pivot_columns: list[int] = []
    row_index = 0
    for column in range(size):
        pivot_row = max(range(row_index, size), key=lambda row: abs(augmented[row][column]))
        if abs(augmented[pivot_row][column]) <= 1e-12:
            continue
        augmented[row_index], augmented[pivot_row] = augmented[pivot_row], augmented[row_index]
        divisor = augmented[row_index][column]
        augmented[row_index] = [value / divisor for value in augmented[row_index]]
        for row in range(size):
            if row == row_index:
                continue
            factor = augmented[row][column]
            if abs(factor) <= 1e-12:
                continue
            augmented[row] = [
                left - factor * right
                for left, right in zip(augmented[row], augmented[row_index])
            ]
        pivot_columns.append(column)
        row_index += 1
        if row_index == size:
            break
    solution = [0.0] * size
    for row, column in enumerate(pivot_columns):
        solution[column] = augmented[row][-1]
    return solution


def _neutralize(values: list[float], industries: list[str], market_caps: list[float]) -> tuple[list[float], str]:
    """Residualize a factor against log market cap and industry dummies."""
    labels = sorted(set(industries))
    columns = 2 + max(0, len(labels) - 1)
    design: list[list[float]] = []
    for industry, market_cap in zip(industries, market_caps):
        row = [1.0, math.log(max(market_cap, 1e-6))]
        row.extend(1.0 if industry == label else 0.0 for label in labels[1:])
        design.append(row)

    gram = [[0.0 for _ in range(columns)] for _ in range(columns)]
    rhs = [0.0 for _ in range(columns)]
    for row, value in zip(design, values):
        for left in range(columns):
            rhs[left] += row[left] * value
            for right in range(columns):
                gram[left][right] += row[left] * row[right]
    # Ridge keeps one-industry/small-sample groups numerically stable.
    for index in range(columns):
        gram[index][index] += 1e-6
    beta = _solve_linear(gram, rhs)
    residuals = [
        value - sum(coefficient * feature for coefficient, feature in zip(beta, row))
        for value, row in zip(values, design)
    ]
    method = "ols_residual_industry_log_market_cap"
    if len(labels) <= 1:
        method = "ols_residual_log_market_cap_single_industry"
    return residuals, method


def _bounded_sum_variable_bounds(
    values: list[float],
    total: float,
    lowers: list[float],
    uppers: list[float],
) -> list[float]:
    """Project onto a simplex with a possibly different upper bound per item."""
    if not values:
        return []
    if len(values) != len(lowers) or len(values) != len(uppers):
        raise ValueError("权重投影维度不一致")
    output = [min(upper, max(lower, value)) for value, lower, upper in zip(values, lowers, uppers)]
    for _ in range(100):
        difference = total - sum(output)
        if abs(difference) <= 1e-10:
            break
        if difference > 0:
            capacity = [max(0.0, upper - value) for value, upper in zip(output, uppers)]
            total_capacity = sum(capacity)
            if total_capacity <= 1e-12:
                break
            for index, available in enumerate(capacity):
                output[index] = min(uppers[index], output[index] + difference * available / total_capacity)
        else:
            capacity = [max(0.0, value - lower) for value, lower in zip(output, lowers)]
            total_capacity = sum(capacity)
            if total_capacity <= 1e-12:
                break
            for index, available in enumerate(capacity):
                output[index] = max(lowers[index], output[index] + difference * available / total_capacity)
    return output


def _bounded_sum(values: list[float], total: float, lower: float, upper: float) -> list[float]:
    return _bounded_sum_variable_bounds(values, total, [lower] * len(values), [upper] * len(values))


def _project_constraints(
    values: list[float],
    industries: list[str],
    *,
    lower: float,
    upper: float,
    industry_cap: float,
) -> list[float]:
    """Project weights onto box, full-investment, and industry-cap constraints.

    The projection is performed at industry-group level first.  Re-running an
    unconstrained stock-level simplex projection after applying the industry
    cap can put weight back into a capped group, so both levels are allocated
    together here.
    """
    if not values:
        return []
    if len(values) != len(industries):
        raise ValueError("权重与行业数量不一致")
    groups: dict[str, list[int]] = defaultdict(list)
    for index, industry in enumerate(industries):
        groups[industry].append(index)

    group_items = list(groups.values())
    baseline = lower * len(values)
    remaining = 1.0 - baseline
    group_upper_excess = [
        max(0.0, min(len(indexes) * (upper - lower), industry_cap - len(indexes) * lower))
        for indexes in group_items
    ]
    if (
        remaining < -1e-10
        or sum(group_upper_excess) < remaining - 1e-10
        or any(industry_cap < len(indexes) * lower - 1e-10 for indexes in group_items)
    ):
        # The requested constraints are mathematically infeasible. Return the
        # best box-constrained result; the audit will expose the violation.
        return _bounded_sum(values, 1.0, lower, upper)

    desired_group_excess = [
        sum(max(0.0, values[index] - lower) for index in indexes)
        for indexes in group_items
    ]
    allocated_group_excess = _bounded_sum_variable_bounds(
        desired_group_excess,
        remaining,
        [0.0] * len(group_items),
        group_upper_excess,
    )
    output = [lower] * len(values)
    for indexes, group_total in zip(group_items, allocated_group_excess):
        member_excess = _bounded_sum_variable_bounds(
            [values[index] - lower for index in indexes],
            group_total,
            [0.0] * len(indexes),
            [upper - lower] * len(indexes),
        )
        for index, excess in zip(indexes, member_excess):
            output[index] += excess
    return output


def _constraint_audit(weights: list[float], industries: list[str], lower: float, upper: float, cap: float) -> dict:
    by_industry: dict[str, float] = defaultdict(float)
    for weight, industry in zip(weights, industries):
        by_industry[industry] += weight
    return {
        "weight_sum": round(sum(weights), 8),
        "min_weight": round(min(weights), 8) if weights else 0.0,
        "max_weight": round(max(weights), 8) if weights else 0.0,
        "max_industry_weight": round(max(by_industry.values(), default=0.0), 8),
        "industry_weights": {key: round(value, 8) for key, value in sorted(by_industry.items())},
        "violations": [
            *(["weight_sum"] if abs(sum(weights) - 1.0) > 1e-5 else []),
            *(["min_weight"] if weights and min(weights) < lower - 1e-5 else []),
            *(["max_weight"] if weights and max(weights) > upper + 1e-5 else []),
            *(["industry_cap"] if any(value > cap + 1e-5 for value in by_industry.values()) else []),
        ],
    }


class FundamentalQuantEngine:
    """Run the retail-light and institutional-heavy present-day engines."""

    DEFAULT_TOP_N = 10
    DEFAULT_CANDIDATE_POOL = 60
    MIN_MARKET_CAP_YI = 50.0
    MIN_LISTING_DAYS = 365
    MIN_OPTIMIZER_HOLDINGS = math.ceil(1 / 0.15)

    @staticmethod
    def _retail_row(stock: dict, mode: str) -> tuple[dict | None, list[str], list[str]]:
        reasons: list[str] = []
        warnings: list[str] = []
        market_cap = _market_cap_yi(stock)
        roe = _roe_ttm(stock)
        cashflow_ratio = _number(stock.get("ocf_to_profit_ttm"))
        if cashflow_ratio is None:
            cashflow_ratio = _number(stock.get("ocf_to_profit"))
            if cashflow_ratio is not None:
                warnings.append("使用最新披露期现金流比，未形成完整TTM")
        debt_ratio = _percentage(stock.get("debt_ratio"))
        pe = _pe(stock)
        growth = _percentage(
            stock.get("deducted_profit_ttm_yoy")
            if stock.get("deducted_profit_ttm_yoy") is not None
            else stock.get("deducted_profit_growth")
        )
        peg = pe / growth if pe is not None and growth and growth > 0 else None

        if _is_special(stock):
            reasons.append("ST/退市风险标记")
        if stock.get("is_suspended") is True:
            reasons.append("停牌")
        if market_cap is None:
            reasons.append("流动市值缺失")
        elif market_cap < FundamentalQuantEngine.MIN_MARKET_CAP_YI:
            reasons.append(f"流动市值低于{FundamentalQuantEngine.MIN_MARKET_CAP_YI:g}亿")
        if roe is None:
            reasons.append("ROE缺失")
        elif roe < 12:
            reasons.append("ROE低于12%")
        if cashflow_ratio is None:
            reasons.append("现金流/净利润缺失")
        elif cashflow_ratio < 0.8:
            reasons.append("现金流含金量低于0.8")
        if debt_ratio is None:
            reasons.append("资产负债率缺失")
        elif debt_ratio > 60:
            reasons.append("资产负债率高于60%")
        if pe is None or pe <= 0:
            reasons.append("PE(TTM)缺失或非正")
        if peg is None or not 0 < peg <= 1:
            reasons.append("PEG不在(0,1]内")

        for field, label in (("list_days", "上市天数"), ("pe_percentile_3y", "PE三年分位")):
            if _number(stock.get(field)) is None:
                if mode == "strict":
                    reasons.append(f"{label}缺失（严格模式不通过）")
                else:
                    warnings.append(f"{label}缺失，宽松模式未将其当作通过")
        if _number(stock.get("list_days")) is not None and _number(stock.get("list_days")) < FundamentalQuantEngine.MIN_LISTING_DAYS:
            reasons.append("上市未满365天")
        if _number(stock.get("pe_percentile_3y")) is not None and _number(stock.get("pe_percentile_3y")) > 35:
            reasons.append("PE三年分位高于35%")

        if reasons:
            return None, reasons, warnings
        peg_score = max(0.0, min(1.0, 1.0 - float(peg)))
        roe_score = max(0.0, min(1.0, float(roe) / 30.0))
        return {
            "code": str(stock.get("code") or ""),
            "name": str(stock.get("name") or ""),
            "industry": _sector(stock),
            "score": round(roe_score * 0.6 + peg_score * 0.4, 6),
            "peg": round(float(peg), 6),
            "roe_ttm": round(float(roe), 4),
            "ocf_to_profit_ttm": round(float(cashflow_ratio), 4),
            "debt_ratio": round(float(debt_ratio), 4),
            "market_cap_yi": round(float(market_cap), 4),
            "weight": 0.0,
            "engine_type": "Retail_Light",
            "data_warnings": warnings,
            "financial_disclosed_at": (stock.get("financial_disclosed_at") or None),
        }, [], warnings

    @classmethod
    def run_retail(cls, stocks: list[dict], top_n: int, mode: str) -> dict:
        accepted: list[dict] = []
        rejection_counts: Counter[str] = Counter()
        examples: list[dict] = []
        warnings: list[str] = []
        for stock in stocks:
            row, reasons, row_warnings = cls._retail_row(stock, mode)
            warnings.extend(row_warnings)
            if row is None:
                for reason in reasons:
                    rejection_counts[reason] += 1
                if len(examples) < 20:
                    examples.append({
                        "code": str(stock.get("code") or ""),
                        "name": str(stock.get("name") or ""),
                        "reasons": reasons,
                    })
            else:
                accepted.append(row)
        accepted.sort(key=lambda item: (item["score"], item["peg"]), reverse=True)
        selected = accepted[:max(1, min(top_n, 15))]
        weight = 1 / len(selected) if selected else 0.0
        for row in selected:
            row["weight"] = round(weight, 8)
            row["weight_pct"] = round(weight * 100, 4)
        return {
            "engine_type": "Retail_Light",
            "label": "零售轻量引擎",
            "count": len(selected),
            "holdings": selected,
            "eligible_count": len(accepted),
            "rejection_counts": rejection_counts.most_common(12),
            "excluded_examples": examples,
            "warnings": list(dict.fromkeys(warnings))[:12],
            "method": "硬性排雷 + ROE/PEG排序 + 等权",
            "data_quality": {
                "mode": mode,
                "auditable": mode == "strict" and not warnings,
                "status": "ready" if selected and not warnings else "research_only" if selected else "insufficient",
            },
        }

    @staticmethod
    async def _load_covariance(codes: list[str], days: int = 260) -> tuple[list[list[float]], dict]:
        cutoff = shanghai_now().date() - timedelta(days=days * 2)
        try:
            async with async_session() as session:
                rows = (await session.execute(
                    select(StockDailyBar).where(
                        StockDailyBar.stock_code.in_(codes),
                        StockDailyBar.trade_date >= cutoff,
                    ).order_by(StockDailyBar.stock_code, StockDailyBar.trade_date)
                )).scalars().all()
        except Exception as exc:
            return [], {"available": False, "warning": f"历史协方差读取失败（{type(exc).__name__}）"}

        returns_by_code: dict[str, dict[date, float]] = defaultdict(dict)
        grouped: dict[str, list[StockDailyBar]] = defaultdict(list)
        for row in rows:
            if row.close_price is not None and row.close_price > 0:
                grouped[row.stock_code].append(row)
        for code, bars in grouped.items():
            for previous, current in zip(bars, bars[1:]):
                if previous.close_price and current.close_price and previous.close_price > 0:
                    returns_by_code[code][current.trade_date] = current.close_price / previous.close_price - 1
        all_dates = set().union(*(values.keys() for values in returns_by_code.values())) if returns_by_code else set()
        matrix: list[list[float]] = []
        usable_days = 0
        for left_code in codes:
            left_values = returns_by_code.get(left_code, {})
            row_values: list[float] = []
            for right_code in codes:
                right_values = returns_by_code.get(right_code, {})
                common = sorted(all_dates & set(left_values) & set(right_values))
                if len(common) >= 2:
                    left_mean = sum(left_values[item] for item in common) / len(common)
                    right_mean = sum(right_values[item] for item in common) / len(common)
                    covariance = sum(
                        (left_values[item] - left_mean) * (right_values[item] - right_mean)
                        for item in common
                    ) / (len(common) - 1) * 252
                    row_values.append(covariance)
                    usable_days = max(usable_days, len(common))
                else:
                    row_values.append(0.04 ** 2)
            matrix.append(row_values)
        if not matrix:
            return [], {"available": False, "warning": "没有足够历史日线形成协方差矩阵"}
        for index in range(len(matrix)):
            matrix[index][index] = max(matrix[index][index], 0.04 ** 2)
        return matrix, {
            "available": usable_days >= 30,
            "usable_days": usable_days,
            "stock_count": len(returns_by_code),
            "warning": None if usable_days >= 30 else "历史收益覆盖不足30个共同交易日，协方差使用收缩/默认方差",
        }

    @classmethod
    async def run_institutional(cls, stocks: list[dict], top_n: int, candidate_pool: int) -> dict:
        eligible: list[dict] = []
        warnings: list[str] = []
        for stock in stocks:
            market_cap = _market_cap_yi(stock)
            pe = _pe(stock)
            roe = _roe_ttm(stock)
            if _is_special(stock) or stock.get("is_suspended") is True:
                continue
            if market_cap is None or market_cap < cls.MIN_MARKET_CAP_YI or pe is None or pe <= 0 or roe is None:
                continue
            cashflow = _number(stock.get("ocf_to_profit_ttm"))
            if cashflow is None:
                cashflow = _number(stock.get("ocf_to_profit"))
            growth = _percentage(stock.get("deducted_profit_ttm_yoy") or stock.get("deducted_profit_growth"))
            eligible.append({
                "stock": stock,
                "market_cap": market_cap,
                "pe": pe,
                "roe": roe,
                "cashflow": cashflow,
                "growth": growth,
                "industry": _sector(stock),
            })
        if not eligible:
            return {
                "engine_type": "Institutional_Heavy", "label": "机构重构引擎", "count": 0,
                "holdings": [], "eligible_count": 0, "warnings": ["缺少可用的正PE、ROE和市值数据"],
                "method": "因子标准化 + 行业/市值OLS残差 + 约束二次规划近似",
                "data_quality": {"status": "insufficient", "auditable": False},
            }

        if len(eligible) < cls.MIN_OPTIMIZER_HOLDINGS:
            return {
                "engine_type": "Institutional_Heavy", "label": "机构重构引擎", "count": 0,
                "holdings": [], "eligible_count": len(eligible),
                "warnings": [f"机构组合单股上限15%、总仓位100%，至少需要{cls.MIN_OPTIMIZER_HOLDINGS}只合格股票；当前仅有{len(eligible)}只"],
                "method": "因子标准化 + 行业/市值OLS残差 + 约束二次规划近似",
                "data_quality": {"status": "insufficient", "auditable": False},
            }

        roe_z = _zscore([item["roe"] for item in eligible])
        value_z = _zscore([-math.log(max(item["pe"], 0.1)) for item in eligible])
        cash_z = _zscore([item["cashflow"] for item in eligible])
        growth_z = _zscore([item["growth"] for item in eligible])
        raw_values: list[float] = []
        for index, item in enumerate(eligible):
            components = [(roe_z[index], 0.4), (value_z[index], 0.3), (cash_z[index], 0.15), (growth_z[index], 0.15)]
            available = [(value, weight) for value, weight in components if value is not None]
            denominator = sum(weight for _, weight in available) or 1.0
            raw_values.append(sum(value * weight for value, weight in available) / denominator)
        residuals, neutral_method = _neutralize(
            raw_values,
            [item["industry"] for item in eligible],
            [item["market_cap"] for item in eligible],
        )
        for item, raw, residual in zip(eligible, raw_values, residuals):
            item["alpha_raw"] = raw
            item["alpha_neutral"] = residual
        eligible.sort(key=lambda item: item["alpha_neutral"], reverse=True)
        selection_count = min(len(eligible), max(top_n, cls.MIN_OPTIMIZER_HOLDINGS))
        pool = eligible[:max(selection_count, min(candidate_pool, 120))]

        # Seed different industries before filling by alpha so the industry cap
        # has a chance to be feasible for a small personal portfolio.
        industry_best: dict[str, dict] = {}
        for item in pool:
            industry_best.setdefault(item["industry"], item)
        seeds = sorted(industry_best.values(), key=lambda item: item["alpha_neutral"], reverse=True)
        selected: list[dict] = []
        for item in seeds:
            if len(selected) >= selection_count:
                break
            selected.append(item)
        for item in pool:
            if len(selected) >= selection_count:
                break
            if item not in selected:
                selected.append(item)
        selected = selected[:selection_count]
        industries = [item["industry"] for item in selected]
        unique_industries = len(set(industries))
        industry_cap = 0.25
        if unique_industries < 4:
            industry_cap = 1.0
            warnings.append("候选行业少于4个，25%行业上限不可行，本次不启用行业上限")

        codes = [str(item["stock"].get("code") or "") for item in selected]
        covariance, covariance_meta = await cls._load_covariance(codes)
        if covariance_meta.get("warning"):
            warnings.append(str(covariance_meta["warning"]))
        if not covariance:
            covariance = [[0.0 if left != right else 0.04 ** 2 for right in range(len(selected))] for left in range(len(selected))]

        mu = [float(item["alpha_neutral"]) for item in selected]
        weights = [1 / len(selected)] * len(selected) if selected else []
        gamma = 0.5
        if selected:
            for iteration in range(180):
                gradient = []
                for index in range(len(selected)):
                    risk_gradient = sum(covariance[index][j] * weights[j] for j in range(len(selected)))
                    gradient.append(mu[index] - 2 * gamma * risk_gradient)
                step = 0.08 / (1 + iteration / 40)
                trial = [weight + step * value for weight, value in zip(weights, gradient)]
                weights = _project_constraints(
                    trial, industries, lower=0.02, upper=0.15, industry_cap=industry_cap,
                )
        audit = _constraint_audit(weights, industries, 0.02, 0.15, industry_cap)
        if audit["violations"]:
            warnings.append("约束优化未能完全收敛，结果仅作研究参考")
        holdings = []
        for item, weight in zip(selected, weights):
            stock = item["stock"]
            holdings.append({
                "code": str(stock.get("code") or ""),
                "name": str(stock.get("name") or ""),
                "industry": item["industry"],
                "alpha_raw": round(item["alpha_raw"], 6),
                "alpha_neutral": round(item["alpha_neutral"], 6),
                "roe_ttm": round(item["roe"], 4),
                "pe_ttm": round(item["pe"], 4),
                "weight": round(weight, 8),
                "weight_pct": round(weight * 100, 4),
                "engine_type": "Institutional_Heavy",
                "financial_disclosed_at": stock.get("financial_disclosed_at"),
            })
        return {
            "engine_type": "Institutional_Heavy",
            "label": "机构重构引擎",
            "count": len(holdings),
            "holdings": holdings,
            "eligible_count": len(eligible),
            "candidate_pool_count": len(pool),
            "warnings": list(dict.fromkeys(warnings)),
            "method": f"因子Z-Score + {neutral_method} + projected_quadratic",
            "optimizer": {"gamma": gamma, "lower_weight": 0.02, "upper_weight": 0.15, "industry_cap": industry_cap, "constraint_audit": audit},
            "covariance": covariance_meta,
            "data_quality": {
                "status": "ready" if holdings and not warnings else "research_only" if holdings else "insufficient",
                "auditable": False,
                "notes": ["证券主表与估值历史由数据合同单独审计；当前输出是研究日组合快照，不是历史回测。"],
            },
        }


class FQECompareService:
    """Background runner for the dual-engine comparison endpoint."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()

    async def start(self, *, top_n: int = 10, candidate_pool: int = 60, mode: str = "pragmatic", force: bool = False) -> dict:
        if not 5 <= top_n <= 15:
            raise ValueError("组合数量必须在5至15之间")
        if not 20 <= candidate_pool <= 120:
            raise ValueError("机构候选池必须在20至120之间")
        if mode not in {"strict", "pragmatic"}:
            raise ValueError("财务数据模式必须是strict或pragmatic")
        running = latest_running_job("fqe")
        if running and not force:
            return running
        job = create_job("fqe", "fqe", {"top_n": top_n, "candidate_pool": candidate_pool, "mode": mode})
        spawn(self._run(job["job_id"], top_n, candidate_pool, mode, force))
        return job

    async def _run(self, job_id: str, top_n: int, candidate_pool: int, mode: str, force: bool) -> None:
        async with self._lock:
            update_job("fqe", job_id, status="running", phase="market_snapshot", progress=5, message="正在读取全市场行情与缓存", started_at=shanghai_now().isoformat())
            try:
                result = await self.compare(top_n=top_n, candidate_pool=candidate_pool, mode=mode, force=force, job_id=job_id)
                update_job("fqe", job_id, status="completed", phase="completed", progress=100, message=f"双引擎完成，零售{result['retail_portfolio']['count']}只，机构{result['institutional_portfolio']['count']}只", result=result, completed_at=shanghai_now().isoformat())
            except Exception as exc:
                update_job("fqe", job_id, status="failed", phase="failed", progress=100, message="FQE双引擎运行失败", error=f"{type(exc).__name__}: {exc}"[:500], completed_at=shanghai_now().isoformat())

    async def compare(self, *, top_n: int, candidate_pool: int, mode: str, force: bool, job_id: str | None = None) -> dict:
        cached = quant_store.read("market_snapshot")
        fetched_at = None
        try:
            fetched_at = datetime.fromisoformat(str(cached.get("fetched_at") or ""))
        except (TypeError, ValueError):
            pass
        if fetched_at and fetched_at.tzinfo is None:
            fetched_at = fetched_at.replace(tzinfo=shanghai_now().tzinfo)
        age = (shanghai_now() - fetched_at).total_seconds() if fetched_at else None
        stale = False
        if force or not cached.get("stocks") or age is None or age > settings.quant_scan_cache_seconds:
            try:
                snapshot = await collector.fetch_quant_market_snapshot()
                await save_quant_market_snapshot(snapshot)
                quant_store.write("market_snapshot", {"version": 1, **snapshot})
            except Exception as exc:
                snapshot = await load_quant_market_snapshot()
                if not snapshot.get("stocks"):
                    raise RuntimeError(f"行情与缓存均不可用（{type(exc).__name__}）") from exc
                stale = True
        else:
            snapshot = cached
        if stale:
            snapshot = {**snapshot, "is_realtime": False, "source": "cache"}
        contexts = [normalize_snapshot_stock(item) for item in snapshot.get("stocks") or []]
        if not contexts:
            raise RuntimeError("全市场股票池为空")
        if job_id:
            update_job("fqe", job_id, phase="pit_ttm", progress=20, message=f"正在按研究日合并财务披露并计算TTM（{len(contexts)}只）")
        research_date = shanghai_now().date()
        feature_result = await stock_feature_service.enrich(
            contexts,
            FQE_FINANCIAL_FIELDS,
            full_market=bool(snapshot.get("complete")),
            as_of=research_date,
        )
        contexts = feature_result.get("stocks") or contexts
        if job_id:
            update_job(
                "fqe", job_id, phase="reference_data", progress=45,
                message="正在合并上市历史、退市证券主表与三年PE分位",
            )
        reference_result = await fqe_reference_data.enrich(contexts, research_date)
        contexts = reference_result.get("stocks") or contexts
        if job_id:
            update_job("fqe", job_id, phase="retail_engine", progress=55, message="正在运行零售轻量引擎")
        retail = FundamentalQuantEngine.run_retail(contexts, top_n, mode)
        if job_id:
            update_job("fqe", job_id, phase="institutional_engine", progress=70, message="正在进行行业/市值中性化与权重优化")
        institutional = await FundamentalQuantEngine.run_institutional(contexts, top_n, candidate_pool)
        financial_available = sum(1 for item in contexts if ((item.get("_feature_meta") or {}).get("financial") or {}).get("status") == "available")
        ttm_available = sum(1 for item in contexts if item.get("ttm_available"))
        warnings = list(dict.fromkeys(
            (feature_result.get("warnings") or [])
            + (reference_result.get("warnings") or [])
            + retail.get("warnings", [])
            + institutional.get("warnings", [])
        ))
        return {
            "version": 1,
            "engine_mode": "COMPARE_DUAL_ENGINE",
            "generated_at": shanghai_now().isoformat(),
            "as_of_date": research_date.isoformat(),
            "data_date": snapshot.get("data_date"),
            "source": "cache" if stale else snapshot.get("source", "cache"),
            "is_realtime": bool(snapshot.get("is_realtime")) and not stale,
            "cache_used": stale or snapshot.get("source") == "cache",
            "retail_portfolio": retail,
            "institutional_portfolio": institutional,
            "data_contract": {
                "pit_financial": {"status": "current_as_of", "covered": financial_available, "total": len(contexts), "note": "财务记录按NOTICE_DATE不晚于研究日筛选"},
                "ttm": {"status": "available" if ttm_available else "missing", "covered": ttm_available, "total": len(contexts), "formula": "current_period + prior_year_full_year - prior_year_same_period"},
                **(reference_result.get("data_contract") or {}),
            },
            "feature_coverage": feature_result.get("coverage") or {},
            "reference_coverage": reference_result.get("coverage") or {},
            "warnings": warnings,
            "disclaimer": "这是当前研究日的双引擎候选比较，不构成收益率或70%-90%胜率承诺；严格历史回测需补齐退市证券、上市状态和财务PIT历史。",
        }

    @staticmethod
    def get_status(job_id: str) -> dict | None:
        return get_job("fqe", job_id)

    @staticmethod
    def get_latest() -> dict | None:
        jobs = quant_store.read("jobs").get("fqe", {}).values()
        completed = [item for item in jobs if item.get("status") == "completed" and item.get("result")]
        latest = max(completed, key=lambda item: item.get("completed_at") or "", default=None)
        return latest.get("result") if latest else None


fqe_compare_service = FQECompareService()
