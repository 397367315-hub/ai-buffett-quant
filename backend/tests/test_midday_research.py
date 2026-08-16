import json
import unittest
from datetime import date, datetime
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from api import research_routes
from database import Base
from models import ResearchSession
from services.midday_research import (
    MiddayResearchService,
    _stock_anomalies,
    build_midday_report,
)
from services.overnight_strategy import STRATEGY_CONFIG
from services.weekend_research import WeekendResearchService


def quote(
    code: str,
    sector: str,
    change: float,
    *,
    price: float = 16.0,
    ratio: float = 1.6,
    turnover: float = 6.5,
    market_cap_yi: float = 100.0,
    flow: int = 80_000_000,
) -> dict:
    return {
        "code": code,
        "name": f"测试{code}",
        "sector": sector,
        "price": price,
        "change_pct": change,
        "volume_ratio": ratio,
        "turnover": turnover,
        "market_cap": market_cap_yi * 100_000_000,
        "main_net_inflow": flow,
        "main_net_inflow_pct": 2.5,
        "amount": 120_000_000,
        "volume": 200_000,
        "high": price * 1.03,
        "low": price * 0.98,
        "previous_close": price / (1 + change / 100),
    }


def workbench_fixture() -> dict:
    return {
        "available": True,
        "meta": {"decision_date": "2026-08-14", "source": "database_cache"},
        "market_state": {
            "state_label": "趋势分歧",
            "score": 62,
            "dimensions": [
                {"id": "liquidity", "score": 52},
                {"id": "risk", "score": 58},
            ],
        },
        "structure_health": {"score": 64},
        "crowding_risk": {"score": 54},
        "headline_metrics": {"market_amount": 1_800_000_000, "failed_limit_rate": 18},
        "market_cognition": {
            "principal_contradiction": {
                "statement": "指数趋势与增量成交不足之间的矛盾",
                "evidence": [
                    "指数结构未坏",
                    "成交扩张尚待确认",
                    "成交额较前5日基准 +999.0%",
                    "头部涨幅待采集",
                ],
            },
            "dominant_aspect": {"statement": "趋势仍在但短线承接不足"},
        },
        "execution_queue": {"phases": []},
    }


def midday_snapshot() -> dict:
    rows = []
    for index, change in enumerate([9.8, 0.4, 0.1, -0.3, -0.6], start=1):
        rows.append(quote(f"6000{index:02d}", "机器人", change))
    for index, change in enumerate([5.0, 4.0, 3.0, 2.0, 1.0], start=1):
        rows.append(quote(f"0001{index:02d}", "AI算力", change))
    for index, change in enumerate([0.5, 0.2, 0.0, -0.2, -0.3], start=1):
        rows.append(quote(f"6012{index:02d}", "有色", change))
    return {
        "stocks": rows,
        "complete": True,
        "is_realtime": False,
        "source": "cache",
        "data_date": "2026-08-14",
    }


