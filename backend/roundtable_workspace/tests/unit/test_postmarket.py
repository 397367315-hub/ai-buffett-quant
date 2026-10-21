"""盘后复盘（规则版）：数字来自快照、缺的写「未提供」、不调用模型。

用一份最小的合成快照做纯函数测试（不需要服务与网络）。
"""
from __future__ import annotations

import unittest

from tests.support.harness import Harness  # noqa: F401  （设置 sys.path）

from panel.services import postmarket_service as pm


def snap(**over) -> dict:
    base = {
        "snapshot_id": "snap_unit_1",
        "metadata": {"session_date": "2026-10-19", "quote_date": "2026-10-19", "quality": "partial",
                     "freshness": "unknown", "freshness_reason": "calendar_does_not_cover_today",
                     "source_mode": "file_import", "is_synthetic": False, "missing_fields": ["index_daily"],
                     "universe_policy_id": "environment_cn_a_v1"},
        "metrics": [
            {"metric_id": "breadth_member_count", "symbol": None, "value": "5213", "unit": "count"},
            {"metric_id": "breadth_quoted_count", "symbol": None, "value": "5213", "unit": "count"},
            {"metric_id": "breadth_up_count", "symbol": None, "value": "2202", "unit": "count"},
            {"metric_id": "breadth_down_count", "symbol": None, "value": "2861", "unit": "count"},
            {"metric_id": "breadth_up_ratio", "symbol": None, "value": "0.42240552465", "unit": "ratio"},
            {"metric_id": "breadth_median_pct_chg", "symbol": None, "value": "-0.003182461103", "unit": "ratio"},
            {"metric_id": "limit_up_count", "symbol": None, "value": "47", "unit": "count"},
            {"metric_id": "limit_down_count", "symbol": None, "value": "24", "unit": "count"},
            {"metric_id": "broken_board_count", "symbol": None, "value": "23", "unit": "count"},
            {"metric_id": "broken_board_ratio", "symbol": None, "value": "0.328571428571", "unit": "ratio"},
            {"metric_id": "max_consecutive_boards", "symbol": None, "value": "3", "unit": "count"},
            {"metric_id": "consecutive_board_count", "symbol": None, "value": "11", "unit": "count"},
            {"metric_id": "prev_limit_up_member_count", "symbol": None, "value": "61", "unit": "count"},
            {"metric_id": "prev_limit_up_quoted_count", "symbol": None, "value": "61", "unit": "count"},
            {"metric_id": "prev_limit_up_followthrough", "symbol": None, "value": "0.004117472186", "unit": "ratio"},
            {"metric_id": "total_amount_minor", "symbol": None, "value": "143733668790000", "unit": "money_minor"},
            {"metric_id": "amount_vs_20d_avg", "symbol": None, "value": "0.834049361357", "unit": "multiple"},
            {"metric_id": "active_amount_count", "symbol": None, "value": "2587", "unit": "count"},
            {"metric_id": "abs_pct_chg_median", "symbol": None, "value": "0.013927576602", "unit": "ratio"},
            {"metric_id": "big_move_share", "symbol": None, "value": "0.071360061385", "unit": "ratio"},
            {"metric_id": "st_limit_up_count", "symbol": None, "value": None, "unit": "count"},
            {"metric_id": "limit_up_industry_count", "symbol": None, "value": None, "unit": "count"},
        ],
        "instruments": [], "daily_bars": [],
    }
    base.update(over)
    return base


class TestPostMarket(unittest.TestCase):
    def test_sections_and_numbers(self):
        r = pm.build(snap(), generated_at="2026-10-20T00:00:00Z")
        self.assertEqual(r["schema_version"], "postmarket_report_v1")
        ids = [s["id"] for s in r["sections"]]
        self.assertEqual(ids, ["index", "breadth", "sentiment", "followthrough", "liquidity", "volatility", "leaders",
                              "sector_funds"])
        funds = next(s for s in r["sections"] if s["id"] == "sector_funds")
        labels = [i["label"] for i in funds["items"]]
        self.assertIn("北向资金", labels)
        north = next(i for i in funds["items"] if i["label"] == "北向资金")
        self.assertEqual(north["value"], "未提供")
        self.assertIn("northbound_net_minor", north["note"])   # 缺什么字段写清楚，不拿别的数凑
        breadth = next(s for s in r["sections"] if s["id"] == "breadth")
        self.assertEqual(breadth["items"][0]["value"], "2202")
        self.assertEqual(breadth["items"][0]["metric_id"], "breadth_up_count")
        self.assertEqual(breadth["items"][2]["value"], "150")              # 平盘 = 5213-2202-2861
        self.assertEqual(breadth["items"][3]["value"], "42.24%")
        liq = next(s for s in r["sections"] if s["id"] == "liquidity")
        self.assertEqual(liq["items"][0]["value"], "14,373.37 亿")
        self.assertIn("0.834", liq["items"][1]["value"])
        # 缺的指标写未提供，不是 0
        sent = next(s for s in r["sections"] if s["id"] == "sentiment")
        st = next(i for i in sent["items"] if i["label"].startswith("ST 涨停"))
        self.assertEqual(st["value"], "未提供")
        self.assertFalse(st["available"])

    def test_tone_and_risks(self):
        r = pm.build(snap())
        self.assertEqual(r["summary"]["tone"], "broad_weak")                # 42% 上涨
        self.assertTrue(any("最高连板只有 3 板" in x for x in r["risks"]))
        # 宽度偏强 + 涨停多的情况
        strong = snap()
        for m in strong["metrics"]:
            if m["metric_id"] == "breadth_up_ratio":
                m["value"] = "0.65"
            if m["metric_id"] == "limit_up_count":
                m["value"] = "72"
            if m["metric_id"] == "broken_board_ratio":
                m["value"] = "0.18"
        r2 = pm.build(strong)
        self.assertEqual(r2["summary"]["tone"], "broad_strong")
        self.assertTrue(any("情绪偏热" in x for x in r2["watchpoints"]))

    def test_gaps_and_no_model(self):
        r = pm.build(snap())
        items = {g["item"] for g in r["gaps"]}
        self.assertIn("两融余额与变动", items)
        self.assertIn("行业 / 概念分组", items)                             # 本快照没有行业映射
        self.assertFalse(r["generator"]["model_called"])
        md = r["markdown"]
        self.assertIn("# A 股盘后复盘 · 2026-10-19", md)
        self.assertIn("14,373.37 亿", md)
        self.assertIn("未接入", md)
        self.assertIn("不构成买卖建议", md)

    def test_missing_metric_is_not_zero(self):
        s = snap()
        s["metrics"] = [m for m in s["metrics"] if m["metric_id"] != "limit_up_count"]
        r = pm.build(s)
        sent = next(x for x in r["sections"] if x["id"] == "sentiment")
        lu = next(i for i in sent["items"] if i["label"].startswith("涨停家数"))
        self.assertEqual(lu["value"], "未提供")


if __name__ == "__main__":
    unittest.main()
