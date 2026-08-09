import unittest
from datetime import date, timedelta

from services.mao_strategy_agent import MaoStrategyAgent


SOURCE_NAMES = [
    "个股行情", "个股日线", "个股资金流", "市场总览", "指数日线",
    "板块资金流", "龙虎榜", "宏观与政策", "公司公告",
]


def _bars(count: int = 80) -> list[dict]:
    start = date(2026, 4, 1)
    rows = []
    for index in range(count):
        close = 100 + index * 0.25
        rows.append({
            "date": (start + timedelta(days=index)).isoformat(),
            "open": close - 0.35,
            "close": close,
            "high": close + 0.5,
            "low": close - 0.8,
            "change_pct": 0.25,
            "turnover": 4.0,
            "volume": 1_000_000 + index * 8_000,
            "amount": 100_000_000,
            "source": "test_cache",
        })
    return rows


def _complete_context() -> dict:
    daily = _bars()
    index_history = [
        {"date": row["date"], "close": 3000 + index * 5}
        for index, row in enumerate(daily)
    ]
    source_audit = [{
        "name": name,
        "available": True,
        "source": "test",
        "data_date": "2026-06-19",
        "is_realtime": name == "个股行情",
        "cache_used": name != "个股行情",
    } for name in SOURCE_NAMES]
    return {
        "generated_at": "2026-06-19T14:50:00+08:00",
        "stock_codes": ["600519"],
        "stocks": {
            "quotes": [{
                "code": "600519", "name": "贵州茅台", "price": 125.0,
                "change_pct": 3.1, "turnover": 6.0, "volume_ratio": 2.0,
                "sector": "食品饮料", "market_cap": 1_500_000_000_000,
            }],
            "quote_metadata": {
                "source": "eastmoney", "data_date": "2026-06-19",
                "is_realtime": True, "cache_used": False, "complete": True,
            },
            "daily_bars": {"600519": daily},
        },
        "stock_fund_flow": {
            "series": {"600519": [{
                "date": (date(2026, 6, 10) + timedelta(days=index)).isoformat(),
                "main_net_inflow": 120_000_000 + index * 1_000_000,
                "super_large_net_inflow": 60_000_000,
                "large_net_inflow": 30_000_000,
                "close_price": 120 + index,
                "change_pct": 1.0,
            } for index in range(10)]},
            "available": True,
            "source": "eastmoney",
            "data_date": "2026-06-19",
            "is_realtime": True,
            "cache_used": False,
        },
        "market_overview": {
            "market_index": {"sh_change_pct": 1.1, "sh_amount": 620_000_000_000},
            "north_bound": {"latest_inflow": 3_000_000_000},
            "limit_board": {"limit_up": 68, "limit_down": 4},
            "market_breadth": {},
        },
        "index_history": {
            "history": index_history,
            "available": True,
            "source": "tencent",
            "data_date": "2026-06-19",
        },
        "sector_flow": {
            "industry": {
                "data_date": "2026-06-19",
                "top_net_inflow": [{"code": "BK001", "name": "食品饮料", "main_net_inflow": 2_000_000_000, "change_pct": 2.2}],
                "top_net_outflow": [{"code": "BK002", "name": "房地产", "main_net_inflow": -100_000_000, "change_pct": -0.8}],
            },
        },
        "dragon_board": {
            "data_date": "2026-06-19",
            "stocks": [{
                "code": "600519", "name": "贵州茅台", "net_amount": 80_000_000,
                "buy_amount": 160_000_000, "sell_amount": 80_000_000,
                "institution_count": 3, "reason": "日涨幅偏离",
            }],
        },
        "macro": {
            "a_share_outlook": {
                "score": 32.0, "headline": "A股综合方向：偏多，仍需成交额确认。",
            },
        },
        "announcements": {
            "announcements": {"600519": [{
                "source": "东方财富公告聚合", "title": "关于股份回购的进展公告",
                "published_at": "2026-06-18", "url": "https://example.test/notice",
            }]},
            "status": {"600519": {"available": True, "source": "eastmoney"}},
            "requested": 1,
            "covered": 1,
        },
        "personal_pool": {"items": []},
        "source_audit": source_audit,
    }


