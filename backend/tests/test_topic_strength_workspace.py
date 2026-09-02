from datetime import date
from unittest.mock import AsyncMock, PropertyMock, patch

import pytest

from services.topic_strength_workspace import (
    PERIOD_SESSIONS,
    TopicStrengthWorkspaceService,
)


def test_period_rankings_passes_the_documented_trading_session_window():
    service = TopicStrengthWorkspaceService()
    service._flow_history = AsyncMock(return_value=([], "database_fund_flow"))
    service._board_names = AsyncMock(return_value={})

    async def check():
        for period, sessions in PERIOD_SESSIONS.items():
            await service._period_rankings(period, "all", 30, date(2026, 8, 28))
            assert service._flow_history.await_args.args == ("all", date(2026, 8, 28), sessions)
            service._flow_history.reset_mock()

    import asyncio

    asyncio.run(check())


def test_rankings_compute_compounded_return_flow_continuity_and_real_coverage():
    rows = [
        {"board_type": "industry", "code": "BK001", "trade_date": f"2026-08-2{index}", "close": 100 + index, "change_pct": 1, "main_net_inflow": 10 if index != 5 else None, "up_count": 8, "down_count": 2, "source": "test"}
        for index in (4, 5, 6)
    ]
    result = TopicStrengthWorkspaceService._build_rankings(rows, 10, expected_sessions=5)

    assert len(result) == 1
    row = result[0]
    assert row["period_return_pct"] == 1.92
    assert row["main_net_inflow"] == 20
    assert row["flow_sessions"] == 2
    assert row["coverage"] == 60.0
    assert row["positive_flow_ratio"] == 100.0
    assert row["breadth_pct"] == 80.0


def test_missing_values_are_not_converted_to_zero_and_ties_have_stable_order():
    rows = [
        {"board_type": "concept", "code": code, "trade_date": "2026-08-28", "close": None, "change_pct": None, "main_net_inflow": None, "up_count": None, "down_count": None}
        for code in ("C002", "C001")
    ]
    result = TopicStrengthWorkspaceService._build_rankings(rows, 10, expected_sessions=5)

    assert [row["code"] for row in result] == ["C001", "C002"]
    assert result[0]["period_return_pct"] is None
    assert result[0]["main_net_inflow"] is None
    assert result[0]["breadth_pct"] is None
    assert result[0]["strength_score"] is None


def test_provider_theme_mapping_keeps_zero_values():
    row = TopicStrengthWorkspaceService._normalise_provider_theme({
        "theme_symbol": "801841k",
        "theme_name": "测试题材",
        "pct_chg": 0,
        "main_net_amount": 0,
        "strength": 0,
        "tradedate": "20260828",
    })

    assert row == {
        "code": "801841k",
        "name": "测试题材",
        "change_pct": 0.0,
        "strength": 0.0,
        "main_net_inflow": 0.0,
        "trade_date": "2026-08-28",
        "source": "numcat",
    }


def test_selected_board_members_and_tags_are_joined_without_losing_theme_code():
    themes = [{"theme_symbol": "801841k", "theme_name": "智能制造", "strength": 72, "pct_chg": 2.1}]
    members = [{"theme_symbol": "801841k", "symbols": ["600001", "000001"]}]

    result = TopicStrengthWorkspaceService._selected_boards(themes, members)

    assert result[0]["code"] == "801841k"
    assert result[0]["member_codes"] == ["600001", "000001"]
    assert result[0]["member_count"] == 2


def test_selected_provider_rankings_use_compounded_daily_returns():
    result = TopicStrengthWorkspaceService._provider_rankings(
        [
            {"theme_symbol": "801841k", "theme_name": "智能制造", "tradedate": "20260827", "pct_chg": 1},
            {"theme_symbol": "801841k", "theme_name": "智能制造", "tradedate": "20260828", "pct_chg": 2},
        ],
        [{"theme_symbol": "801841k", "theme_name": "智能制造", "trademin": "1455", "main_net_amount": 0}],
        10,
    )

    assert result[0]["period_return_pct"] == 3.02
    assert result[0]["main_net_inflow"] == 0.0
    assert result[0]["primary_factors"]


