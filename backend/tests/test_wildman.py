import unittest

from wildman.rules import WildmanRuleCore


class WildmanRuleCoreTests(unittest.TestCase):
    def setUp(self):
        self.core = WildmanRuleCore()

    @staticmethod
    def main_rise_market(**updates):
        payload = {
            "max_limit_height": 5,
            "limit_up_count": 46,
            "limit_down_count": 2,
            "new_theme_first_boards": 5,
            "ladder_complete": True,
            "core_midcap_support": True,
            "first_divergence": True,
            "leader_nuked": False,
            "mid_level_limit_down_spread": False,
            "one_word_limit_count": 0,
            "leader_volume_acceleration": False,
            "rear_all_red": False,
        }
        payload.update(updates)
        return payload

    @staticmethod
    def confirmed_theme(**updates):
        payload = {
            "theme_name": "机器人", "limit_up_count": 7, "max_limit_height": 5,
            "has_pioneer": True, "has_core_midcap": True, "old_dragon_active": True,
            "leader_premium": True, "support_promotion": True, "core_midcap_stable": True,
            "fund_return": True,
        }
        payload.update(updates)
        return payload

    @staticmethod
    def space_dragon(**updates):
        payload = {
            "symbol": "000001", "name": "测试龙头", "close_price": 10,
            "low_price": 9.5, "market_cap": 8_000_000_000,
            "consecutive_limit_days": 5, "theme_linkage": True,
            "emotion_benchmark": True, "first_volume_divergence": True,
            "divergence_low": 9.5, "pressure_price": 12.5,
        }
        payload.update(updates)
        return payload

    def test_retreat_rejects_every_new_setup(self):
        market = self.main_rise_market(leader_nuked=True, mid_level_limit_down_spread=True, limit_down_count=18)
        result = self.core.evaluate(market, self.confirmed_theme(), self.space_dragon(), {"two_pullbacks_hold": True})
        self.assertEqual(result["cycle"]["cycle"], "退潮")
        self.assertEqual(result["position"]["range"], "0")
        self.assertIn("RETREAT_MARKET", result["risk"]["flags"])
        self.assertEqual(result["candidate_status"], "风险否决")

    def test_first_divergence_waits_or_becomes_ready_with_anchor(self):
        result = self.core.evaluate(self.main_rise_market(), self.confirmed_theme(), self.space_dragon(), {"two_pullbacks_hold": True, "lows_rising": True})
        self.assertEqual(result["setup"]["type"], "FIRST_DIVERGENCE")
        self.assertIn(result["candidate_status"], {"等待确认", "模式条件成立"})
        self.assertEqual(result["setup"]["anchor_price"], 9.5)

    def test_weak_to_strong(self):
        stock = self.space_dragon(yesterday_divergence=True, auction_pct=3, auction_volume_strength=True, open_volume_attack=True, intraday_anchor=10.1, first_volume_divergence=False)
        result = self.core.evaluate(self.main_rise_market(), self.confirmed_theme(), stock, {"two_pullbacks_hold": True})
        self.assertEqual(result["setup"]["type"], "WEAK_TO_STRONG")

    def test_should_be_strong_but_is_weak(self):
        result = self.core.expectation.evaluate({"yesterday_strong": True, "open_low": True, "no_support": True})
        self.assertEqual(result["state"], "不及预期")
        self.assertIn("优先处理", result["action"])

    def test_core_midcap_pullback(self):
        stock = self.space_dragon(
            consecutive_limit_days=0, emotion_benchmark=False, market_cap=20_000_000_000,
            trend_intact=True, weekly_monthly_bottom=True, ma20_turning_up=True,
            ma5_cross_ma10=True, pullback_ma10_ma20=True, volume_contract=True,
            stabilizing_candle=True, ma20=9.6, pressure_price=12,
        )
        result = self.core.evaluate(self.main_rise_market(), self.confirmed_theme(), stock, {"two_pullbacks_hold": True})
        self.assertEqual(result["role"]["role"], "核心中军")
        self.assertEqual(result["setup"]["type"], "SWING_PULLBACK")

    def test_fish_tail_is_hard_reject(self):
        market = self.main_rise_market(one_word_limit_count=4, leader_volume_acceleration=True, rear_all_red=True, leader_broken=True, cold_rear_supplement=True)
        result = self.core.evaluate(market, self.confirmed_theme(), self.space_dragon(), {"two_pullbacks_hold": True})
        self.assertIn("FISH_TAIL", result["risk"]["flags"])
        self.assertEqual(result["candidate_status"], "风险否决")

    def test_fake_breakout_is_rejected(self):
        result = self.core.evaluate(self.main_rise_market(), self.confirmed_theme(), self.space_dragon(fake_breakout=True), {"two_pullbacks_hold": True})
        self.assertIn("FAKE_BREAKOUT", result["risk"]["flags"])
        self.assertTrue(result["risk"]["hard_reject"])

    def test_risk_reward_below_two_is_rejected(self):
        stock = self.space_dragon(close_price=10, divergence_low=9, pressure_price=11.33)
        result = self.core.evaluate(self.main_rise_market(), self.confirmed_theme(), stock, {"two_pullbacks_hold": True})
        self.assertLess(result["risk_reward"], 2)
        self.assertIn("RISK_REWARD_FAIL", result["risk"]["flags"])

    def test_three_consecutive_stops_only_reduce_position(self):
        result = self.core.position.suggest("发酵/主升", "FIRST_DIVERGENCE", consecutive_stops=3)
        self.assertEqual(result["range"], "0~5%")
        self.assertIn("继续观察行情", result["reason"])

    def test_mode_outside_intraday_spike_is_rejected(self):
        stock = self.space_dragon(first_volume_divergence=False, divergence_low=None, pressure_price=13, theme_linkage=False, emotion_benchmark=False)
        result = self.core.evaluate(self.main_rise_market(), self.confirmed_theme(), stock, {"lows_rising": True})
        self.assertEqual(result["setup"]["type"], "NONE")
        self.assertIn("MODE_OUTSIDE", result["risk"]["flags"])
        self.assertEqual(result["candidate_status"], "风险否决")

    def test_numcat_level2_can_confirm_support(self):
        support = self.core.support.evaluate(
            {"two_pullbacks_hold": True, "lows_rising": True},
            {"available": True, "provider": "numcat", "summary": {"available": True, "absorption": {"buy": {"value": 78}}, "replenishment": {"bid": {"value": 70}}, "obi": {"value": .22}, "distribution": {"value": 20}}},
        )
        self.assertEqual(support["state"], "良性")
        self.assertTrue(support["level2_available"])

    def test_numcat_level2_weak_bid_replenishment_blocks_confirmation(self):
        support = self.core.support.evaluate(
            {"two_pullbacks_hold": True, "lows_rising": True},
            {"available": True, "provider": "numcat", "summary": {"available": True, "absorption": {"buy": {"value": 82}}, "replenishment": {"bid": {"value": 10}}, "obi": {"value": .2}, "distribution": {"value": 18}}},
        )
        self.assertEqual(support["state"], "差/无承接")

    def test_promoted_leader_fact_can_confirm_mainline_step_four(self):
        theme = self.confirmed_theme(leader_premium=True, support_promotion=True, core_midcap_stable=True)
        result = self.core.mainline.evaluate(theme)
        self.assertTrue(result["steps"]["step4"])
        self.assertEqual(result["state"], "核心主线确认")


if __name__ == "__main__":
    unittest.main()
