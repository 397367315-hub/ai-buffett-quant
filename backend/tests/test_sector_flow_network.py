import unittest

from services.sector_flow_network import build_inferred_transfers


class SectorFlowNetworkTests(unittest.TestCase):
    def test_links_preserve_visible_flow_and_mark_inference(self):
        result = build_inferred_transfers(
            [
                {"code": "I1", "name": "半导体", "main_net_inflow": 300},
                {"code": "I2", "name": "通信设备", "main_net_inflow": 100},
            ],
            [
                {"code": "O1", "name": "房地产", "main_net_inflow": -250},
                {"code": "O2", "name": "银行", "main_net_inflow": -80},
            ],
        )

        links = result["transfers"]
        sector_links = [link for link in links if link["source"]["type"] == "outflow"]
        new_money_links = [link for link in links if link["source"]["type"] == "new_money"]
        self.assertEqual(len(sector_links), 4)
        self.assertEqual(len(new_money_links), 2)
        self.assertTrue(all(link["inferred"] for link in links))
        self.assertEqual(result["inference"]["paired_amount"], 330)
        self.assertEqual(sum(link["amount"] for link in sector_links), 330)

    def test_residual_market_exit_is_exposed_when_outflow_is_larger(self):
        result = build_inferred_transfers(
            [{"code": "I1", "name": "电子", "main_net_inflow": 100}],
            [{"code": "O1", "name": "银行", "main_net_inflow": -180}],
        )

        self.assertTrue(any(link["target"]["type"] == "market_exit" for link in result["transfers"]))
        self.assertEqual(result["inference"]["outflow_total"], 180)

    def test_residual_new_money_is_exposed_when_inflow_is_larger(self):
        result = build_inferred_transfers(
            [{"code": "I1", "name": "电子", "main_net_inflow": 180}],
            [{"code": "O1", "name": "银行", "main_net_inflow": -100}],
        )

        self.assertTrue(any(link["source"]["type"] == "new_money" for link in result["transfers"]))
        self.assertEqual(result["inference"]["inflow_total"], 180)


if __name__ == "__main__":
    unittest.main()
