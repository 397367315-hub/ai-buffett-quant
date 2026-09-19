"""Pure, auditable rule engine for the three Wildman classic setups.

This module deliberately contains no database or network access.  The service
layer supplies point-in-time bars and metadata, while this module turns only
observable OHLCV facts into a compact row with rule evidence.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from statistics import median
from typing import Any, Iterable


STRATEGY_IDS = ("WM_CLASSIC_520", "WM_CLASSIC_T", "WM_CLASSIC_75A")
MAX_520_SIGNAL_AGE = 5


STRATEGIES: dict[str, dict[str, Any]] = {
    "WM_CLASSIC_520": {
        "id": "WM_CLASSIC_520",
        "name": "520战法",
        "horizon": "短线",
        "holding_period": "不超过5个交易日",
        "summary": "5日线向上穿越20日线，20日线走平或向上且金叉放量；次日收阳并收盘高于金叉日才确认。",
        "entry_rules": [
            "MA5上穿MA20，MA20走平或向上",
            "金叉日成交量至少为前5日均量的1.2倍",
            "金叉次日收阳，且收盘价高于金叉日收盘价",
        ],
        "exit_rules": [
            "有效跌破MA5减半仓",
            "有效跌破MA20清仓",
            "持仓不超过5个交易日，目标2%-5%",
        ],
        "risk_note": "均线信号是价格行为代理，不代表基本面或收益保证。",
    },
    "WM_CLASSIC_T": {
        "id": "WM_CLASSIC_T",
        "name": "老太太扶楼梯",
        "horizon": "超短",
        "holding_period": "1-3日做T，4-5日评估强弱",
        "summary": "仅针对已有可卖底仓的标的，连续下跌或近期跌幅超过10%时执行先卖后买回。",
        "entry_rules": [
            "必须存在可卖的已有持仓，不产生新买入建议",
            "连续5个收盘下跌，或从近期高点回撤超过10%",
            "最近5日有成交额且中位成交额不低于1000万元",
        ],
        "exit_rules": [
            "先卖后买，盘中参考下跌3%-5%接回",
            "第4-5日若转强则评估减仓，不加倍补仓",
        ],
        "risk_note": "T+1可卖性、单边下跌和流动性枯竭会放大风险；未知基本面不认证为安全。",
    },
    "WM_CLASSIC_75A": {
        "id": "WM_CLASSIC_75A",
        "name": "圆弧底75A",
        "horizon": "中线波段",
        "holding_period": "波段持有，按MA75和形态失效退出",
        "summary": "用超过3个月的左跌、底部收缩、右升几何代理，结合MA75走平向上和突破或缩量回踩确认。",
        "entry_rules": [
            "圆弧几何跨度超过3个日历月，左跌、底部收缩、右升",
            "MA75真实可计算且走平或向上",
            "放量突破前高颈线并站上MA75，或两日缩量回踩不破",
        ],
        "exit_rules": [
            "有效跌破圆弧底最低价或MA75退出",
            "理论目标按颈线到圆弧底低点的投影估算，不保证30%-50%收益",
        ],
        "risk_note": "圆弧底与75日线是工程代理，不等同原始定性判断，也不保证30%-50%回报。",
    },
}


@dataclass(frozen=True)
class Bar:
    date: str
    open: float | None
    high: float | None
    low: float | None
    close: float | None
    volume: float | None
    amount: float | None
    change_pct: float | None
    source: str | None = None


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number else None


def _value(row: Any, *names: str) -> Any:
    for name in names:
        if isinstance(row, dict) and name in row:
            return row[name]
        value = getattr(row, name, None)
        if value is not None:
            return value
    return None


def _date_text(value: Any) -> str:
    if isinstance(value, date):
        return value.isoformat()
    return str(value or "")[:10]


def normalize_bars(rows: Iterable[Any]) -> list[Bar]:
    """Normalize and sort one stock's bars without filling missing values."""
    output: list[Bar] = []
    for row in rows:
        trade_date = _date_text(_value(row, "date", "trade_date"))
        close = _number(_value(row, "close", "close_price"))
        if not trade_date or close is None or close <= 0:
            continue
        output.append(Bar(
            date=trade_date,
            open=_number(_value(row, "open", "open_price")),
            high=_number(_value(row, "high", "high_price")),
            low=_number(_value(row, "low", "low_price")),
            close=close,
            volume=_number(_value(row, "volume", "vol")),
            amount=_number(_value(row, "amount")),
            change_pct=_number(_value(row, "change_pct", "pct_chg")),
            source=str(_value(row, "source") or "") or None,
        ))
    return sorted(output, key=lambda item: item.date)


