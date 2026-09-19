from datetime import date, timedelta
from types import SimpleNamespace

import pytest

from services.wildman_history_service import WildmanHistoryService
from wildman.history import (
    MAX_HISTORY_TRADING_DAYS,
    bounded_trade_dates,
    derive_historical_overrides,
    normalize_theme_member_history,
)


def _days(count=6):
    start = date(2026, 8, 3)
    return [start + timedelta(days=index) for index in range(count)]


def _pool(days, code="000001", heights=None, **extra):
    heights = heights or [1] * len(days)
    return [
        {"code": code, "trade_date": day.isoformat(), "continuous_days": height, **extra}
        for day, height in zip(days, heights)
    ]


def _bars(days, code="000001", closes=None):
    closes = closes or [10 + index for index in range(len(days))]
    return {
        code: [
            SimpleNamespace(
                stock_code=code,
                trade_date=day,
                open_price=close - 0.2,
                close_price=close,
                high_price=close + 0.2,
                low_price=close - 0.3,
                volume=1000,
            )
            for day, close in zip(days, closes)
        ]
    }


def test_bounded_dates_never_invents_weekday_sessions():
    target = date(2026, 8, 28)
    values = [target - timedelta(days=index) for index in range(30) if index % 2 == 0]
    result = bounded_trade_dates(target, values)
    assert len(result) <= MAX_HISTORY_TRADING_DAYS
    assert result == sorted(set(values + [target]))[-MAX_HISTORY_TRADING_DAYS:]


def test_theme_members_are_dated_and_request_scope_is_disclosed():
    rows = normalize_theme_member_history(
        [{"theme_symbol": "T1", "symbols": ["1", "000002"]}],
        requested_date="20260820",
    )
    assert rows[0]["trade_date"] == "2026-08-20"
    assert rows[0]["date_quality"] == "request_scoped_date"
    assert rows[0]["symbols"] == ["000001", "000002"]


def test_history_computes_dated_leader_and_never_causal_independence():
    days = _days(6)
    up = _pool(days[:5], heights=[1, 2, 3, 4, 5])
    up += _pool(days[2:5], code="000002", heights=[1, 2, 3])
    members = [
        {"theme_symbol": "T1", "symbols": ["000001", "000002"], "trade_date": day.isoformat()}
        for day in days
    ]
    result = derive_historical_overrides(
        days[-1],
        [{"code": "000001", "theme_symbol": "T1", "continuous_days": 5}],
        up,
        members,
        bars=_bars(days, closes=[10, 10.5, 11, 11.5, 12, 12.5]),
    )
    facts = result["stock_overrides"]["000001"]
    assert facts["primary_theme_name"] == "T1"
    assert facts["leader_height"] == 5
    assert facts["leader_established_date"] == days[3].isoformat()
    assert facts["stock_start_date"] == days[0].isoformat()
    assert facts["early_theme_start"] is True
    assert facts["theme_linkage"] is True
    assert facts["independent_theme_leadership"] is None
    assert "causal_boundary" in facts["fact_basis"]


def test_same_day_seal_order_does_not_create_leadership_fact():
    days = _days(3)
    result = derive_historical_overrides(
        days[-1],
        [{"code": "000001", "theme_symbol": "T1", "first_limit_time": "09:25"}],
        _pool(days, heights=[1, 2, 3]),
        [
            {"theme_symbol": "T1", "symbols": ["000001", "000002"], "trade_date": day.isoformat()}
            for day in days
        ],
    )
    assert result["stock_overrides"]["000001"]["independent_theme_leadership"] is None


def test_old_dragon_requires_separated_dated_reappearance():
    days = _days(10)
    event_days = [days[0], days[1], days[2], days[3], days[8], days[9]]
    up = _pool(event_days[:4], heights=[1, 2, 3, 4]) + _pool(event_days[4:], heights=[1, 2])
    members = [
        {"theme_symbol": "T1", "symbols": ["000001"], "trade_date": day.isoformat()}
        for day in days
    ]
    result = derive_historical_overrides(
        days[-1], [{"code": "000001", "theme_symbol": "T1"}], up, members
    )
    facts = result["stock_overrides"]["000001"]
    assert facts["old_dragon"] is True
    assert facts["cross_cycle"] is None


def test_continuous_eight_board_run_is_not_an_old_dragon():
    days = _days(8)
    up = _pool(days, heights=list(range(1, 9)))
    members = [
        {"theme_symbol": "T1", "symbols": ["000001"], "trade_date": day.isoformat()}
        for day in days
    ]
    result = derive_historical_overrides(
        days[-1], [{"code": "000001", "theme_symbol": "T1"}], up, members
    )
    assert result["stock_overrides"]["000001"]["old_dragon"] is None


