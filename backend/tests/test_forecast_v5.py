from unittest.mock import AsyncMock, patch

from datetime import date, datetime

import services.forecast_v5 as forecast_module
from api import forecast_routes
from services.factor_registry_v5 import factor_definitions
from services.forecast_v5 import ForecastV5Service, HORIZONS


def _factors(*, freshness: float = 1.0) -> list[dict]:
    return [
        {
            "id": item["id"],
            "name": item["name"],
            "layer": item["layer"],
            "source": item["source"],
            "source_level": item["source_level"],
            "lead_score": item["lead_score"],
            "observed": True,
            "freshness": freshness,
            "updated_at": "2026-09-10T10:00:00",
            "signal": 0.2,
            "direction_score": 0.2,
        }
        for item in factor_definitions()
    ]


def test_truth_failure_blocks_high_confidence_even_with_complete_factors():
    health = ForecastV5Service._data_health(
        _factors(),
        {},
        datetime(2026, 9, 10, 10, 1),
        truth={
            "status": "FAIL",
            "status_label": "真值阻断",
            "confidence_pct": 96,
            "high_confidence_allowed": False,
        },
        trading_permission={"code": "ALLOW", "label": "允许研究"},
    )

    assert health["completeness_pct"] == 100.0
    assert health["high_confidence_allowed"] is False
    assert health["execution_allowed"] is False
    assert health["level"] == "真值阻断"
    assert "V4真值层未通过" in health["block_reasons"]


def test_stale_coverage_blocks_data_high_confidence():
    health = ForecastV5Service._data_health(
        _factors(freshness=0.1),
        {},
        datetime(2026, 9, 10, 10, 1),
        truth={
            "status": "PASS",
            "status_label": "真值通过",
            "confidence_pct": 90,
            "high_confidence_allowed": True,
        },
        trading_permission={"code": "ALLOW", "label": "允许研究"},
    )

    assert health["fresh_coverage_pct"] == 0.0
    assert health["data_high_confidence_allowed"] is False
    assert health["high_confidence_allowed"] is False


def test_uncalibrated_model_emits_tendency_without_probability():
    service = ForecastV5Service()
    health = {
        "completeness_pct": 100.0,
        "fresh_coverage_pct": 100.0,
        "confidence_ceiling_pct": 88.0,
        "high_confidence_allowed": False,
    }
    result = service._forecast_for_horizon(
        HORIZONS[0],
        "S2",
        _factors(),
        {"defensive_resonance_pct": 35, "offensive_resonance_pct": 65},
        [],
        health,
    )

    assert result["forecast_mode"] == "研究倾向"
    assert result["probability_qualified"] is False
    assert result["confidence_pct"] is None
    assert all(value is None for value in result["probabilities"].values())
    assert all(item["probability_pct"] is None for item in result["scenarios"])


def test_behavior_factors_are_nearly_removed_from_quarter_signal():
    service = ForecastV5Service()
    behavior = [{
        "id": "fomo_behavior",
        "layer": "propagation",
        "observed": True,
        "signal": 1.0,
        "direction_score": 1.0,
        "reliability": 1.0,
        "lead_score": 1.0,
    }]
    neutral = [{
        "id": "policy_support",
        "layer": "leading",
        "observed": True,
        "signal": 0.0,
        "direction_score": 0.0,
        "reliability": 1.0,
        "lead_score": 1.0,
    }]

    short, _ = service._weighted_signal([*behavior, *neutral], HORIZONS[0])
    quarter, _ = service._weighted_signal([*behavior, *neutral], HORIZONS[-1])

    assert short is not None and quarter is not None
    assert quarter < short * 0.2


