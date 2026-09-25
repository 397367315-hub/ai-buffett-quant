import pytest
import httpx
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from unittest.mock import patch

from api.decision_authority_routes import get_decision_authority
from database import Base
from main import app
from models import MarketDataCache
from services.market_decision_contract import WORKBENCH_CACHE_PREFIX
from services.wildman_service import CACHE_KEY as WILDMAN_CACHE_KEY
from services.wildman_decision_authority import build_authority


def wildman(cycle="发酵/主升", position="20%~40%", trade_date="2026-09-25"):
    return {
        "trade_date": trade_date,
        "rule_version": "WM_RULE_CORE_V1_3",
        "updated_at": "2026-09-25T15:01:00+08:00",
        "source_status": {"data_date": "2026-09-25"},
        "cycle": {"cycle": cycle, "cycle_node": "非关键节点", "position_range": position},
        "mainlines": [],
    }


def v4(action="execute", truth="PASS", cap=40, trade_date="2026-09-25"):
    return {
        "available": True,
        "meta": {"contract_version": "market-workbench-v4.0.0", "decision_date": trade_date, "updated_at": "2026-09-25T14:55:00+08:00"},
        "market_way_v4": {"truth": {"status": truth}},
        "market_cognition": {"final_action": action},
        "decision_2026": {"trading_permission": {"max_total_position_pct": cap}},
    }


def test_limited_v4_caps_main_rise_to_25():
    result = build_authority(wildman(), v4(truth="LIMITED", cap=40), decision_date="2026-09-25")
    assert result["effective"]["status"] == "caution"
    assert result["effective"]["max_position_pct"] == 25
    assert result["primary"]["updated_at"] == "2026-09-25T15:01:00+08:00"
    assert result["primary"]["source_data_date"] == "2026-09-25"
    assert result["secondary"]["updated_at"] == "2026-09-25T14:55:00+08:00"


def test_retreat_cannot_be_upgraded_by_v4():
    result = build_authority(wildman("退潮", "0"), v4("execute", cap=40), decision_date="2026-09-25")
    assert result["effective"]["status"] == "observe"
    assert result["effective"]["max_position_pct"] == 0


def test_positive_wildman_and_no_trade_preserve_disagreement():
    result = build_authority(wildman(), v4("no_trade", cap=0), decision_date="2026-09-25")
    assert result["effective"]["status"] == "caution"
    assert result["conflicts"][0]["scope"] == "market_action"
    assert result["secondary"]["status"] == "disagreement"


def test_wrong_day_is_unknown_without_fake_cap():
    result = build_authority(wildman(), v4(trade_date="2026-09-24"), decision_date="2026-09-25")
    assert result["effective"]["status"] == "unknown"
    assert result["effective"]["max_position_pct"] is None
    assert result["data_quality"]["comparable"] is False
    assert result["secondary"]["status"] == "stale"


def test_missing_snapshot_is_unknown():
    result = build_authority(wildman(), None, decision_date="2026-09-25")
    assert result["effective"]["status"] == "unknown"
    assert result["secondary"]["status"] == "unavailable"


def test_fail_truth_is_not_research_authorization():
    result = build_authority(wildman(), v4("execute", truth="FAIL", cap=0), decision_date="2026-09-25")
    assert result["effective"]["status"] == "caution"
    assert any("阻断" in reason for reason in result["effective"]["reasons"])


def test_unknown_primary_cycle_cannot_inherit_v4_cap():
    result = build_authority(wildman("待确认", None), v4("execute", cap=40), decision_date="2026-09-25")
    assert result["effective"]["status"] == "unknown"
    assert result["effective"]["max_position_pct"] is None


def test_observe_is_a_disagreement_against_positive_wildman():
    result = build_authority(wildman(), v4("observe", cap=40), decision_date="2026-09-25")
    assert result["secondary"]["status"] == "disagreement"
    assert result["effective"]["status"] == "caution"


def test_v4_truth_missing_is_unknown_and_has_no_cap():
    snapshot = v4("execute", cap=40)
    del snapshot["market_way_v4"]["truth"]["status"]
    result = build_authority(wildman(), snapshot, decision_date="2026-09-25")
    assert result["secondary"]["status"] == "unavailable"
    assert result["effective"]["status"] == "unknown"
    assert result["effective"]["max_position_pct"] is None


def test_fail_truth_forces_zero_even_when_old_cache_says_forty():
    result = build_authority(wildman(), v4("execute", truth="FAIL", cap=40), decision_date="2026-09-25")
    assert result["secondary"]["max_position_pct"] == 0
    assert result["effective"]["max_position_pct"] == 0


def test_non_finite_v4_cap_is_not_used():
    result = build_authority(wildman("启动/试错", "5%~10%"), v4("execute", cap="nan"), decision_date="2026-09-25")
    assert result["effective"]["max_position_pct"] == 10
    assert any("主周期收紧" in reason for reason in result["effective"]["reasons"])


def test_retreat_and_execute_is_a_secondary_disagreement():
    result = build_authority(wildman("退潮", "0"), v4("execute", cap=40), decision_date="2026-09-25")
    assert result["secondary"]["status"] == "disagreement"


def test_rule_version_mismatch_is_not_comparable():
    snapshot = wildman()
    snapshot["rule_version"] = "old"
    result = build_authority(snapshot, v4(), decision_date="2026-09-25")
    assert result["data_quality"]["comparable"] is False
    assert result["effective"]["status"] == "unknown"


def test_nonstandard_primary_cycle_or_range_cannot_borrow_v4_cap():
    for cycle, position in (("非标准周期", "20%~40%"), ("启动/试错", None), ("启动/试错", "未知")):
        result = build_authority(wildman(cycle, position), v4(cap=40), decision_date="2026-09-25")
        assert result["effective"]["status"] == "unknown"
        assert result["effective"]["max_position_pct"] is None


@pytest.mark.asyncio
async def test_invalid_date_is_rejected_by_read_only_route():
    with pytest.raises(Exception) as caught:
        await get_decision_authority("not-a-date")
    assert getattr(caught.value, "status_code", None) == 422


@pytest.mark.asyncio
async def test_read_only_route_uses_isolated_cache_and_returns_unknown_then_valid():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all, tables=[MarketDataCache.__table__])
    try:
        with patch("services.wildman_decision_authority.async_session", factory):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                missing = await client.get("/api/v1/decision-authority", params={"date": "2026-09-25"})
                assert missing.status_code == 200
                assert missing.json()["data"]["effective"]["status"] == "unknown"

                wildman_payload = wildman()
                v4_payload = v4(truth="PASS", cap=40)
                async with factory() as session:
                    session.add(MarketDataCache(key=f"{WILDMAN_CACHE_KEY}:s1:g1", payload=wildman_payload))
                    session.add(MarketDataCache(key=f"{WORKBENCH_CACHE_PREFIX}2026-09-25", payload=v4_payload))
                    await session.commit()
                valid = await client.get("/api/v1/decision-authority", params={"date": "2026-09-25"})
                assert valid.status_code == 200
                body = valid.json()["data"]
                assert body["effective"]["max_position_pct"] == 40
                assert body["data_quality"]["comparable"] is True
    finally:
        await engine.dispose()
