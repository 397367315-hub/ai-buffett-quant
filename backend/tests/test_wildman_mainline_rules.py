import unittest

from wildman.rules import MainlineEngine


class MainlineFiveStepRuleTests(unittest.TestCase):
    titles = ["消息级别", "首日异动", "容量中军", "老龙异动", "次日溢价验证"]

    @staticmethod
    def fact(passed):
        return {
            "passed": passed,
            "actual": {"observed": passed},
            "sources": [{"url": "https://example.test/fact"}],
            "reason": "测试事实",
        }

    def theme(self, **updates):
        payload = {
            "candidate_eligible": True,
            "mainline_five_steps": {f"step{index}": self.fact(True) for index in range(1, 6)},
        }
        payload.update(updates)
        return payload

    def test_old_legacy_only_flags_cannot_confirm(self):
        result = MainlineEngine().evaluate({
            "limit_up_count": 20,
            "max_limit_height": 6,
            "has_pioneer": True,
            "has_core_midcap": True,
            "old_dragon_active": True,
            "leader_premium": True,
            "support_promotion": True,
            "core_midcap_stable": True,
            "fund_return": True,
        })
        self.assertFalse(result["confirmed"])
        self.assertEqual(result["state"], "观察")
        self.assertEqual(result["missing"], self.titles)

    def test_missing_any_stage_is_unknown_and_cannot_confirm(self):
        theme = self.theme()
        del theme["mainline_five_steps"]["step3"]
        result = MainlineEngine().evaluate(theme)
        self.assertFalse(result["confirmed"])
        self.assertIsNone(result["steps"]["step3"])
        self.assertEqual(result["missing"], ["容量中军"])
        self.assertEqual(result["failed"], [])
        self.assertEqual(result["next_step"], "容量中军")

    def test_event_and_style_flags_do_not_create_an_eligible_candidate(self):
        theme = self.theme(candidate_eligible=False, event_category=False, style_category=False)
        result = MainlineEngine().evaluate(theme)
        self.assertFalse(result["confirmed"])
        self.assertEqual(result["state"], "观察")
        self.assertEqual(result["passed_count"], 5)

    def test_complete_order_is_the_only_path_to_each_qualification_level(self):
        engine = MainlineEngine()
        expected = {
            0: "观察",
            1: "观察",
            2: "观察",
            3: "候选主线",
            4: "高质量候选",
            5: "核心主线确认",
        }
        for prefix, state in expected.items():
            facts = {f"step{index}": self.fact(index <= prefix) for index in range(1, 6)}
            result = engine.evaluate(self.theme(mainline_five_steps=facts))
            self.assertEqual(result["state"], state)
            self.assertEqual(result["passed_count"], prefix)
            self.assertEqual(result["next_step"], None if prefix == 5 else self.titles[prefix])

        bypass = self.theme(mainline_five_steps={
            "step1": self.fact(False),
            "step2": self.fact(True),
            "step3": self.fact(True),
            "step4": self.fact(True),
            "step5": self.fact(True),
        })
        result = engine.evaluate(bypass)
        self.assertEqual(result["state"], "观察")
        self.assertEqual(result["next_step"], "消息级别")
        self.assertEqual(result["failed"], ["消息级别"])
        self.assertEqual(result["missing"], [])

    def test_evidence_keeps_actual_sources_reason_and_fixed_order(self):
        result = MainlineEngine().evaluate(self.theme())
        self.assertEqual([row["rule_name"] for row in result["evidence"]], self.titles)
        self.assertEqual(result["evidence"][0]["actual"], {"observed": True})
        self.assertEqual(result["evidence"][0]["sources"], [{"url": "https://example.test/fact"}])
        self.assertEqual(result["evidence"][0]["reason"], "测试事实")


if __name__ == "__main__":
    unittest.main()