def _workbench(*, truth_status: str = "PASS") -> dict:
    return {
        "available": True,
        "meta": {
            "decision_date": "2026-09-10",
            "updated_at": "2026-09-10T10:00:00",
        },
        "headline_metrics": {"market_amount": 1000, "up_count": 10, "down_count": 5},
        "main_lines": [{"name": "测试主线", "strength_score": 72}],
        "market_state": {"state_code": "S2", "score": 60},
        "execution_queue": {
            "phases": [
                {"id": "tail", "display_status": "等待窗口", "candidate_count": 0},
                {"id": "auction", "display_status": "等待真实竞价序列", "candidate_count": 0},
            ],
        },
        "decision_2026": {
            "trading_permission": {
                "code": "ALLOW",
                "label": "允许研究",
                "max_total_position_pct": 30,
                "reasons": ["市场许可样本"],
            },
        },
        "market_way_v4": {
            "truth": {
                "status": truth_status,
                "status_label": "真值阻断" if truth_status == "FAIL" else "真值通过",
                "confidence_pct": 96,
                "high_confidence_allowed": truth_status == "PASS",
            },
        },
    }


class _NoopSession:
    async def get(self, *_args, **_kwargs):
        return None

    def add(self, *_args, **_kwargs):
        return None

    async def commit(self):
        return None


class _NoopSessionContext:
    async def __aenter__(self):
        return _NoopSession()

    async def __aexit__(self, *_args):
        return False


def _noop_async_session():
    return _NoopSessionContext()


def _build_mocks(service: ForecastV5Service, *, truth_status: str = "PASS"):
    now = datetime(2026, 9, 10, 10, 1)
    patches = [
        patch.object(service, "_cache", new=AsyncMock(return_value={})),
        patch.object(service, "_macro", new=AsyncMock(return_value={})),
        patch.object(service, "_sentiment_history", new=AsyncMock(return_value=[])),
        patch.object(service, "_sector_rows", new=AsyncMock(return_value=[])),
        patch.object(service, "_persist", new=AsyncMock()),
        patch.object(forecast_module, "shanghai_now", return_value=now),
        patch.object(forecast_module, "is_a_share_market_session", return_value=True),
        patch.object(forecast_module, "async_session", new=_noop_async_session),
        patch.object(
            forecast_module.behavior_analysis_v5_service,
            "evaluate",
            new=AsyncMock(return_value={"factors": [], "market_psychology_state": "数据不足"}),
        ),
    ]
    return patches, _workbench(truth_status=truth_status)


def test_build_embeds_workbench_summary_and_today_action_center_without_external_calls():
    service = ForecastV5Service()
    patches, workbench = _build_mocks(service)
    for item in patches:
        item.start()
    try:
        result = __import__("asyncio").run(service.build(force=True, workbench_override=workbench))
    finally:
        for item in reversed(patches):
            item.stop()

    assert result["workbench_summary"]["available"] is True
    assert result["workbench_summary"]["meta"] == workbench["meta"]
    assert result["workbench_summary"]["main_lines"] == workbench["main_lines"]
    assert result["today_action_center"]["trade_date"] == "2026-09-10"
    assert result["today_action_center"]["current_stage"] == "midday"
    assert result["today_action_center"]["permission"]["code"] == "BLOCK"
    assert result["today_action_center"]["permission"]["label"] == "数据资格不足，禁止执行"
    assert [item["id"] for item in result["today_action_center"]["stages"]] == [
        "pre_market", "auction", "midday", "tail", "post_close",
    ]
    assert result["today_action_center"]["stages"][1]["label"] == "09:25竞价确认"


def test_truth_failure_unifies_action_center_permission_and_reason():
    service = ForecastV5Service()
    patches, workbench = _build_mocks(service, truth_status="FAIL")
    for item in patches:
        item.start()
    try:
        result = __import__("asyncio").run(service.build(force=True, workbench_override=workbench))
    finally:
        for item in reversed(patches):
            item.stop()

    health = result["data_health"]
    permission = result["today_action_center"]["permission"]
    assert health["truth_status"] == "FAIL"
    assert permission["code"] == "BLOCK"
    assert permission["label"] == "真值阻断，只允许查看"
    assert permission["max_total_position_pct"] == 0
    assert permission["reasons"] == ["V4真值层未通过"]
    assert health["block_reasons"][0] == permission["reasons"][0]