def _mean(values: Iterable[float | None]) -> float | None:
    numbers = [value for value in values if value is not None]
    return sum(numbers) / len(numbers) if numbers else None


def _moving_average(values: list[float | None], period: int, index: int) -> float | None:
    if index + 1 < period:
        return None
    window = values[index + 1 - period:index + 1]
    return _mean(window)


def indicator_bars(rows: Iterable[Any]) -> list[dict[str, Any]]:
    """Return normalized bars plus only causal moving-average indicators."""
    bars = normalize_bars(rows)
    closes = [bar.close for bar in bars]
    output = []
    for index, bar in enumerate(bars):
        item = {
            "date": bar.date,
            "open": bar.open,
            "high": bar.high,
            "low": bar.low,
            "close": bar.close,
            "volume": bar.volume,
            "ma5": _moving_average(closes, 5, index),
            "ma20": _moving_average(closes, 20, index),
            "ma75": _moving_average(closes, 75, index),
        }
        output.append(item)
    return output


def _round(value: float | None, digits: int = 4) -> float | None:
    return round(value, digits) if value is not None else None


def _evidence(rule_id: str, rule_name: str, required: bool, actual: Any, passed: bool | None, source: str) -> dict[str, Any]:
    return {
        "rule_id": rule_id,
        "rule_name": rule_name,
        "required": required,
        "actual": actual,
        "passed": passed,
        "source": source,
    }


def _base_result(strategy_id: str, bars: list[Bar], meta: dict[str, Any]) -> dict[str, Any]:
    definition = STRATEGIES[strategy_id]
    latest = bars[-1] if bars else None
    return {
        "symbol": str(meta.get("symbol") or meta.get("code") or ""),
        "name": str(meta.get("name") or ""),
        "theme_name": str(meta.get("theme_name") or meta.get("sector") or ""),
        "price": _number(meta.get("price")) if meta.get("price") is not None else (latest.close if latest else None),
        "change_pct": _number(meta.get("change_pct")) if meta.get("change_pct") is not None else (latest.change_pct if latest else None),
        "status": "NO_MATCH",
        "signal_date": None,
        "horizon": definition["horizon"],
        "reason": "未达到工程化规则阈值",
        "anchor_price": None,
        "target_price": None,
        "holding_period": definition["holding_period"],
        "evidence": [],
        "risk_notes": [],
    }


def _finish(result: dict[str, Any], *, status: str, reason: str, signal_date: str | None = None, anchor: float | None = None, target: float | None = None, evidence: list[dict[str, Any]] | None = None, risks: list[str] | None = None) -> dict[str, Any]:
    result.update({
        "status": status,
        "reason": reason,
        "signal_date": signal_date,
        "anchor_price": _round(anchor),
        "target_price": _round(target),
        "evidence": evidence or [],
        "risk_notes": risks or [],
    })
    return result


