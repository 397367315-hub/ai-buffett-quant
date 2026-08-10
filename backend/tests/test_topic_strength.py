import unittest
from datetime import date
from unittest.mock import AsyncMock, patch

from services.topic_strength import (
    TopicStrengthService,
    _aggregate_daily_rows,
    _topic_date_metadata,
)


class TopicStrengthTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.service = TopicStrengthService()

    def test_weekly_kline_aggregation_preserves_ohlcv(self):
        rows = [
            {"date": "2026-08-03", "open": 10, "close": 11, "high": 11.2, "low": 9.8, "volume": 100, "amount": 1000},
            {"date": "2026-08-04", "open": 11, "close": 10.5, "high": 11.5, "low": 10.2, "volume": 200, "amount": 2200},
            {"date": "2026-08-10", "open": 10.7, "close": 12, "high": 12.2, "low": 10.6, "volume": 300, "amount": 3600},
        ]

        result = _aggregate_daily_rows(rows, 5)

        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]["date"], "2026-08-04")
        self.assertEqual(result[0]["open"], 10)
        self.assertEqual(result[0]["close"], 10.5)
        self.assertEqual(result[0]["high"], 11.5)
        self.assertEqual(result[0]["low"], 9.8)
        self.assertEqual(result[0]["volume"], 300)
        self.assertAlmostEqual(result[1]["change_pct"], 14.2857, places=4)

    def test_topic_date_metadata_matches_the_snapshot_displayed_by_the_page(self):
        class CacheRow:
            def __init__(self, key, payload):
                self.key = key
                self.payload = payload

        rows = [
            CacheRow(
                "topic_strength_v1:2026-08-10",
                {
                    "data_date": "2026-08-10",
                    "market": {
                        "emotion": {"zt_count": 99, "zb_count": 14},
                        "sentiment": {"total": 5459},
                    },
                },
            ),
            CacheRow(
                "topic_strength_v1:2026-08-09",
                {"data_date": "2026-08-08", "market": {}},
            ),
        ]

        result = _topic_date_metadata(rows)

        self.assertEqual(result[date(2026, 8, 10)]["limit_up_count"], 99)
        self.assertEqual(result[date(2026, 8, 10)]["failed_limit_count"], 14)
        self.assertEqual(result[date(2026, 8, 10)]["stock_count"], 5459)
        self.assertNotIn(date(2026, 8, 8), result)

    def test_linked_topic_requires_observed_breadth_before_strong_label(self):
        limit_rows = [
            {"code": "600001", "name": "龙头", "sector": "电子", "continuous_days": 2, "change_pct": 10, "amount": 500, "turnover": 12},
            {"code": "600002", "name": "跟随", "sector": "电子", "continuous_days": 1, "change_pct": 10, "amount": 300, "turnover": 8},
        ]
        history = {
            "600001": {"return_5d_pct": 12},
            "600002": {"return_5d_pct": 8},
        }

        missing_breadth = self.service._build_topics(limit_rows, [], [], history, set())
        complete = self.service._build_topics(
            limit_rows,
            [
                {"code": "600001", "sector": "电子", "change_pct": 10},
                {"code": "600002", "sector": "电子", "change_pct": 10},
                {"code": "600003", "sector": "电子", "change_pct": -1},
            ],
            [{"code": "BK1", "name": "电子", "main_net_inflow": 1_000_000, "up_count": 2, "down_count": 1}],
            history,
            set(),
        )

        self.assertEqual(missing_breadth[0]["status"], "观察")
        self.assertIn("板块上涨宽度", missing_breadth[0]["audit"]["gaps"])
        self.assertEqual(complete[0]["status"], "强")
        self.assertEqual(complete[0]["leader"]["code"], "600001")
        self.assertEqual(complete[0]["sector_flow_rank"], 1)
        self.assertEqual(complete[0]["novelty"], "新出现")

    def test_missing_heat_fields_are_not_treated_as_passed(self):
        topics = self.service._build_topics(
            [{"code": "600001", "name": "待核验", "sector": "电子", "change_pct": 10}],
            [],
            [],
            {},
            None,
        )

        stock = topics[0]["leader"]
        self.assertEqual(stock["heat_status"], "待核验")
        self.assertIsNone(stock["overheated"])
        self.assertIn("近5日涨幅", stock["data_gaps"])

    def test_market_break_rate_uses_limit_and_failed_pool_counts(self):
        market = self.service._market_payload(
            {},
            [{"code": "600001", "change_pct": 1}, {"code": "600002", "change_pct": -1}],
            {"total": 80, "trade_date": "2026-08-07", "verified": True, "stocks": [{"code": "600001"}]},
            {"total": 5, "trade_date": "2026-08-07", "verified": True, "stocks": []},
            {"total": 20, "trade_date": "2026-08-07", "verified": True, "stocks": [{"code": "600003"}]},
            [],
        )

        self.assertEqual(market["sentiment"]["breadth"], "分化")
        self.assertEqual(market["emotion"]["zt_count"], 80)
        self.assertEqual(market["emotion"]["break_rate"], 20.0)

    def test_wrong_date_pool_is_discarded_and_never_labeled_verified(self):
        pool = self.service._verified_pool(
            {"total": 80, "trade_date": "20260806", "stocks": [{"code": "600001"}]},
            date(2026, 8, 7),
        )
        market = self.service._market_payload(
            {"limit_up_count": 72, "failed_limit_count": 18, "source": "database_cache"},
            [],
            pool,
            self.service._verified_pool({}, date(2026, 8, 7)),
            self.service._verified_pool({}, date(2026, 8, 7)),
            [],
        )

        self.assertFalse(pool["verified"])
        self.assertEqual(pool["stocks"], [])
        self.assertIsNone(pool["total"])
        self.assertEqual(market["emotion"]["zt_count"], 72)
        self.assertEqual(market["emotion"]["source"], "database_cache")

    async def test_closed_session_cache_short_circuits_upstream_calls(self):
        cached = {
            "available": True,
            "data_date": "2026-08-07",
            "updated_at": "2026-08-07T15:10:00+08:00",
            "topics": [],
        }
        with (
            patch.object(self.service, "_resolve_date", new=AsyncMock(return_value=date(2026, 8, 7))),
            patch.object(self.service, "_read_cache", new=AsyncMock(return_value=cached)),
            patch("services.topic_strength.is_a_share_market_session", return_value=False),
            patch.object(self.service, "_cached_sentiment", new=AsyncMock(side_effect=AssertionError("cache should short circuit"))),
        ):
            result = await self.service.get()

        self.assertTrue(result["cache_hit"])
        self.assertEqual(result["source"], "database_cache")
        self.assertFalse(result["is_realtime"])

    async def test_kline_rejects_unknown_category_without_network(self):
        with self.assertRaisesRegex(ValueError, "category"):
            await self.service.kline("600519", category=9, offset=60)


if __name__ == "__main__":
    unittest.main()