def test_research_only_action_center_never_exposes_executable_position():
    action = ForecastV5Service._today_action_center(
        _workbench(),
        {
            "truth_status": "PASS",
            "truth_status_label": "真值通过",
            "execution_allowed": True,
            "calibration_qualified": False,
            "block_reasons": ["多周期概率模型尚未完成样本外校准"],
        },
        "pre_market",
        date(2026, 9, 10),
        datetime(2026, 9, 10, 8, 30),
    )

    assert action["permission"]["code"] == "RESEARCH_ONLY"
    assert action["permission"]["label"] == "可研究，预测概率尚未校准"
    assert action["permission"]["max_total_position_pct"] == 0


def test_dashboard_without_skills_returns_explicit_empty_contract_and_does_not_load_skills():
    service = ForecastV5Service()
    service.build = AsyncMock(return_value={
        "forecast_date": "2026-09-10",
        "trading_skills": {"active_skills": [{"id": "heavy"}], "candidates": [{"code": "600000"}]},
    })
    with patch("services.trading_skill_service.trading_skill_service.dashboard", new_callable=AsyncMock) as skills_dashboard:
        result = __import__("asyncio").run(service.dashboard(include_skills=False))

    skills_dashboard.assert_not_awaited()
    assert result["trading_skills"] == {
        "included": False,
        "action": "NOT_REQUESTED",
        "active_skills": [],
        "candidates": [],
        "scanned_count": 0,
        "data_cutoff_time": None,
        "cache_used": False,
        "filters": {},
        "reflexivity": {},
    }


def test_dashboard_with_skills_keeps_legacy_fields_and_passes_market_filters():
    service = ForecastV5Service()
    service.build = AsyncMock(return_value={"forecast_date": "2026-09-10"})
    skill_payload = {
        "action": "OBSERVE",
        "market_permission": {"code": "ALLOW"},
        "active_skills": [{"id": "VOLUME_SHOCK"}],
        "candidates": [{"code": str(index)} for index in range(10)],
        "scanned_count": 10,
        "data_cutoff_time": "2026-09-10T09:25:00",
        "cache_used": True,
        "filters": {"exclude_star_market": False, "exclude_gem": True},
        "reflexivity": {"state": "observe"},
    }
    with patch("services.trading_skill_service.trading_skill_service.dashboard", new_callable=AsyncMock, return_value=skill_payload) as skills_dashboard:
        result = __import__("asyncio").run(service.dashboard(
            include_skills=True,
            exclude_star_market=False,
            exclude_gem=True,
        ))

    skills_dashboard.assert_awaited_once_with(
        force=False,
        exclude_star_market=False,
        exclude_gem=True,
    )
    assert result["trading_skills"]["included"] is True
    assert result["trading_skills"]["active_skills"] == [{"id": "VOLUME_SHOCK"}]
    assert len(result["trading_skills"]["candidates"]) == 8
    assert result["trading_skills"]["filters"] == skill_payload["filters"]


def test_forecast_dashboard_route_passes_skill_contract_parameters():
    service_result = {"workbench_summary": {}, "today_action_center": {}, "trading_skills": {"included": False}}
    with patch.object(forecast_routes.forecast_v5_service, "dashboard", new_callable=AsyncMock, return_value=service_result) as dashboard:
        result = __import__("asyncio").run(forecast_routes.get_forecast_dashboard(
            refresh=True,
            include_skills=False,
            exclude_star_market=False,
            exclude_gem=False,
        ))

    dashboard.assert_awaited_once_with(
        force=True,
        include_skills=False,
        exclude_star_market=False,
        exclude_gem=False,
    )
    assert result == {"code": 0, "data": service_result}