def _evaluate_520(bars: list[Bar], result: dict[str, Any]) -> dict[str, Any]:
    source = "StockDailyBar/NumCat批量stk_factor_pro; MA仅使用<=trade_date的QFQ OHLC"
    insufficient = _evidence("520_HISTORY", "MA20及前一日比较所需历史", True, len(bars), len(bars) >= 21, source)
    if len(bars) < 21:
        return _finish(result, status="风险排除", reason="历史不足，不能判断520金叉", evidence=[insufficient], risks=["缺少至少21个按交易日排序的历史样本，不计入无匹配。"])

    values = [bar.close for bar in bars]
    ma5 = [_moving_average(values, 5, i) for i in range(len(bars))]
    ma20 = [_moving_average(values, 20, i) for i in range(len(bars))]
    crosses: list[tuple[int, float, float]] = []
    for i in range(1, len(bars)):
        if None in (ma5[i - 1], ma20[i - 1], ma5[i], ma20[i]):
            continue
        flat_up = ma20[i] >= ma20[i - 1] * 0.999
        prior_volume = _mean(bar.volume for bar in bars[max(0, i - 5):i])
        volume_expand = prior_volume is not None and bars[i].volume is not None and bars[i].volume >= prior_volume * 1.2
        if ma5[i - 1] <= ma20[i - 1] and ma5[i] > ma20[i] and flat_up and volume_expand and bars[i].close > ma20[i]:
            crosses.append((i, bars[i].close, prior_volume or 0.0))

    evidence = [insufficient]
    if not crosses:
        latest_ma5, latest_ma20 = ma5[-1], ma20[-1]
        evidence.extend([
            _evidence("520_CROSS", "MA5向上穿越MA20", True, {"ma5": _round(latest_ma5), "ma20": _round(latest_ma20)}, False, source),
            _evidence("520_VOLUME", "金叉日成交量/前5日均量（工程化放量代理阈值1.2x，原文仅要求放量）", True, None, False, source),
        ])
        return _finish(result, status="NO_MATCH", reason="最近历史没有满足走平向上和放量条件的真实金叉", evidence=evidence)

    cross_index, cross_close, prior_volume = crosses[-1]
    age_from_cross = len(bars) - 1 - cross_index
    if age_from_cross == 0:
        next_bar = None
        confirmed = False
        waiting = True
    else:
        next_bar = bars[cross_index + 1] if cross_index + 1 < len(bars) else None
        confirmed = bool(next_bar and next_bar.close > (next_bar.open or next_bar.close) and next_bar.close > cross_close)
        waiting = False
    latest_ma5, latest_ma20 = ma5[-1], ma20[-1]
    if age_from_cross > MAX_520_SIGNAL_AGE or (not confirmed and not waiting):
        return _finish(result, status="NO_MATCH", reason="金叉未在次日收阳并高于金叉日收盘，或信号已过5个交易日", evidence=[
            _evidence("520_CROSS", "真实金叉", True, bars[cross_index].date, True, source),
            _evidence("520_CONFIRM", "次日收阳且收盘高于金叉日", True, next_bar.close if next_bar else None, confirmed, source),
        ])
    death_crosses = []
    for i in range(cross_index + 1, len(bars)):
        if None not in (ma5[i - 1], ma20[i - 1], ma5[i], ma20[i]) and ma5[i - 1] >= ma20[i - 1] and ma5[i] <= ma20[i]:
            death_crosses.append(i)
    if death_crosses:
        return _finish(result, status="风险排除", reason="金叉后出现MA5下穿MA20，520信号失效", signal_date=bars[death_crosses[-1]].date, anchor=latest_ma20, evidence=[
            _evidence("520_CONFIRM", "次日收阳且收盘高于金叉日", True, confirmed, confirmed, source),
            _evidence("520_DEATH_CROSS", "金叉后无新的MA5死叉", True, bars[death_crosses[-1]].date, False, source),
        ], risks=["最新死叉覆盖此前金叉，按规则不再确认。"])
    if latest_ma20 is not None and result["price"] is not None and result["price"] < latest_ma20:
        return _finish(result, status="风险排除", reason="价格已有效跌破MA20，520清仓条件触发", signal_date=bars[-1].date, anchor=latest_ma20, evidence=[
            _evidence("520_CONFIRM", "次日收阳且收盘高于金叉日", True, confirmed, confirmed, source),
            _evidence("520_MA20_STOP", "收盘不跌破MA20", True, {"close": result["price"], "ma20": _round(latest_ma20)}, False, source),
        ], risks=["规则定义的MA20清仓条件已触发。"])

    evidence.extend([
        _evidence("520_CROSS", "MA5向上穿越MA20", True, {"date": bars[cross_index].date, "ma5": _round(ma5[cross_index]), "ma20": _round(ma20[cross_index])}, True, source),
        _evidence("520_MA20_SLOPE", "MA20走平或向上", True, _round(ma20[cross_index] - ma20[cross_index - 1]), True, source),
        _evidence("520_VOLUME", "金叉日成交量/前5日均量（工程化放量代理阈值1.2x，原文仅要求放量）", True, _round((bars[cross_index].volume or 0) / prior_volume if prior_volume else None), True, source),
        _evidence("520_CONFIRM", "次日收阳且收盘高于金叉日", True, next_bar.close if next_bar else None, confirmed, source),
        _evidence("520_FRESH", "确认信号不超过5个交易日", True, age_from_cross, age_from_cross <= MAX_520_SIGNAL_AGE, source),
    ])
    if waiting:
        return _finish(result, status="等待确认", reason="今日形成真实金叉，等待下一交易日收阳且收盘高于金叉日", signal_date=bars[-1].date, anchor=latest_ma20, target=cross_close * 1.02, evidence=evidence, risks=["今天金叉不能提前视为确认买点。"])
    return _finish(result, status="已确认", reason="金叉次日收阳且收盘高于金叉日，目标按2%-5%短线管理", signal_date=next_bar.date if next_bar else bars[-1].date, anchor=latest_ma20, target=result["price"] * 1.02 if result["price"] else None, evidence=evidence, risks=["跌破MA5减半仓，跌破MA20清仓；持仓不超过5个交易日。"])


