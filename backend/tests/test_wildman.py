import unittest

from wildman.rules import RULE_VERSION, WildmanRuleCore, review_metrics


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
            "independent_theme_leadership": True,
            "early_theme_start": True,
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
            fundamentals_clear=True,
            ma5_cross_ma10=True, pullback_ma10_ma20=True, volume_contract=True,
            stabilizing_candle=True, ma20=9.6, pressure_price=12,
        )
        result = self.core.evaluate(self.main_rise_market(), self.confirmed_theme(), stock, {"two_pullbacks_hold": True})
        self.assertEqual(result["role"]["role"], "核心中军")
        self.assertEqual(result["setup"]["type"], "SWING_PULLBACK")

    def test_swing_setup_waits_for_explicit_fundamentals_clear(self):
        stock = self.space_dragon(
            consecutive_limit_days=0, emotion_benchmark=False, market_cap=20_000_000_000,
            trend_intact=True, weekly_monthly_bottom=True, ma20_turning_up=True,
            ma5_cross_ma10=True, pullback_ma10_ma20=True, volume_contract=True,
            stabilizing_candle=True, ma20=9.6, pressure_price=12,
        )
        result = self.core.evaluate(self.main_rise_market(), self.confirmed_theme(), stock, {"two_pullbacks_hold": True})
        self.assertEqual(result["setup"]["type"], "NONE")
        self.assertIsNone(stock.get("fundamentals_clear"))

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
            {"available": True, "provider": "numcat", "data_quality": {"status": "COMPLETE"}, "summary": {"available": True, "absorption": {"buy": {"value": 78}}, "replenishment": {"bid": {"value": 70}}, "obi": {"value": .22}, "distribution": {"value": 20}}},
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

    def test_missing_market_numbers_are_unknown_not_ice(self):
        result = self.core.market_cycle.detect({})
        self.assertEqual(result["cycle"], "待确认")
        self.assertFalse(result["confirmed"])
        self.assertEqual(result["data_quality"]["status"], "UNKNOWN")
        self.assertIsNone(result["evidence"][0]["actual"])

    def test_partial_nextday_mainline_evidence_stays_unknown(self):
        theme = self.confirmed_theme(support_promotion=None)
        result = self.core.mainline.evaluate(theme)
        self.assertIsNone(result["steps"]["step4"])
        self.assertNotEqual(result["state"], "核心主线确认")
        self.assertIn("次日溢价验证", result["missing"])

    def test_expectation_without_auction_is_pending(self):
        result = self.core.expectation.evaluate({"yesterday_divergence": True})
        self.assertEqual(result["state"], "待确认")
        self.assertFalse(result["confirmed"])

    def test_weak_to_strong_is_restricted_to_main_rise_core(self):
        stock = self.space_dragon(
            yesterday_divergence=True, auction_pct=3, auction_volume_strength=True,
            open_volume_attack=True, intraday_anchor=10.1, first_volume_divergence=False,
        )
        market = self.main_rise_market(max_limit_height=2, ladder_complete=False, core_midcap_support=False, new_theme_first_boards=2)
        result = self.core.evaluate(market, self.confirmed_theme(), stock, {"two_pullbacks_hold": True})
        self.assertNotEqual(result["setup"]["type"], "WEAK_TO_STRONG")
        self.assertFalse(result["position"]["opening_recommendation"])

    def test_rejected_candidate_returns_observation_not_opening_size(self):
        result = self.core.evaluate(
            self.main_rise_market(), self.confirmed_theme(), self.space_dragon(fake_breakout=True),
            {"two_pullbacks_hold": True}, account={"consecutive_stops": 3},
        )
        self.assertEqual(result["candidate_status"], "风险否决")
        self.assertEqual(result["position"]["allocation_type"], "observation")
        self.assertFalse(result["position"]["opening_recommendation"])
        self.assertEqual(result["position"]["range"], "0")
        self.assertEqual(result["position"]["reference_range"], "0~5%")

    def test_independent_leadership_is_required_for_space_dragon(self):
        stock = self.space_dragon(independent_theme_leadership=None)
        result = self.core.role.classify(stock, self.confirmed_theme(), self.main_rise_market())
        self.assertNotEqual(result["role"], "空间龙/总龙头")
        leadership = next(item for item in result["evidence"] if item["rule_id"] == "WM_ROLE_LEADERSHIP")
        self.assertIsNone(leadership["passed"])

    def test_same_day_pioneer_flag_does_not_prove_high_board_early_start(self):
        stock = self.space_dragon(early_theme_start=None, first_limit_pioneer=True)
        result = self.core.role.classify(stock, self.confirmed_theme(), self.main_rise_market())
        self.assertEqual(result["role"], "待确认")
        timing = next(item for item in result["evidence"] if item["rule_id"] == "WM_ROLE_TIMING")
        self.assertIsNone(timing["passed"])

    def test_supplement_requires_explicit_non_independent_leadership(self):
        stock = {
            "consecutive_limit_days": 2, "leader_height": 5, "theme_linkage": False,
            "emotion_benchmark": False, "supplement_started_after_leader": True,
            "independent_theme_leadership": False, "stock_start_date": "2026-09-18",
            "leader_established_date": "2026-09-17",
            "fact_basis": {"timing": "relative first_limit_time proxy; not causation"},
        }
        result = self.core.role.classify(stock, {}, self.main_rise_market())
        self.assertEqual(result["role"], "补涨龙")
        self.assertEqual(len(result["comparison"]), 5)
        self.assertEqual(result["comparison"][0]["actual"]["stock_start_date"], "2026-09-18")
        self.assertEqual(result["fact_basis"]["timing"], "relative first_limit_time proxy; not causation")

    def test_incomplete_level2_cannot_confirm_full_support(self):
        result = self.core.support.evaluate(
            {"two_pullbacks_hold": True},
            {"available": True, "provider": "numcat", "data_quality": {"status": "DEGRADED"}, "summary": {
                "absorption": {"buy": {"value": 78}}, "replenishment": {"bid": {"value": 70}},
                "obi": {"value": .22}, "distribution": {"value": 20},
            }},
        )
        self.assertEqual(result["state"], "待确认")
        self.assertIsNone(next(item for item in result["evidence"] if item["rule_id"] == "WM_SUPPORT_L2")["passed"])

    def test_classic_requires_scanner_fact_not_bar_length(self):
        self.assertEqual(self.core.classic.evaluate({"arc_days": 60, "ma75_turning_up": True, "break_neckline": True}), [])
        result = self.core.classic.evaluate({"classic_75a_confirmed": True})
        self.assertEqual(result[0]["id"], "WM_CLASSIC_75A")

    def test_review_metrics_use_sorted_closed_trades_and_ignore_null_pnl(self):
        result = review_metrics([
            {"entry_date": "2026-09-06", "pnl_pct": -99, "mode_inside": True},
            {"exit_date": "2026-09-04", "pnl_pct": None, "mode_inside": False, "setup_type": "B_POINT"},
            {"exit_date": "2026-09-05", "pnl_pct": 4, "mode_inside": True, "setup_type": "B_POINT"},
            {"exit_date": "2026-09-02", "pnl_pct": -3, "mode_inside": False, "setup_type": "WTS"},
            {"exit_date": "2026-09-03", "pnl_pct": -2, "mode_inside": True, "setup_type": "WTS"},
            {"exit_date": "2026-09-01", "pnl_pct": -1, "mode_inside": False, "setup_type": "WTS"},
        ])
        self.assertEqual(result["total"], 6)
        self.assertEqual(result["closed_total"], 5)
        self.assertEqual(result["sample_count"], 4)
        self.assertEqual(result["max_consecutive_losses"], 3)
        self.assertEqual(result["outside_loss_share"], 66.7)
        self.assertEqual(result["expectancy"], -0.5)
        self.assertEqual(result["per_setup"]["WTS"]["sample_count"], 3)
        self.assertIn("不阻断扫描", result["per_setup"]["WTS"]["guidance"])
        self.assertEqual(RULE_VERSION, "WM_RULE_CORE_V1_1")


if __name__ == "__main__":
    unittest.main()