def test_leadership_proxy_requires_three_lead_days_early_start_and_two_followers():
    days = _days(6)
    up = _pool(days, heights=[1, 2, 3, 4, 5, 6])
    up += _pool(days[3:], code="000002", heights=[1, 2, 3])
    up += _pool(days[4:], code="000003", heights=[1, 2])
    members = [
        {
            "theme_symbol": "T1",
            "symbols": ["000001", "000002", "000003"],
            "trade_date": day.isoformat(),
        }
        for day in days
    ]
    result = derive_historical_overrides(
        days[-1],
        [{"code": "000001", "theme_symbol": "T1"}],
        up,
        members,
    )
    facts = result["stock_overrides"]["000001"]
    assert facts["leadership_proxy"] is True
    assert facts["leadership_proxy_evidence"]["lead_day_count"] >= 3
    assert facts["independent_theme_leadership"] is None


def test_first_divergence_support_requires_the_divergence_day_bar():
    days = _days(4)
    up = _pool(days[:3], heights=[1, 2, 3])
    failed = [{"code": "000001", "trade_date": days[3].isoformat(), "continuous_days": 3}]
    members = [
        {"theme_symbol": "T1", "symbols": ["000001"], "trade_date": day.isoformat()}
        for day in days
    ]
    result = derive_historical_overrides(
        days[-1],
        [{"code": "000001", "theme_symbol": "T1"}],
        up,
        members,
        failed_limit_rows=failed,
    )
    facts = result["stock_overrides"]["000001"]
    assert facts["first_divergence"] is True
    assert facts["first_divergence_support"] is None
    assert "missing_divergence_day_bar" in facts["fact_basis"]["missing_reasons"]


def test_old_dragon_does_not_use_three_calendar_days_as_three_trading_days():
    days = [date(2026, 8, 7), date(2026, 8, 10)]
    up = _pool([days[0]], heights=[4]) + _pool([days[1]], heights=[2])
    result = derive_historical_overrides(
        days[-1],
        [{"code": "000001", "theme_symbol": "T1"}],
        up,
        [
            {"theme_symbol": "T1", "symbols": ["000001"], "trade_date": day.isoformat()}
            for day in days
        ],
    )
    assert result["stock_overrides"]["000001"]["old_dragon"] is None


@pytest.mark.parametrize("post_height", [4, 5])
def test_cross_cycle_requires_stage_transition_and_survival_evidence(post_height):
    days = _days(8)
    up = _pool([days[0], days[1], days[2], days[3]], heights=[1, 2, 3, 4])
    up += _pool([days[7]], heights=[post_height])
    members = [
        {"theme_symbol": "T1", "symbols": ["000001"], "trade_date": day.isoformat()}
        for day in days
    ]
    current = [{
        "code": "000001",
        "theme_symbol": "T1",
    }]
    sentiments = [
        {"trade_date": days[4].isoformat(), "cycle": "冰点"},
        {"trade_date": days[6].isoformat(), "cycle": "主升"},
    ]
    result = derive_historical_overrides(
        days[-1],
        current,
        up,
        members,
        sentiments=sentiments,
        bars=_bars(
            [days[0] - timedelta(days=1), *days],
            closes=[10, 12, 13, 14, 15, 14, 13, 14, 16],
        ),
    )
    assert result["stock_overrides"]["000001"]["cross_cycle"] is True


def test_current_candidates_are_not_added_as_today_limit_ups():
    days = _days(4)
    result = derive_historical_overrides(
        days[-1], [{"code": "000001", "continuous_days": 4, "sector": "行业"}],
        _pool(days[:2], heights=[3, 4]), [], requested_dates=days,
    )
    assert days[-1].isoformat() not in result["stock_overrides"]["000001"]["fact_basis"]["limit_up_dates"]


def test_cross_cycle_is_unknown_when_transition_daily_bars_are_incomplete():
    days = _days(8)
    up = _pool([days[0], days[1], days[2], days[3]], heights=[1, 2, 3, 4])
    up += _pool([days[7]], heights=[4])
    members = [
        {"theme_symbol": "T1", "symbols": ["000001"], "trade_date": day.isoformat()}
        for day in days
    ]
    sentiments = [
        {"trade_date": days[4].isoformat(), "cycle": "冰点"},
        {"trade_date": days[6].isoformat(), "cycle": "主升"},
    ]
    result = derive_historical_overrides(
        days[-1],
        [{"code": "000001", "theme_symbol": "T1"}],
        up,
        members,
        sentiments=sentiments,
        bars=_bars([days[0], days[1], days[2], days[3], days[4], days[6], days[7]]),
    )
    facts = result["stock_overrides"]["000001"]
    assert facts["cross_cycle"] is None
    assert "incomplete_transition_daily_bar_coverage" in facts["cross_cycle_evidence"]["missing_reasons"]


