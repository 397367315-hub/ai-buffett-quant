from datetime import date
from unittest.mock import AsyncMock, patch

import pytest

from services import wildman_risk_service as module
from services.wildman_risk_service import WildmanRiskService
from services.wildman_classic_service import WildmanClassicService


def _provider():
    return type(
        "Provider",
        (),
        {
            "configured": True,
            "announcements": AsyncMock(return_value=[]),
            "finance_indicator": AsyncMock(return_value=[{
                "code": "600519",
                "report_date": "20260630",
                "announce_date": "20260829",
                "eps": 2,
                "roe": 8,
                "debt_to_assets": 40,
            }]),
        },
    )()


@pytest.mark.asyncio
async def test_success_empty_is_unknown_and_not_no_risk():
    provider = _provider()
    with patch.object(module, "numcat_market_provider", provider), patch.object(
        module.WildmanRiskService, "_pit_financials", new=AsyncMock(return_value={}
    )), patch.object(module.WildmanRiskService, "_fallback_announcements", new=AsyncMock(return_value={
        "600519": ([], None),
    })):
        result = await WildmanRiskService().risk_facts(["600519"], date(2026, 9, 18))
    fact = result["600519"]
    assert fact["fundamentals_clear"] is None
    assert fact["major_risk"] is None
    assert fact["coverage"]["status"] == "success_empty"
    assert any(item["status"] == "success_empty" for item in fact["risk_evidence"])


@pytest.mark.asyncio
async def test_pit_filters_future_announcement_and_keeps_original_url():
    provider = _provider()
    provider.announcements.return_value = [{
        "symbol": "600519",
        "event_date": "20260919",
        "title": "future risk",
        "content_url": "https://example.invalid/future",
    }, {
        "symbol": "600519",
        "event_date": "20260918",
        "title": "关于立案调查的风险提示",
        "content_url": "https://example.invalid/pit",
    }]
    with patch.object(module, "numcat_market_provider", provider), patch.object(
        module.WildmanRiskService, "_pit_financials", new=AsyncMock(return_value={})
    ), patch.object(module.WildmanRiskService, "_fallback_announcements", new=AsyncMock(return_value={})):
        result = await WildmanRiskService().risk_facts(["600519"], date(2026, 9, 18))
    fact = result["600519"]
    assert fact["major_risk"] is True
    hits = [item for item in fact["risk_evidence"] if item.get("status") == "risk_hit"]
    assert len(hits) == 1
    assert hits[0]["url"] == "https://example.invalid/pit"
    assert all(item.get("published_at") != "2026-09-19" for item in fact["risk_evidence"])


@pytest.mark.asyncio
async def test_latest_pit_and_observed_window_can_clear_only_the_declared_proxy():
    provider = _provider()
    provider.announcements.return_value = [{
        "symbol": "600519",
        "event_date": "20260918",
        "title": "关于年度报告披露的公告",
        "content_url": "https://example.invalid/annual",
    }]
    pit = {
        "600519": {
            "net_profit": 10,
            "operating_cf": 8,
            "report_date": "20260630",
            "disclosed_at": "20260829",
            "source": "financial_pit_snapshots",
        }
    }
    with patch.object(module, "numcat_market_provider", provider), patch.object(
        module.WildmanRiskService, "_pit_financials", new=AsyncMock(return_value=pit)
    ), patch.object(module.WildmanRiskService, "_fallback_announcements", new=AsyncMock(return_value={} )):
        fact = (await WildmanRiskService().risk_facts(["600519"], date(2026, 9, 18)))["600519"]
    assert fact["major_risk"] is False
    assert fact["fundamentals_clear"] is True
    assert fact["financial_safe"] is None
    assert "不认证完整基本面安全" in fact["financial_screen"]["proxy_rules"]


@pytest.mark.asyncio
async def test_risk_evidence_is_summarized_but_counts_use_all_rows():
    provider = _provider()
    provider.announcements.return_value = [
        {
            "symbol": "600519",
            "event_date": f"2026-09-{(18 - index % 9):02d}",
            "title": "关于常规公告的说明" if index else "关于立案调查的风险提示",
            "content_url": f"https://example.invalid/{index}",
        }
        for index in range(30)
    ]
    with patch.object(module, "numcat_market_provider", provider), patch.object(
        module.WildmanRiskService, "_pit_financials", new=AsyncMock(return_value={})
    ), patch.object(module.WildmanRiskService, "_fallback_announcements", new=AsyncMock(return_value={} )):
        fact = (await WildmanRiskService().risk_facts(["600519"], date(2026, 9, 18), refresh=True))["600519"]
    assert len(fact["risk_evidence"]) <= 24
    assert fact["risk_evidence_summary"]["evidence_total"] > 24
    assert fact["risk_evidence_summary"]["risk_hit_count"] == 1
    assert fact["risk_evidence_summary"]["evidence_truncated"] is True