def _evaluate_t(bars: list[Bar], result: dict[str, Any], meta: dict[str, Any]) -> dict[str, Any]:
    source = "StockDailyBar/NumCat批量stk_factor_pro; 价格与成交额同一来源"
    holding = bool(meta.get("holding"))
    evidence = [_evidence(
        "T_HOLDING",
        "已有可卖持仓",
        True,
        {"position_record": holding, "sellable_shares": "unknown"},
        None,
        "PersonalPoolItem仅提供用户管理仓位线索，未提供可卖股数/T+1状态",
    )]
    if not bars:
        return _finish(result, status="风险排除", reason="无历史，不能执行做T判断", evidence=evidence, risks=["缺少历史不计入无匹配。"])
    closes = [bar.close for bar in bars if bar.close is not None]
    down_streak = 0
    for current, previous in zip(reversed(closes), reversed(closes[:-1])):
        if current < previous:
            down_streak += 1
        else:
            break
    recent = closes[-10:]
    peak = max(recent[:-1], default=None)
    decline_pct = ((recent[-1] / peak) - 1) * 100 if peak and recent else None
    down_trigger = down_streak >= 5 or (decline_pct is not None and decline_pct <= -10)
    amounts = [bar.amount for bar in bars[-5:]]
    amount_values = [value for value in amounts if value is not None and value > 0]
    liquid = len(amount_values) == 5 and median(amount_values) >= 10_000_000
    fundamentals = meta.get("fundamental_safe")
    evidence.extend([
        _evidence("T_5V10", "连续5日下跌或近期跌幅超过10%", True, {"down_streak": down_streak, "decline_pct": _round(decline_pct)}, down_trigger, source),
        _evidence("T_LIQUIDITY", "最近5日成交额可执行", True, median(amount_values) if amount_values else None, liquid, source),
        _evidence("T_FUNDAMENTAL", "基本面安全状态（未知不认证安全）", True, fundamentals if fundamentals is not None else "unknown", fundamentals if fundamentals is not None else None, str(meta.get("fundamental_source") or "PIT基本面；未接入即未知")),
    ])
    if not down_trigger:
        return _finish(result, status="NO_MATCH", reason="尚未达到5V10做T触发条件", evidence=evidence)
    if not liquid:
        return _finish(result, status="风险排除", reason="流动性不足，不能认证盘中先卖后买可执行", evidence=evidence, risks=["成交额或成交量字段不完整，未计作无匹配。"])
    if not holding:
        return _finish(result, status="等待确认", reason="满足5V10，但没有PersonalPoolItem仓位线索；等待确认可卖底仓，不产生新买入", signal_date=bars[-1].date, anchor=bars[-1].close, target=bars[-1].close * 0.97, evidence=evidence, risks=["PersonalPoolItem仅表示用户管理的仓位比例，未提供可卖股数；不得假设可卖。", "T+1限制、未知基本面和单边下跌风险；不得加倍补仓。"])
    return _finish(result, status="等待确认", reason="满足5V10且有仓位线索，但可卖股数未接入；不生成新买入", signal_date=bars[-1].date, anchor=bars[-1].close, target=bars[-1].close * 0.97, evidence=evidence, risks=["没有可卖股数/可用底仓接口，不能确认执行。", "T+1限制、未知基本面和单边下跌风险；不得加倍补仓。"])


