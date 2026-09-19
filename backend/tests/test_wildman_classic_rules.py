from datetime import date, timedelta

from wildman.classic import evaluate_classic, indicator_bars


def bars(closes, volumes=None, amounts=None):
    volumes = volumes or [1000] * len(closes)
    amounts = amounts or [20_000_000] * len(closes)
    start = date(2026, 1, 1)
    return [
        {
            "date": (start + timedelta(days=index)).isoformat(),
            "open": close * 0.99,
            "high": close * 1.02,
            "low": close * 0.98,
            "close": close,
            "volume": volumes[index],
            "amount": amounts[index],
        }
        for index, close in enumerate(closes)
    ]


def test_520_waits_for_next_day_confirmation_and_uses_ma20_anchor():
    closes = [10] * 24 + [11, 13]
    volumes = [1000] * len(closes)
    volumes[-2] = 2000
    waiting = evaluate_classic("WM_CLASSIC_520", bars(closes[:-1], volumes[:-1]), meta={"symbol": "000001"})
    assert waiting["status"] == "等待确认"
    assert waiting["anchor_price"] != closes[-2]
    assert waiting["target_price"] == round(closes[-2] * 1.02, 4)

    confirmed = evaluate_classic("WM_CLASSIC_520", bars(closes, volumes), meta={"symbol": "000001"})
    assert confirmed["status"] == "已确认"
    assert confirmed["signal_date"] == "2026-01-26"

    rejected = evaluate_classic("WM_CLASSIC_520", bars([*closes[:-1], 10], volumes), meta={"symbol": "000001"})
    assert rejected["status"] == "NO_MATCH"


def test_520_later_death_cross_invalidates_signal():
    closes = [10] * 24 + [11, 13, 8, 8, 8, 8]
    volumes = [1000] * len(closes)
    volumes[-6] = 2000
    result = evaluate_classic("WM_CLASSIC_520", bars(closes, volumes), meta={"symbol": "000001"})
    assert result["status"] == "风险排除"
    assert any(item["rule_id"] == "520_DEATH_CROSS" for item in result["evidence"])


def test_t_checks_5v10_before_unheld_status_and_unknown_fundamentals_are_not_safe():
    no_trigger = evaluate_classic("WM_CLASSIC_T", bars([10] * 11), meta={"symbol": "000001", "holding": False})
    assert no_trigger["status"] == "NO_MATCH"
    assert "5V10" in no_trigger["reason"]

    amounts = [None, None, 20_000_000, 20_000_000, 20_000_000] + [20_000_000] * 6
    result = evaluate_classic("WM_CLASSIC_T", bars(list(range(20, 10, -1)) + [10], amounts=amounts), meta={"symbol": "000001", "holding": False})
    assert result["status"] == "等待确认"
    assert "不产生新买入" in result["reason"]
    liquidity = next(item for item in result["evidence"] if item["rule_id"] == "T_LIQUIDITY")
    assert liquidity["actual"] is not None
    fundamental = next(item for item in result["evidence"] if item["rule_id"] == "T_FUNDAMENTAL")
    assert fundamental["passed"] is None


def test_75a_requires_geometry_not_history_length_alone():
    result = evaluate_classic("WM_CLASSIC_75A", bars([10] * 120), meta={"symbol": "000001"})
    assert result["status"] == "NO_MATCH"
    assert any(item["rule_id"] == "75A_GEOMETRY" for item in result["evidence"])


def test_indicators_are_causal():
    output = indicator_bars(bars(list(range(1, 81))))
    assert output[0]["ma5"] is None
    assert output[4]["ma5"] == 3
    assert output[74]["ma75"] == 38


def test_75a_breakout_and_retest_require_a_real_prior_cross():
    closes = [12 - i * .08 for i in range(50)] + [8] * 20 + [8 + i * .1 for i in range(1, 40)] + [12.4]
    volumes = [1500] * 35 + [300] * 35 + [1200] * 39 + [2400]
    history = bars(closes, volumes)
    breakout = evaluate_classic("WM_CLASSIC_75A", history)
    assert breakout["status"] == "已确认"
    assert next(row for row in breakout["evidence"] if row["rule_id"] == "75A_CONFIRM")["actual"]["breakout"] is True
    retest_bars = bars(closes + [12.2, 12.2], volumes + [600, 600])
    for row in retest_bars[-2:]:
        row["low"] = 12.0
    retest = evaluate_classic("WM_CLASSIC_75A", retest_bars)
    assert retest["status"] == "已确认"
    assert next(row for row in retest["evidence"] if row["rule_id"] == "75A_CONFIRM")["actual"]["retest"] is True
    retest_bars[-3].update(close=12.0, volume=1200)
    assert evaluate_classic("WM_CLASSIC_75A", retest_bars)["status"] == "等待确认"
