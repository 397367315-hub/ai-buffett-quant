import unittest
from datetime import datetime
from unittest.mock import AsyncMock, patch

from services.macro_policy_news import MacroPolicyNewsCollector


class MacroPolicyNewsTests(unittest.IsolatedAsyncioTestCase):
    def test_policy_parser_preserves_source_date_and_absolute_url(self):
        document = '''
            <a href="./policy/example.html" title="关于促进半导体产业发展的行动计划">ignored</a>
            <span>2026-07-30</span>
        '''

        items = MacroPolicyNewsCollector._parse_policy_items(
            document,
            "中国政府网",
            "https://www.gov.cn/zhengce/",
        )

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["title"], "关于促进半导体产业发展的行动计划")
        self.assertEqual(items[0]["published_at"], "2026-07-30")
        self.assertEqual(items[0]["url"], "https://www.gov.cn/zhengce/policy/example.html")

    def test_policy_and_announcement_impacts_are_bounded_and_keyword_based(self):
        collector = MacroPolicyNewsCollector()
        policy_delta, policy_impact, matched = collector.policy_impact(
            "关于支持半导体产业发展的行动计划",
            collector.sector_terms("半导体"),
        )
        positive_delta, positive_impact = collector.announcement_impact("关于股份回购暨重大合同中标的公告")
        negative_delta, negative_impact = collector.announcement_impact("关于立案调查及风险提示的公告")

        self.assertEqual((policy_delta, policy_impact), (6.0, "positive"))
        self.assertIn("半导体", matched)
        self.assertEqual((positive_delta, positive_impact), (7.0, "positive"))
        self.assertEqual((negative_delta, negative_impact), (-8.0, "negative"))

    def test_empty_context_explicitly_disables_news_scoring(self):
        context = MacroPolicyNewsCollector.empty_context()

        self.assertFalse(context["available"])
        self.assertEqual(context["macro_adjustment"], 0.0)
        self.assertIn("不计入", context["summary"])

    async def test_ftshare_announcements_are_used_only_when_eastmoney_fails(self):
        collector = MacroPolicyNewsCollector()
        fallback_rows = [{
            "announcement_title": "贵州茅台2025年年度权益分派实施公告",
            "announcement_time": "2026-07-29 00:00:00",
            "column_type": "stock",
            "url_hash": "a" * 64,
        }]
        with patch(
            "services.macro_policy_news.collector.fetch_json",
            new=AsyncMock(side_effect=RuntimeError("proxy unavailable")),
        ), patch(
            "services.macro_policy_news.ftshare_mcp_client.get_stock_announcements",
            new=AsyncMock(return_value=fallback_rows),
        ):
            announcements = await collector._get_stock_announcements("600519")

        self.assertEqual(len(announcements), 1)
        self.assertEqual(announcements[0]["source"], "FTShare MCP 公告")
        self.assertEqual(announcements[0]["published_at"], "2026-07-29")
        self.assertIn("a" * 64, announcements[0]["url"])
        self.assertTrue(collector._announcement_status_cache["600519"][1]["available"])
        self.assertEqual(collector._announcement_status_cache["600519"][1]["source"], "ftshare_mcp")

    async def test_failed_announcement_sources_are_not_treated_as_no_negative_news(self):
        collector = MacroPolicyNewsCollector()
        with patch(
            "services.macro_policy_news.collector.fetch_json",
            new=AsyncMock(side_effect=RuntimeError("proxy unavailable")),
        ), patch(
            "services.macro_policy_news.ftshare_mcp_client.get_stock_announcements",
            new=AsyncMock(side_effect=RuntimeError("fallback unavailable")),
        ):
            result = await collector.get_stock_announcements_audit(["600519"], max_stocks=1)

        self.assertEqual(result["announcements"], {"600519": []})
        self.assertFalse(result["status"]["600519"]["available"])
        self.assertEqual(result["covered"], 0)

    async def test_numcat_announcement_is_used_before_ftshare_when_configured(self):
        collector = MacroPolicyNewsCollector()
        with patch(
            "services.macro_policy_news.collector.fetch_json",
            new=AsyncMock(side_effect=RuntimeError("proxy unavailable")),
        ), patch(
            "services.macro_policy_news.settings.level2_enabled",
            True,
        ), patch(
            "services.macro_policy_news.settings.meoz_api_key",
            "test-key",
        ), patch(
            "services.macro_policy_news.numcat_market_provider.announcements",
            new=AsyncMock(return_value=[{
                "symbol": "600519",
                "event_date": "2026-08-29",
                "title": "贵州茅台年度报告",
                "summary": "年度报告摘要",
                "announcement_type": "financial_report",
                "content_url": "https://example.test/notice",
            }]),
        ), patch(
            "services.macro_policy_news.ftshare_mcp_client.get_stock_announcements",
            new=AsyncMock(),
        ) as ftshare:
            announcements = await collector._get_stock_announcements("600519")

        self.assertEqual(announcements[0]["source"], "猫爪公司公告")
        self.assertEqual(announcements[0]["published_at"], "2026-08-29")
        self.assertEqual(collector._announcement_status_cache["600519"][1]["source"], "numcat")
        ftshare.assert_not_awaited()

    def test_recent_check_uses_shanghai_calendar_date(self):
        collector = MacroPolicyNewsCollector()
        with patch("services.macro_policy_news.shanghai_now", return_value=datetime(2026, 7, 31)):
            self.assertTrue(collector.is_recent("2026-07-01"))
            self.assertFalse(collector.is_recent("2026-04-01"))
