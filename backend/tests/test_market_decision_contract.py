import unittest
from datetime import date

from services.market_decision_contract import (
    WORKBENCH_CONTRACT_VERSION,
    evaluate_market_execution_gate,
)


DECISION_DATE = date(2026, 8, 13)


def decision_payload(
    *,
    action: str = "execute",
    weight_pct: object = 50,
    contract_version: str = WORKBENCH_CONTRACT_VERSION,
    decision_date: str = "2026-08-13",
    available: object = True,
) -> dict:
    return {
        "available": available,
        "meta": {
            "contract_version": contract_version,
            "decision_date": decision_date,
        },
        "market_cognition": {"final_action": action},
        "adaptive_strategy_weights": {
            "weights": [{"strategy_id": "tail_1455", "weight_pct": weight_pct}],
        },
    }


class MarketDecisionContractTests(unittest.TestCase):
    def evaluate(self, payload: dict | None) -> dict:
        return evaluate_market_execution_gate(
            payload,
            decision_date=DECISION_DATE,
            strategy_id="overnight_review_v2",
            requires_auction_confirmation=False,
        )

    def test_missing_or_non_boolean_available_snapshot_fails_closed(self):
        self.assertTrue(self.evaluate(None)["blocked"])
        self.assertTrue(self.evaluate(decision_payload(available="true"))["blocked"])

    def test_old_contract_fails_closed(self):
        gate = self.evaluate(decision_payload(contract_version="market-workbench-v2.0.1"))

        self.assertTrue(gate["blocked"])
        self.assertIn("契约已过期", gate["reason"])

    def test_cross_day_snapshot_fails_closed(self):
        gate = self.evaluate(decision_payload(decision_date="2026-08-12"))

        self.assertTrue(gate["blocked"])
        self.assertIn("策略信号日不一致", gate["reason"])

    def test_observe_and_no_trade_block_new_positions(self):
        for action in ("observe", "no_trade"):
            with self.subTest(action=action):
                gate = self.evaluate(decision_payload(action=action))
                self.assertTrue(gate["available"])
                self.assertTrue(gate["blocked"])

    def test_caution_with_positive_weight_is_allowed(self):
        gate = self.evaluate(decision_payload(action="caution", weight_pct=25))

        self.assertFalse(gate["blocked"])
        self.assertEqual(gate["weight_pct"], 25)
        self.assertIn("允许caution", gate["reason"])

    def test_zero_or_non_finite_weight_fails_closed(self):
        for weight in (0, "nan", "inf"):
            with self.subTest(weight=weight):
                gate = self.evaluate(decision_payload(weight_pct=weight))
                self.assertTrue(gate["blocked"])

    def test_missing_matching_strategy_weight_fails_closed(self):
        payload = decision_payload()
        payload["adaptive_strategy_weights"]["weights"] = [
            {"strategy_id": "auction_confirmation", "weight_pct": 50},
        ]

        gate = self.evaluate(payload)

        self.assertTrue(gate["blocked"])
        self.assertIn("动态权重缺失", gate["reason"])


if __name__ == "__main__":
    unittest.main()