class MiddayResearchBuilderTests(unittest.TestCase):
    def test_report_is_serializable_and_supports_sum_to_one_hundred(self):
        snapshot = midday_snapshot()
        topic = {
            "source": "topic-cache",
            "market": {
                "top_sectors": [
                    {"name": "AI算力", "main_net_inflow": 2_000_000_000},
                    {"name": "机器人", "main_net_inflow": 1_200_000_000},
                ]
            },
        }
        previous = {
            "morning_autopsy": {"metrics": {"market_amount": 1_500_000_000}},
            "principal_conflict": {"current_statement": "指数趋势与增量成交不足之间的矛盾"},
        }
        positions = {item["code"]: 0.5 for item in snapshot["stocks"]}

        report = build_midday_report(
            workbench=workbench_fixture(),
            topic=topic,
            snapshot=snapshot,
            market_quote={"sh_change_pct": 0.6},
            positions=positions,
            previous_report=previous,
            strategic=None,
            tail_preview={"strategy_id": "overnight", "candidate_count": 0, "candidates": []},
            session_id="mr_test",
        )

        json.dumps(report, ensure_ascii=False)
        self.assertAlmostEqual(sum(item["support_pct"] for item in report["afternoon_scenarios"]), 100.0)
        metrics = report["morning_autopsy"]["metrics"]
        self.assertEqual(metrics["limit_up_count"], 1)
        self.assertEqual(metrics["previous_midday_amount"], 1_500_000_000)
        self.assertEqual(metrics["comparison_baseline"], "前一午间全市场快照")
        structures = {item["name"]: item for item in report["sector_structures"]}
        self.assertEqual(structures["机器人"]["status"], "龙头抱团")
        self.assertEqual(structures["AI算力"]["status"], "强化")
        self.assertEqual(len(structures["机器人"]["roles"]), 4)
        self.assertEqual(structures["有色"]["main_net_inflow"], 400_000_000)
        self.assertEqual(structures["有色"]["flow_source"], "板块成员主力资金汇总")
        self.assertFalse(any(
            "前5日基准" in item or "待采集" in item
            for item in report["principal_conflict"]["evidence"]
        ))
        self.assertTrue(report["tail_preview"].get("strategy_id"))

    def test_first_run_builds_baseline_without_inventing_comparison(self):
        report = build_midday_report(
            workbench=workbench_fixture(),
            topic={},
            snapshot=midday_snapshot(),
            market_quote={},
            positions={},
            previous_report=None,
            strategic=None,
            tail_preview={"strategy_id": "overnight", "candidate_count": 0, "candidates": []},
        )

        metrics = report["morning_autopsy"]["metrics"]
        self.assertIsNone(metrics["amount_change_pct"])
        self.assertIn("首日", metrics["comparison_baseline"])
        self.assertTrue(any(
            "本次已建立基线" in item
            for item in report["data_quality"]["missing_fields"]
        ))
        self.assertEqual(report["meta"]["phase"], "HISTORICAL")
        self.assertEqual(report["meta"]["phase_label"], "历史快照研究")
        self.assertEqual(metrics["volume_support"], "未确认")
        self.assertTrue(any(
            "近20日位置覆盖不足" in item
            for item in report["data_quality"]["missing_fields"]
        ))
        low_position_answer = next(
            item["answer"] for item in report["morning_autopsy"]["answers"]
            if item["question"] == "高位股与低位股谁更占优？"
        )
        self.assertNotIn("None", low_position_answer)

    def test_alpha_beta_and_high_position_anomalies_are_distinct(self):
        stocks = [
            {**quote("600001", "弱板块", 2.2), "volume_ratio": 1.8},
            {**quote("600002", "强板块", 1.0), "volume_ratio": 1.4, "main_net_inflow": -20_000_000},
            {**quote("600003", "高位板块", -2.1), "volume_ratio": 1.6, "main_net_inflow": -50_000_000},
        ]
        normalized = []
        for item in stocks:
            normalized.append({
                "code": item["code"], "name": item["name"], "sector": item["sector"],
                "price": item["price"], "change_pct": item["change_pct"],
                "volume_ratio": item["volume_ratio"], "turnover": item["turnover"],
                "main_net_inflow": item["main_net_inflow"],
            })
        sectors = [
            {"name": "弱板块", "average_change_pct": -1.0, "status": "弱化"},
            {"name": "强板块", "average_change_pct": 3.2, "status": "强化"},
            {"name": "高位板块", "average_change_pct": -1.2, "status": "弱化"},
        ]

        result = _stock_anomalies(
            normalized,
            sectors,
            {"600001": 0.4, "600002": 0.5, "600003": 0.9},
            market_median=-0.4,
        )

        self.assertEqual(result["counts"]["contrarian_strength"], 1)
        self.assertEqual(result["counts"]["alpha_strengthening"], 1)
        self.assertEqual(result["counts"]["beta_weak"], 1)
        self.assertEqual(result["counts"]["high_position_negative_feedback"], 1)


class MiddayPreviewTests(unittest.IsolatedAsyncioTestCase):
    async def test_preview_keeps_execution_boundary_and_uses_active_strategy(self):
        stock = quote("600519", "食品饮料", 3.5, price=17.0, ratio=1.8, turnover=6.8)
        stock["volume"] = 300
        bars = [
            {"close": 10 + index * 0.1, "volume": 100 + index}
            for index in range(65)
        ]
        service = MiddayResearchService()
        with patch(
            "services.midday_research.overnight_strategy_service._daily_bars",
            new=AsyncMock(return_value={"600519": bars}),
        ):
            preview = await service._tail_preview(
                {"stocks": [stock], "data_date": "2026-08-14", "is_realtime": False},
                date(2026, 8, 14),
                {"strategy": {**STRATEGY_CONFIG}},
            )

        self.assertEqual(preview["strategy_id"], STRATEGY_CONFIG["id"])
        self.assertEqual(preview["candidate_count"], 1)
        self.assertEqual(preview["candidates"][0]["quality"], "高质量")
        self.assertTrue(preview["preview_only"])
        self.assertIn("不建立模拟持仓", preview["boundary"])