def _evaluate_75a(bars: list[Bar], result: dict[str, Any]) -> dict[str, Any]:
    source = "StockDailyBar/NumCat批量stk_factor_pro; QFQ OHLCV和因果MA75"
    values = [bar.close for bar in bars]
    if len(bars) < 75:
        return _finish(result, status="风险排除", reason="历史不足75个交易日，不能计算真实MA75和三个月几何", evidence=[
            _evidence("75A_HISTORY", "MA75和3个月几何历史", True, len(bars), False, source),
        ], risks=["历史不足不计入无匹配。"])
    low_index = min(range(len(values)), key=lambda index: values[index])
    left = values[:low_index]
    right = values[low_index:]
    span_days = (date.fromisoformat(bars[-1].date) - date.fromisoformat(bars[0].date)).days
    if len(left) < 20 or len(right) < 20:
        return _finish(result, status="NO_MATCH", reason="最低点两侧缺少足够的左跌和右升结构", evidence=[
            _evidence("75A_GEOMETRY", "最低点两侧各有至少20个交易日", True, {"left": len(left), "right": len(right)}, False, source),
        ])
    left_fall = left[0] > left[-1] * 1.08
    right_rise = right[-1] > right[0] * 1.08
    base = values[max(0, low_index - 15):min(len(values), low_index + 16)]
    base_range = (max(base) - min(base)) / min(base) if base and min(base) > 0 else None
    base_contract = base_range is not None and base_range <= 0.25
    left_vol = [bar.volume for bar in bars[:low_index] if bar.volume is not None and bar.volume > 0]
    base_vol = [bar.volume for bar in bars[max(0, low_index - 15):min(len(bars), low_index + 16)] if bar.volume is not None and bar.volume > 0]
    right_vol = [bar.volume for bar in bars[low_index:] if bar.volume is not None and bar.volume > 0]
    left_declining_volume = bool(left_vol and base_vol and median(base_vol) <= median(left_vol) * 0.9)
    right_expanding_volume = bool(base_vol and right_vol and median(right_vol[-min(15, len(right_vol)):]) >= median(base_vol) * 1.05)
    geometry = span_days >= 90 and left_fall and right_rise and base_contract and left_declining_volume and right_expanding_volume
    ma75 = [_moving_average(values, 75, i) for i in range(len(values))]
    ma75_slope = ma75[-1] - ma75[-11] if ma75[-11] is not None and ma75[-1] is not None else None
    ma75_flat_up = ma75_slope is not None and ma75[-1] >= ma75[-11] * 0.998
    left_neckline = max(left[:min(20, len(left))], default=None)
    early_right = right[1:min(21, len(right))]
    right_neckline = max(early_right, default=None)
    neckline = max(value for value in (left_neckline, right_neckline) if value is not None)
    prior_volume = _mean(bar.volume for bar in bars[-6:-1])
    breakout = bool(
        bars[-1].close > neckline * 1.01
        and bars[-2].close <= neckline * 1.01
        and bars[-1].close > (ma75[-1] or bars[-1].close)
        and prior_volume
        and bars[-1].volume
        and bars[-1].volume >= prior_volume * 1.2
    )
    prior_breakout_index: int | None = None
    for index in range(low_index + 20, max(low_index + 20, len(bars) - 2)):
        prior_average_volume = _mean(bar.volume for bar in bars[max(0, index - 5):index])
        if (
            bars[index].close > neckline * 1.01
            and bars[index - 1].close <= neckline * 1.01
            and bars[index].close > (ma75[index] or bars[index].close)
            and prior_average_volume
            and bars[index].volume
            and bars[index].volume >= prior_average_volume * 1.2
        ):
            prior_breakout_index = index
    support_candidates = [neckline, ma75[-1]]
    retest_support = max(value for value in support_candidates if value is not None)
    last_two = bars[-2:]
    prior5 = [bar.volume for bar in bars[-7:-2] if bar.volume is not None and bar.volume > 0]
    retest = bool(
        prior_breakout_index is not None
        and len(last_two) == 2
        and all(bar.low is not None and retest_support <= bar.low <= retest_support * 1.03 and bar.close >= retest_support for bar in last_two)
        and all(bar.volume is not None for bar in last_two)
        and prior5
        and _mean(bar.volume for bar in last_two) <= _mean(prior5) * 0.85
    )
    evidence = [
        _evidence("75A_SPAN", "跨度超过3个日历月", True, span_days, span_days >= 90, source),
        _evidence("75A_GEOMETRY", "左跌、底部收缩、右升几何代理", True, {"left_fall": left_fall, "base_contract": base_contract, "right_rise": right_rise}, geometry, source),
        _evidence("75A_VOLUME", "左侧缩量、底部收缩、右侧温和放量", True, {"left_declining": left_declining_volume, "right_expanding": right_expanding_volume}, left_declining_volume and right_expanding_volume, source),
        _evidence("75A_MA75", "MA75真实可计算且走平或向上", True, _round(ma75_slope), ma75_flat_up, source),
        _evidence("75A_CONFIRM", "放量突破颈线站上MA75或两日缩量回踩不破", True, {"breakout": breakout, "prior_breakout": prior_breakout_index is not None, "retest": retest, "neckline": _round(neckline)}, breakout or retest, source),
    ]
    if not geometry or not ma75_flat_up:
        return _finish(result, status="NO_MATCH", reason="未形成超过3个月且满足成交量几何的圆弧底，或MA75仍向下", evidence=evidence)
    bottom = min(values)
    target = neckline + (neckline - bottom)
    if breakout or retest:
        reason = "圆弧几何、MA75和右侧确认条件成立；目标为颈线到低点的工程投影"
        return _finish(result, status="已确认", reason=reason, signal_date=bars[-1].date, anchor=retest_support, target=target, evidence=evidence, risks=["目标投影不是30%-50%收益承诺。", "跌破圆弧低点或有效跌破MA75退出。"])
    return _finish(result, status="等待确认", reason="圆弧几何和MA75成立，等待放量突破或两日缩量回踩不破", signal_date=bars[-1].date, anchor=retest_support, target=target, evidence=evidence, risks=["尚无右侧突破/回踩确认，不提前视为买点。"])


def evaluate_classic(strategy_id: str, rows: Iterable[Any], *, meta: dict[str, Any] | None = None) -> dict[str, Any]:
    """Evaluate one stock using one documented classic setup."""
    if strategy_id not in STRATEGIES:
        raise ValueError(f"未知经典战法: {strategy_id}")
    bars = normalize_bars(rows)
    result = _base_result(strategy_id, bars, meta or {})
    if strategy_id == "WM_CLASSIC_520":
        return _evaluate_520(bars, result)
    if strategy_id == "WM_CLASSIC_T":
        return _evaluate_t(bars, result, meta or {})
    return _evaluate_75a(bars, result)


def strategy_card(strategy_id: str) -> dict[str, Any]:
    if strategy_id not in STRATEGIES:
        raise ValueError(f"未知经典战法: {strategy_id}")
    return dict(STRATEGIES[strategy_id])


__all__ = [
    "STRATEGY_IDS",
    "STRATEGIES",
    "Bar",
    "evaluate_classic",
    "indicator_bars",
    "normalize_bars",
    "strategy_card",
]