def test_old_divergence_is_not_reused_as_current_first_volume_divergence():
    days = _days(8)
    up = _pool(days[:2], heights=[1, 2])
    failed = [{"code": "000001", "trade_date": days[2].isoformat()}]
    members = [
        {"theme_symbol": "T1", "symbols": ["000001"], "trade_date": day.isoformat()}
        for day in days
    ]
    result = derive_historical_overrides(
        days[-1],
        [{"code": "000001", "theme_symbol": "T1"}],
        up,
        members,
        failed_limit_rows=failed,
        bars=_bars(days),
    )
    facts = result["stock_overrides"]["000001"]
    assert facts["first_divergence"] is None
    assert facts["first_volume_divergence"] is None


class FakeProvider:
    name = "fake_numcat"

    def __init__(self, up, failed, members):
        self.up, self.failed, self.members = up, failed, members
        self.member_dates = []
        self.theme_queries = []

    async def limit_pool(self, pool_type, **kwargs):
        return self.up if pool_type == "u" else self.failed

    async def theme_members(self, **kwargs):
        self.member_dates.append(kwargs["tradedate"])
        self.theme_queries.append(kwargs.get("theme_symbols"))
        return self.members

    async def theme_daily(self, **kwargs):
        return [{"theme_symbol": "T1", "theme_name": "真实题材", "trade_date": "2026-08-27"}]


@pytest.mark.asyncio
async def test_service_contract_is_bounded_and_returns_source_coverage():
    days = _days(25)
    up = _pool(days, heights=[1] * 25)
    members = [{"theme_symbol": "T1", "symbols": ["000001"]} for _ in days]
    provider = FakeProvider(up, [], members)
    result = await WildmanHistoryService(provider).build_history_context(
        days[-1], [{"code": "000001", "theme_symbol": "T1"}], _bars(days), []
    )
    assert len(result["source"]["observed_dates"]) <= MAX_HISTORY_TRADING_DAYS
    assert result["source"]["coverage"]["bounded"] is True
    assert result["source"]["provider"] == "fake_numcat"
    assert len(provider.member_dates) <= MAX_HISTORY_TRADING_DAYS
    assert all(query == ["T1"] for query in provider.theme_queries)
    assert result["stock_overrides"]["000001"]["primary_theme_name"] == "真实题材"


@pytest.mark.asyncio
async def test_service_does_not_send_industry_label_as_theme_symbol():
    days = _days(2)
    provider = FakeProvider(
        _pool(days),
        [],
        [{"theme_symbol": "T1", "symbols": ["000001"]}],
    )
    await WildmanHistoryService(provider).build_history_context(
        days[-1],
        [{"code": "000001", "sector": "煤炭"}],
        _bars(days),
        [],
    )
    assert all(query is None for query in provider.theme_queries)


@pytest.mark.asyncio
async def test_partial_or_industry_theme_coverage_uses_all_dated_memberships():
    days = _days(2)
    provider = FakeProvider(
        _pool(days),
        [],
        [{"theme_symbol": "T1", "symbols": ["000001"]}],
    )
    await WildmanHistoryService(provider).build_history_context(
        days[-1],
        [
            {"code": "000001", "theme_symbol": "T1", "sector": "半导体"},
            {"code": "000002", "sector": "机器人"},
        ],
        _bars(days),
        [],
    )
    assert all(query is None for query in provider.theme_queries)


@pytest.mark.asyncio
async def test_theme_name_mapping_is_point_in_time_safe():
    class FutureMappingProvider(FakeProvider):
        async def theme_daily(self, **kwargs):
            return [{
                "theme_symbol": "T1",
                "theme_name": "未来才出现的题材名",
                "trade_date": "2026-12-31",
            }]

    days = _days(2)
    provider = FutureMappingProvider(
        _pool(days),
        [],
        [{"theme_symbol": "T1", "symbols": ["000001"]}],
    )
    result = await WildmanHistoryService(provider).build_history_context(
        days[-1], [{"code": "000001", "theme_symbol": "T1"}], _bars(days), []
    )
    assert result["source"]["theme_symbol_mapping_count"] == 0
    assert result["stock_overrides"]["000001"]["primary_theme_name"] == "T1"