def test_failed_section_uses_previous_successful_rows_and_marks_cache():
    section, used_cache = TopicStrengthWorkspaceService._section_or_cache(
        [],
        source="numcat_hotstock",
        data_date="2026-08-28",
        realtime=True,
        error="TimeoutError: upstream unavailable",
        previous={
            "available": True,
            "rows": [{"code": "600001"}],
            "count": 1,
            "source": "numcat_hotstock",
            "data_date": "2026-08-27",
            "is_realtime": True,
            "cache_hit": False,
        },
    )

    assert used_cache is True
    assert section["rows"] == [{"code": "600001"}]
    assert section["is_realtime"] is False
    assert section["cache_hit"] is True
    assert "TimeoutError" in section["error"]


def test_market_stats_can_keep_style_when_statistics_endpoint_fails():
    section, used_cache = TopicStrengthWorkspaceService._market_stats_or_cache(
        [{"symbol": "fg", "trade_date": "2026-08-28"}],
        [],
        style_error=None,
        stat_error="TimeoutError: statistics unavailable",
        latest="2026-08-28",
        previous={
            "style": [],
            "statistics": [{"symbol": "tj", "trade_date": "2026-08-27"}],
            "source": "numcat_theme_stat_daily",
        },
        realtime=True,
    )

    assert used_cache is True
    assert section["style"] == [{"symbol": "fg", "trade_date": "2026-08-28"}]
    assert section["statistics"] == [{"symbol": "tj", "trade_date": "2026-08-27"}]
    assert section["is_realtime"] is True
    assert section["cache_hit"] is True
    assert "statistics unavailable" in section["error"]


@pytest.mark.asyncio
async def test_workspace_keeps_other_sections_when_one_provider_call_fails():
    service = TopicStrengthWorkspaceService()
    cached = {
        "updated_at": "2026-08-28T15:00:00+08:00",
        "sections": {
            "hot_search": {
                "available": True,
                "rows": [{"code": "600001"}],
                "count": 1,
                "source": "numcat_hotstock",
                "data_date": "2026-08-28",
                "is_realtime": True,
                "cache_hit": False,
            }
        },
        "rankings": [],
        "quality": {},
    }
    service._read_cache = AsyncMock(return_value=cached)
    service._write_cache = AsyncMock()
    service._latest_db_date = AsyncMock(return_value=date(2026, 8, 28))

    async def provider_rows(**kwargs):
        return [{"symbol": "600001", "s": "600001", "n": "甲公司", "pc": 1.2}]

    with patch("services.topic_strength_workspace.is_a_share_market_session", return_value=True), \
         patch.object(type(__import__("market_data.numcat.market_provider", fromlist=["numcat_market_provider"]).numcat_market_provider), "configured", new_callable=PropertyMock, return_value=True), \
         patch("services.topic_strength_workspace.numcat_market_provider.hot_stock", new=AsyncMock(side_effect=TimeoutError("hot search unavailable"))), \
         patch("services.topic_strength_workspace.numcat_market_provider.theme_daily", new=AsyncMock(return_value=[])), \
         patch("services.topic_strength_workspace.numcat_market_provider.theme_fund_flow", new=AsyncMock(return_value=[])), \
         patch("services.topic_strength_workspace.numcat_market_provider.theme_members", new=AsyncMock(return_value=[])), \
         patch("services.topic_strength_workspace.numcat_market_provider.theme_auction", new=AsyncMock(return_value=[])), \
         patch("services.topic_strength_workspace.numcat_market_provider.strongest_fengkou", new=AsyncMock(return_value=[])), \
         patch("services.topic_strength_workspace.numcat_market_provider.theme_library", new=AsyncMock(return_value=[])), \
         patch("services.topic_strength_workspace.numcat_market_provider.theme_reason", new=AsyncMock(return_value=[])), \
         patch("services.topic_strength_workspace.numcat_market_provider.theme_style_daily", new=AsyncMock(return_value=[])), \
         patch("services.topic_strength_workspace.numcat_market_provider.theme_stat_daily", new=AsyncMock(return_value=[])):
        result = await service.get(period="week", board_type="all", refresh=True)

    assert result["sections"]["hot_search"]["rows"] == [{"code": "600001"}]
    assert result["sections"]["hot_search"]["cache_hit"] is True
    assert result["sections"]["hot_search"]["is_realtime"] is False
    assert result["partial_cache_hit"] is True
    assert result["sections"]["auction"]["available"] is False