class MiddayResearchRouteTests(unittest.IsolatedAsyncioTestCase):
    async def test_start_and_track_routes_call_midday_service(self):
        with patch.object(
            research_routes.midday_research_service,
            "start",
            new=AsyncMock(return_value={"id": "mr_test", "status": "DRAFT"}),
        ) as start:
            response = await research_routes.start_midday_research(
                research_routes.MiddayResearchRequest(force=True),
            )
        self.assertEqual(response["data"]["id"], "mr_test")
        start.assert_awaited_once_with(force=True, background=True)

        with patch.object(
            research_routes.midday_research_service,
            "track",
            new=AsyncMock(return_value={"checkpoint": "13:30"}),
        ) as track:
            response = await research_routes.track_midday_research(
                "mr_test",
                research_routes.MiddayTrackRequest(checkpoint="13:30", force_quote=False),
            )
        self.assertEqual(response["data"]["checkpoint"], "13:30")
        track.assert_awaited_once_with("13:30", session_id="mr_test", force_quote=False)


class ResearchModeIsolationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        self.session_factory = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.weekend_patch = patch("services.weekend_research.async_session", self.session_factory)
        self.midday_patch = patch("services.midday_research.async_session", self.session_factory)
        self.weekend_patch.start()
        self.midday_patch.start()

    async def asyncTearDown(self):
        self.weekend_patch.stop()
        self.midday_patch.stop()
        await self.engine.dispose()

    @staticmethod
    def row(session_id: str, mode: str) -> ResearchSession:
        return ResearchSession(
            id=session_id,
            mode=mode,
            status="COMPLETED",
            stage="完成",
            progress=100,
            as_of_date=date(2026, 8, 14),
            source_data_date=date(2026, 8, 14),
            market_data_version="test",
            fundamental_data_version="test",
            strategy_version="test",
            model_version="test",
            prompt_version="test",
            research_version="test",
            report={"conclusion": {"market_state": mode}},
        )

    async def test_weekend_and_midday_history_do_not_mix(self):
        async with self.session_factory() as session:
            session.add_all([self.row("wr_test", "quick"), self.row("mr_test", "midday")])
            await session.commit()

        weekend = WeekendResearchService()
        midday = MiddayResearchService()
        weekend_rows = await weekend.list()
        midday_rows = await midday.list()

        self.assertEqual([item["id"] for item in weekend_rows], ["wr_test"])
        self.assertEqual([item["id"] for item in midday_rows], ["mr_test"])
        self.assertTrue(midday_rows[0]["created_at"].endswith("Z"))
        self.assertIsNone(await weekend.get("mr_test"))
        self.assertIsNone(await midday.get("wr_test"))

    async def test_premarket_result_does_not_block_midday_refresh(self):
        premarket = self.row("mr_premarket", "midday")
        premarket.as_of_date = date(2026, 8, 17)
        premarket.source_data_date = date(2026, 8, 14)
        premarket.created_at = datetime(2026, 8, 16, 19, 0)
        premarket.completed_at = datetime(2026, 8, 16, 19, 1)
        async with self.session_factory() as session:
            session.add(premarket)
            await session.commit()

        service = MiddayResearchService()
        now = datetime(2026, 8, 17, 11, 42, tzinfo=ZoneInfo("Asia/Shanghai"))
        with (
            patch("services.midday_research.shanghai_now", return_value=now),
            patch.object(service, "_run", new=AsyncMock()) as run,
        ):
            result = await service.start(force=False, background=False)

        self.assertNotEqual(result["id"], "mr_premarket")
        run.assert_awaited_once()

    async def test_close_validation_rejects_previous_day_snapshot(self):
        row = self.row("mr_current", "midday")
        row.as_of_date = date(2026, 8, 17)
        row.source_data_date = date(2026, 8, 17)
        row.report = {"validation": {"completed": False}}
        async with self.session_factory() as session:
            session.add(row)
            await session.commit()

        service = MiddayResearchService()
        now = datetime(2026, 8, 17, 15, 50, tzinfo=ZoneInfo("Asia/Shanghai"))
        stale_snapshot = {
            "data_date": "2026-08-14",
            "complete": True,
            "stocks": [quote("600001", "测试", 1.0)],
        }
        with (
            patch("services.midday_research.shanghai_now", return_value=now),
            patch(
                "services.midday_research.collector.fetch_quant_market_snapshot",
                new=AsyncMock(return_value=stale_snapshot),
            ),
        ):
            updated = await service.validate_pending()

        self.assertEqual(updated, [])
        saved = await service.get("mr_current")
        self.assertFalse(saved["report"]["validation"]["completed"])


if __name__ == "__main__":
    unittest.main()