class MaoStrategyAgentTests(unittest.TestCase):
    def setUp(self):
        self.service = MaoStrategyAgent()

    def test_complete_evidence_returns_all_required_sections_and_guardrails(self):
        report = self.service.analyze_context(
            "分析600519",
            _complete_context(),
            regime={"regime": "牛市", "bias": "bullish", "confidence": 0.8},
        )

        self.assertEqual(report["data_audit"]["grade"], "充分")
        self.assertEqual(report["cycle"]["stage"], "counteroffensive")
        self.assertEqual(len(report["camps"]), 4)
        self.assertEqual(len(report["strategy_factors"]), 5)
        self.assertEqual(len(report["research_hypotheses"]), 5)
        self.assertEqual(report["tactics"]["total_position_range_pct"], [40, 60])
        self.assertLessEqual(report["tactics"]["single_position_cap_pct"], 25)
        self.assertEqual(
            next(item for item in report["research_hypotheses"] if item["id"] == "H2")["status"],
            "awaiting_breadth_confirmation",
        )
        self.assertIn("禁止满仓", "。".join(report["tactics"]["red_lines"]))
        self.assertIn("极端可以持续", next(
            item["interpretation"] for item in report["strategy_factors"]
            if item["id"] == "crowd_extreme_score"
        ))

    def test_missing_evidence_blocks_directional_deployment(self):
        context = {
            "generated_at": "2026-06-20T12:00:00+08:00",
            "stock_codes": ["600519"],
            "stocks": {"quotes": [], "quote_metadata": {}, "daily_bars": {}},
            "source_audit": [{"name": name, "available": False} for name in SOURCE_NAMES],
        }

        report = self.service.analyze_context(
            "600519能买吗",
            context,
            regime={"regime": "未知", "bias": "neutral", "confidence": 0},
        )

        self.assertEqual(report["data_audit"]["grade"], "不足")
        self.assertEqual(report["data_audit"]["decision_gate"], "observe_only")
        self.assertEqual(report["tactics"]["action"], "observe")
        self.assertEqual(report["tactics"]["total_position_range_pct"], [0, 0])
        self.assertIsNone(report["tactics"]["stop_loss"]["percent"])
        self.assertIsNone(report["tactics"]["time_stop_days"])
        self.assertIn("数据完整性", report["main_contradiction"]["title"])
        self.assertEqual(report["cycle"]["stage"], "unknown")
        self.assertIsNone(next(
            item for item in report["strategy_factors"]
            if item["id"] == "supply_exhaustion_score"
        )["score"])
        self.assertIsNone(next(
            item for item in report["strategy_factors"]
            if item["id"] == "breakout_confirmation_score"
        )["score"])

    def test_cached_sources_are_not_upgraded_to_realtime(self):
        context = _complete_context()
        for source in context["source_audit"]:
            source["is_realtime"] = False
            source["cache_used"] = True
        context["stocks"]["quote_metadata"]["is_realtime"] = False
        context["stocks"]["quote_metadata"]["cache_used"] = True

        report = self.service.analyze_context("600519", context, regime={"regime": "震荡市", "bias": "neutral", "confidence": 0.6})

        self.assertFalse(report["data_audit"]["is_realtime"])
        self.assertEqual(report["data_audit"]["data_mode"], "cache_or_history")
        self.assertFalse(report["stock_reports"][0]["is_realtime"])

    def test_text_report_contains_required_structure_and_new_hypotheses(self):
        report = self.service.analyze_context("A股大盘", _complete_context(), regime={"regime": "牛市", "bias": "bullish", "confidence": 0.8})
        rendered = self.service.render_report(report)

        for heading in (
            "【主要矛盾分析】", "【阵营与资金博弈】", "【周期阶段定位】",
            "【五个斗争因子】", "【可证伪研究假设】", "【战术部署与风控红线】", "【闭环复盘】",
        ):
            self.assertIn(heading, rendered)
        self.assertIn("H1 敌疲我打", rendered)
        self.assertIn("H5 主线集中但不重仓赌博", rendered)
        self.assertIn("风控红线：", rendered)
        self.assertEqual(rendered.count("入场前提："), 1)


if __name__ == "__main__":
    unittest.main()
