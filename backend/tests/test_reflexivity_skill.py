from datetime import date, timedelta

from quant.reflexivity_skill import build_reflexivity_diagnosis
from quant.trading_skill_features import build_skill_features
from quant.trading_skills import evaluate_skill


def _bars(count: int = 160, *, shock: bool = False) -> list[dict]:
    rows = []
    price = 10.0
    for index in range(count):
        previous = price
        if shock and index == count - 3:
            price *= 0.90
        elif shock and index == count - 2:
            price *= 1.01
        else:
            price *= 1.002 if index % 11 else 0.997
        amount = 1_000_000 + index * 2_000
        if shock and index == count - 3:
            amount *= 3
        rows.append({
            "trade_date": date(2025, 1, 1) + timedelta(days=index),
            "open_price": previous,
            "close_price": price,
            "high_price": max(previous, price) * 1.01,
            "low_price": min(previous, price) * 0.99,
            "volume": 100_000 + index * 100,
            "amount": amount,
            "turnover": 2.0 + (index % 5) * 0.2,
        })
    return rows


def test_reflexivity_is_point_in_time_and_has_six_dimensions():
    rows = _bars()
    cutoff = date(2025, 4, 15)
    result = build_reflexivity_diagnosis(
        rows,
        as_of=cutoff,
        context={
            "sector_return_1d": 0.002,
            "market_return_1d": 0.001,
            "sector_strength": 65,
            "sector_breadth": 62,
            "stock_alpha_score": 64,
            "alpha_density": 60,
            "sector_state": "启势",
        },
        symbol="600001",
        name="测试股票",
    )
    assert result["data_date"] == cutoff.isoformat()
    assert result["audit"]["no_future_data"] is True
    assert set(result) >= {
        "forced_trading", "liquidity_map", "capital_price_efficiency",
        "absorption_pressure", "psychology", "reflexivity", "selection_score",
    }
    assert result["forced_trading"]["short_cover"]["status"] == "disabled"
    assert result["data_quality"]["l2_available"] is False
    assert "庄家" not in str(result)
    assert "主力准备" not in str(result)


def test_reflexivity_preserves_pressure_dynamics_and_skill_contract():
    result = build_reflexivity_diagnosis(
        _bars(shock=True),
        context={"sector_return_1d": -0.01, "sector_strength": 38, "sector_breadth": 35, "sector_state": "分歧"},
        symbol="000001",
    )
    pressure = result["absorption_pressure"]
    assert {"absorption_delta", "pressure_delta", "absorption_trend", "pressure_trend"} <= set(pressure)
    skill = result["skill_result"]
    assert skill["skill_id"] == "skill_10_behavior_reflexivity"
    assert skill["direct_order"] is False
    assert isinstance(skill["missing_factors"], list)


def test_reflexivity_short_history_is_explicitly_unavailable_not_an_exception():
    result = build_reflexivity_diagnosis(_bars(3), symbol="920059", name="新上市标的")
    assert result["available"] is False
    assert result["data_quality"]["history_sessions"] == 3
    assert result["candidate_type"] == "NO_CLEAR_CANDIDATE"


def test_legacy_feature_vector_can_validate_skill_ten_without_fabricating_l2():
    features = build_skill_features(_bars(), context={"sector_return_1d": 0.001})
    result = evaluate_skill("skill_10_behavior_reflexivity", features)
    assert result["skill_id"] == "skill_10_behavior_reflexivity"
    assert result["direct_order"] is False
    assert result["data_level"] == "DAILY"
    assert "full_liquidity_map" in result["missing_factors"]