@pytest.mark.asyncio
async def test_numcat_failure_uses_bounded_fallback_and_preserves_unknown_on_failure():
    provider = _provider()
    provider.announcements.side_effect = RuntimeError("BUSINESS422")
    provider.finance_indicator.side_effect = RuntimeError("BUSINESS422")
    service = WildmanRiskService()
    with patch.object(module, "numcat_market_provider", provider), patch.object(
        service, "_pit_financials", new=AsyncMock(return_value={}
    )), patch.object(service, "_fallback_announcements", new=AsyncMock(return_value={
        "600519": ([], "FallbackError"),
    })) as fallback:
        result = await service.risk_facts(["600519"], date(2026, 9, 18))
    assert fallback.await_count == 1
    assert result["600519"]["major_risk"] is None
    assert result["600519"]["coverage"]["status"] == "failed"


@pytest.mark.asyncio
async def test_classic_fundamental_adapter_preserves_unknown_and_major_risk():
    facts = {
        "600519": {
            "fundamentals_clear": None,
            "major_risk": True,
            "financial_screen": {"eps": 1.0},
            "coverage": {"status": "covered", "sources": ["numcat_finance_announcement"]},
            "risk_evidence": [{"status": "risk_hit", "url": "https://example.invalid/risk"}],
        }
    }
    with patch("services.wildman_risk_service.risk_facts", new=AsyncMock(return_value=facts)):
        result = await WildmanClassicService()._load_fundamentals(["600519"], date(2026, 9, 18))
    assert result["600519"]["fundamental_safe"] is None
    assert result["600519"]["financial_safe"] is None
    assert result["600519"]["major_risk"] is True
    assert result["600519"]["risk_evidence"][0]["url"] == "https://example.invalid/risk"


@pytest.mark.asyncio
async def test_success_empty_does_not_trigger_fallback_and_cache_is_per_symbol():
    provider = _provider()
    service = WildmanRiskService()
    with patch.object(module, "numcat_market_provider", provider), patch.object(
        service, "_pit_financials", new=AsyncMock(return_value={}
    )), patch.object(service, "_fallback_announcements", new=AsyncMock(return_value={})) as fallback, patch.object(
        service, "_risk_facts_uncached", wraps=service._risk_facts_uncached
    ) as uncached:
        first = await service.risk_facts(["600519"], date(2026, 9, 18))
        second = await service.risk_facts(["600519"], date(2026, 9, 18))
    assert fallback.await_count == 0
    assert uncached.await_count == 1
    assert first["600519"]["coverage"]["cache_hit"] is False
    assert second["600519"]["coverage"]["cache_hit"] is True


@pytest.mark.asyncio
async def test_symbol_bound_returns_explicit_unknown_without_provider_calls():
    service = WildmanRiskService()
    with patch.object(service, "_risk_facts_uncached", new=AsyncMock(return_value={})) as uncached:
        result = await service.risk_facts([f"{index:06d}" for index in range(161)], date(2026, 9, 18))
    assert uncached.await_count == 1
    assert len(result) == 161
    assert result["000160"]["coverage"]["status"] == "bounded_not_checked"


@pytest.mark.asyncio
async def test_truncated_numcat_batch_adaptively_splits_symbols():
    service = WildmanRiskService()
    calls = []

    async def fake_batch(codes, target, **kwargs):
        calls.append(tuple(codes))
        if len(codes) > 1:
            return ({}, {}, {}, {"announcements": True})
        code = codes[0]
        return (
            {code: [{"symbol": code, "event_date": "20260918", "title": "ok", "content_url": f"https://example.invalid/{code}"}]},
            {},
            {},
            {},
        )

    with patch.object(service, "_numcat_batch", new=AsyncMock(side_effect=fake_batch)):
        announcements, _, errors, truncated = await service._numcat_adaptive(
            ["000001", "000002", "600519", "600630"],
            date(2026, 9, 18),
            start_date=date(2025, 9, 18),
            end_date=date(2026, 9, 18),
            budget=[16],
            deadline=10**12,
        )
    assert set(announcements) == {"000001", "000002", "600519", "600630"}
    assert errors == {}
    assert truncated == {}
    assert len(calls) == 7
