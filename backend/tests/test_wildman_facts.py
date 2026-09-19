from datetime import date, timedelta
from types import SimpleNamespace

from wildman.facts import daily_facts, intraday_facts, minute_key


def bars(prices):
    return [SimpleNamespace(trade_date=date(2026, 8, 1) + timedelta(days=i), open_price=p * .99, close_price=p, high_price=p * 1.01, low_price=p * .98, volume=1000, change_pct=1) for i, p in enumerate(prices)]


def test_above_average_is_not_a_new_cross_and_missing_volume_is_not_expansion():
    rows = bars([10 + i / 10 for i in range(30)])
    facts = daily_facts({}, rows, None, {}, {})
    assert facts["ma5_cross_ma20"] is False
    assert facts["cross_volume_expand"] is False
    rows[-1].volume = None
    facts = daily_facts({}, rows, None, {}, {})
    assert facts["cross_volume_expand"] is None


def test_today_board_breaks_cannot_prove_yesterdays_divergence():
    rows = bars([10] * 25)
    facts = daily_facts({"continuous_days": 4, "failed_attempts": 3}, rows, None, {}, {})
    assert facts["yesterday_divergence"] is None
    assert facts["first_volume_divergence"] is None
    assert facts["recent_solid_limit"] is None
    assert facts["holds_limit_candle_half"] is None


def test_previous_limit_is_checked_against_its_own_candle():
    rows = bars([10] * 25)
    rows[-2].open_price, rows[-2].close_price = 9, 10
    rows[-1].low_price = 9.3
    facts = daily_facts({"previous_limit": {"trade_date": rows[-2].trade_date.isoformat(), "failed_attempts": 0, "first_limit_time": "09:40"}}, rows, None, {}, {})
    assert facts["limit_candle_half"] == 9.5
    assert facts["holds_limit_candle_half"] is False
    assert facts["anchor_broken"] is True
    assert facts["recent_solid_limit"] is True


def test_old_auction_and_future_or_stale_daily_data_do_not_confirm_signals():
    rows = bars([10] * 30)
    target = rows[-2].trade_date
    auction = SimpleNamespace(trade_date=rows[-1].trade_date, high_open_pct=4, auction_volume_ratio=3)
    facts = daily_facts({}, rows, auction, {}, {"trade_date": target.isoformat()})
    assert facts["fact_basis"]["history_rows"] == 29
    assert facts["auction_pct"] is None
    stale = daily_facts({}, rows[:10], auction, {}, {"trade_date": target.isoformat()})
    assert stale["close_price"] is None
    assert stale["ma5_cross_ma20"] is None


def test_auction_high_open_is_not_opening_volume_attack():
    facts = daily_facts({}, bars([10] * 30), SimpleNamespace(high_open_pct=4, auction_volume_ratio=3), {}, {})
    assert facts["auction_volume_strength"] is True
    assert facts["open_volume_attack"] is None


def test_short_history_is_neither_multitimeframe_bottom_nor_arc():
    facts = daily_facts({}, bars([10] * 90), None, {}, {})
    assert facts["weekly_monthly_bottom"] is None
    assert facts["arc_days"] is None


def test_decline_must_be_consecutive_not_any_five_red_days():
    facts = daily_facts({}, bars([10, 9, 8, 9, 8, 7]), None, {}, {})
    assert facts["decline_days"] == 2


def test_falling_three_points_and_flat_prices_are_not_two_supporting_pullbacks():
    assert intraday_facts([{"close_price": p} for p in [10, 9, 8]]) == {}
    assert intraday_facts([{"close_price": 10}] * 10)["two_pullbacks_hold"] is None
    falling = intraday_facts([{"close_price": p} for p in [10, 9.9, 9.8, 9.7, 9.6]])
    assert falling["two_pullbacks_hold"] is None
    rising = intraday_facts([{"close_price": p} for p in [10, 9.9, 10, 9.95, 10.1]])
    assert rising["two_pullbacks_hold"] is True
    assert rising["lows_rising"] is True


def test_minute_key_normalizes_clock_and_iso_datetime_inputs():
    values = {
        "0925": "0925",
        "925": "0925",
        "9:25": "0925",
        "09:25:41": "0925",
        "2026-09-20T09:25:41+08:00": "0925",
        "2026-09-20 09:25:41": "0925",
        None: None,
        "25:00": None,
    }
    for raw, expected in values.items():
        assert minute_key(raw) == expected


def test_leadership_is_explicit_and_same_day_seal_order_is_not_causal():
    rows = bars([10] * 30)
    theme = {"max_limit_height": 5, "limit_up_count": 3, "leader_first_limit_time": "09:30"}
    assert daily_facts({"continuous_days": 5, "first_limit_time": "09:25"}, rows, None, theme, {})["independent_theme_leadership"] is None
    assert daily_facts({"continuous_days": 5, "first_limit_time": "09:25", "independent_theme_leadership": True}, rows, None, theme, {})["independent_theme_leadership"] is True
    assert daily_facts({"continuous_days": 5, "first_limit_time": "09:25", "independent_theme_leadership": False}, rows, None, theme, {})["independent_theme_leadership"] is False


def test_early_theme_start_uses_start_date_and_rejects_after_leader_start():
    rows = bars([10] * 30)
    theme = {"max_limit_height": 5, "earliest_start_date": "2026-08-26", "leader_established_date": "2026-08-28"}
    early = daily_facts({"continuous_days": 5}, rows, None, theme, {})
    assert early["stock_start_date"] == "2026-08-26"
    assert early["early_theme_start"] is True

    after = daily_facts({"continuous_days": 3}, rows, None, {**theme, "leader_established_date": "2026-08-27"}, {})
    assert after["stock_start_date"] == "2026-08-28"
    assert after["early_theme_start"] is False


def test_board_count_does_not_become_sector_influence_or_benchmark():
    facts = daily_facts({"continuous_days": 5}, bars([10] * 30), None, {"max_limit_height": 5, "limit_up_count": 10}, {})
    assert facts["theme_linkage"] is None
    assert facts["emotion_benchmark"] is None
