from datetime import date, timedelta

from quant.trading_skill_features import build_skill_features
from quant.trading_skills import evaluate_all_skills, evaluate_skill, stage_label
from services.trading_skill_service import TradingSkillService


def _bars(count: int = 140, *, shock_at: int | None = None) -> list[dict]:
    rows = []
    price = 10.0
    for index in range(count):
        previous = price
        price *= 1.003 if index % 9 else 0.992
        amount = 1_000_000 + index * 2_000
        if shock_at == index:
            amount *= 4
            price *= 1.02
        rows.append({
            "trade_date": date(2025, 1, 1) + timedelta(days=index),
            "open_price": previous,
            "close_price": price,
            "high_price": max(previous, price) * 1.01,
            "low_price": min(previous, price) * 0.99,
            "volume": 100_000 + index * 100,
            "amount": amount,
            "turnover": 2.0 + (index % 4) * 0.1,
        })
    return rows


def test_features_are_point_in_time():
    rows = _bars()
    features = build_skill_features(rows, as_of=date(2025, 3, 1), context={"sector_return_1d": 0.001})
    assert features["data_date"] == "2025-03-01"
    assert features["history_sessions"] == 60
    expected = next(item["close_price"] for item in rows if item["trade_date"] == date(2025, 3, 1))
    assert features["close"] == expected


def test_all_skills_share_non_order_output_contract():
    features = build_skill_features(
        _bars(shock_at=100),
        context={"sector_return_1d": 0.001, "market_return_1d": 0.0, "sector_state": "顺势", "sector_strength": 65, "sector_breadth": 60},
    )
    results = evaluate_all_skills(features)
    assert len(results) == 10
    for result in results:
        assert {"skill_id", "stage", "confidence_pct", "evidence", "invalidation_conditions", "direct_order"} <= set(result)
        assert result["direct_order"] is False
        assert "buy" not in str(result).lower() or "buy" in str(result.get("language_boundary", "")).lower()


def test_auction_skill_does_not_invent_missing_history():
    features = build_skill_features(_bars())
    result = evaluate_skill("skill_09_auction_intraday_confirm", features)
    assert result["stage"] == "WAIT"
    assert "verified_09_25_auction_observation" in result["missing_factors"]
    assert result["signal_type"] == "INSUFFICIENT_DATA"


def test_abnormal_turnover_tracks_shock_without_future_values():
    features = build_skill_features(_bars(shock_at=139))
    result = evaluate_skill("skill_03_abnormal_turnover", features)
    assert result["stage"] in {"VOLUME_SHOCK", "NOISE", "NO_RECENT_EVENT"}
    assert result["stage_label"] in {"成交异常触发", "异常成交未形成明确结构", "暂无近期异常成交"}
    assert all("future" not in str(item).lower() for item in result["evidence"])


def test_internal_stage_codes_have_user_facing_labels():
    assert stage_label("VOLUME_SHOCK") == "成交异常触发"
    assert stage_label("EFFICIENT_UP") == "量价效率改善"


def test_board_scope_can_exclude_star_and_gem():
    stocks = [
        {"code": "688001", "name": "科创样本", "change_pct": 3},
        {"code": "300001", "name": "创业样本", "change_pct": 2},
        {"code": "302132", "name": "新创业样本", "change_pct": 2},
        {"code": "600001", "name": "主板样本", "change_pct": 1},
    ]
    filtered, counts = TradingSkillService._rank_snapshot(stocks)
    assert [item["code"] for item in filtered] == ["600001"]
    assert counts["科创板"] == 1
    assert counts["创业板"] == 2

    included, counts = TradingSkillService._rank_snapshot(
        stocks, exclude_star_market=False, exclude_gem=False
    )
    assert {item["code"] for item in included} == {"688001", "300001", "302132", "600001"}
    assert counts["科创板"] == 0
    assert counts["创业板"] == 0
