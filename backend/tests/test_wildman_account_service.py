import unittest
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from api.wildman_account_routes import router
from database import Base
from services.admin_auth import create_admin_token
from services.wildman_account_service import (
    MAX_HOLDINGS,
    WildmanAccountService,
    account_key,
    wildman_account_service,
)
from config import settings


class WildmanAccountServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        self.session_factory = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.service = WildmanAccountService()
        self.now = datetime(2026, 9, 20, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai"))

    async def asyncTearDown(self):
        await self.engine.dispose()

    def _payload(self, **overrides):
        previous_trading_day = (self.now.date() - timedelta(days=2)).isoformat()
        today = self.now.date().isoformat()
        payload = {
            "as_of": today,
            "source": "manual_user_input",
            "cash": 100000,
            "net_asset_value": 120000,
            "holdings": [{
                "symbol": "600519",
                "name": "贵州茅台",
                "quantity": 100,
                "sellable_quantity": 100,
            }],
            "holding_snapshots": [{
                "as_of": previous_trading_day,
                "source": "manual_user_input",
                "holdings": [{"symbol": "600519", "quantity": 100}],
            }],
            "nav_history": [
                {"as_of": previous_trading_day, "net_asset_value": 110000},
                {"as_of": today, "net_asset_value": 120000},
            ],
        }
        payload.update(overrides)
        return payload

    async def test_save_is_per_user_and_returns_manual_t1_contract(self):
        with (
            patch("services.wildman_account_service.async_session", self.session_factory),
            patch("services.wildman_account_service.shanghai_now", return_value=self.now),
            patch("services.wildman_account_service.numcat_extended_provider.calendar", new_callable=AsyncMock, return_value=[{"pretrade_date": "20260918"}]),
        ):
            account = await self.service.update_account("alice", self._payload())
            loaded = await self.service.get_account("alice", "600519")

        self.assertEqual(account_key("alice"), "wildman_account_v1:" + account_key("alice").split(":", 1)[1])
        self.assertFalse(loaded["broker_verified"])
        self.assertEqual(loaded["verification_status"], "manual_user_input")
        self.assertTrue(loaded["manual_reconciliation_required"])
        self.assertEqual(loaded["t1"]["status"], "passed")
        self.assertEqual(loaded["t1"]["reference_date"], "2026-09-18")
        self.assertEqual(loaded["t1"]["reference_date_source"], "numcat_tradecal")
        self.assertTrue(loaded["target"]["t_allowed"])
        self.assertEqual(loaded["target"]["sellable_quantity"], 100)
        self.assertFalse(loaded["scan_blocked"])

    async def test_today_additions_cannot_be_sold_and_stale_snapshot_is_blocked(self):
        today = self.now.date().isoformat()
        payload = self._payload(holdings=[{
            "symbol": "600519", "quantity": 200, "sellable_quantity": 101,
        }])
        with (
            patch("services.wildman_account_service.async_session", self.session_factory),
            patch("services.wildman_account_service.shanghai_now", return_value=self.now),
        ):
            account = await self.service.update_account("alice", payload)
            self.assertEqual(account["t1"]["status"], "failed")
            self.assertFalse(account["t1"]["t_allowed"])
            self.assertEqual(account["t1"]["violations"][0]["symbol"], "600519.SH")
            with self.assertRaisesRegex(ValueError, "as_of必须是上海当日"):
                await self.service.update_account("alice", {**self._payload(), "as_of": (self.now.date() - timedelta(days=1)).isoformat()})

        # A saved prior-day snapshot remains readable but cannot authorize a T operation.
        with (
            patch("services.wildman_account_service.async_session", self.session_factory),
            patch("services.wildman_account_service.shanghai_now", return_value=self.now),
        ):
            await self.service.update_account("alice", self._payload())
            with patch("services.wildman_account_service.shanghai_now", return_value=self.now + timedelta(days=1)):
                refreshed = await self.service.get_account("alice")
        self.assertEqual(refreshed["t1"]["status"], "stale")
        self.assertFalse(refreshed["t1"]["t_allowed"])

    async def test_missing_history_does_not_pass_t1(self):
        with (
            patch("services.wildman_account_service.async_session", self.session_factory),
            patch("services.wildman_account_service.shanghai_now", return_value=self.now),
        ):
            account = await self.service.update_account("alice", {
                **self._payload(),
                "holding_snapshots": [],
            })
        self.assertEqual(account["t1"]["status"], "insufficient_history")
        self.assertFalse(account["t1"]["t_allowed"])

    async def test_manual_previous_quantity_and_bought_today_are_sufficient(self):
        with (
            patch("services.wildman_account_service.async_session", self.session_factory),
            patch("services.wildman_account_service.shanghai_now", return_value=self.now),
        ):
            account = await self.service.update_account("alice", {
                **self._payload(holding_snapshots=[]),
                "holdings": [{
                    "symbol": "600519.SH",
                    "quantity": 120,
                    "sellable_quantity": 100,
                    "previous_quantity": 100,
                    "bought_today_shares": 20,
                }],
            })
        self.assertEqual(account["holdings"][0]["symbol"], "600519.SH")
        self.assertEqual(account["t1"]["validation_mode"], "manual_input")
        self.assertTrue(account["t1"]["t_allowed"])

    async def test_symbols_are_normalised_to_six_digits_and_market_suffix(self):
        with patch("services.wildman_account_service.shanghai_now", return_value=self.now):
            account = self.service._normalise({
                **self._payload(),
                "holdings": [{"symbol": "1.SZ", "quantity": 1, "sellable_quantity": 1}],
            })
        self.assertEqual(account["holdings"][0]["symbol"], "000001.SZ")

    async def test_cash_flow_without_unit_nav_is_excluded_from_drawdown(self):
        today = self.now.date().isoformat()
        with (
            patch("services.wildman_account_service.async_session", self.session_factory),
            patch("services.wildman_account_service.shanghai_now", return_value=self.now),
        ):
            account = await self.service.update_account("alice", {
                **self._payload(),
                "net_asset_value": None,
                "nav_history": [
                    {"as_of": (self.now.date() - timedelta(days=1)).isoformat(), "net_asset_value": 100, "cash_in": 1000},
                    {"as_of": today, "net_asset_value": 110},
                ],
            })
        self.assertEqual(account["nav_metrics"]["basis"], "unit_nav")
        self.assertEqual(account["nav_metrics"]["eligible_points"], 0)
        self.assertEqual(len(account["nav_metrics"]["excluded_points"]), 2)
        self.assertIn("单位净值", account["nav_metrics"]["excluded_points"][0]["reason"])

    async def test_hard_bounds_and_sellable_validation(self):
        too_many = [{"symbol": f"{index:06d}", "quantity": 1, "sellable_quantity": 1} for index in range(MAX_HOLDINGS + 1)]
        with patch("services.wildman_account_service.shanghai_now", return_value=self.now):
            with self.assertRaisesRegex(ValueError, "100"):
                self.service._normalise({**self._payload(), "holdings": too_many})
            with self.assertRaisesRegex(ValueError, "不能大于"):
                self.service._normalise({
                    **self._payload(),
                    "holdings": [{"symbol": "600519", "quantity": 1, "sellable_quantity": 2}],
                })


class WildmanAccountRouteTests(unittest.TestCase):
    def test_account_routes_require_auth_and_keep_symbol_scope_explicit(self):
        app = FastAPI()
        app.include_router(router)
        fake_account = {"as_of": "2026-09-20", "target": {"symbol": "600519", "sellable_quantity": 10}}
        token = create_admin_token(settings.admin_username)
        with patch.object(wildman_account_service, "get_account", new=AsyncMock(return_value=fake_account)) as getter:
            client = TestClient(app)
            self.assertEqual(client.get("/api/v1/wildman/account").status_code, 401)
            response = client.get(
                "/api/v1/wildman/account?symbol=600519",
                headers={"Authorization": f"Bearer {token}"},
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["data"]["target"]["symbol"], "600519")
        getter.assert_awaited_once_with(settings.admin_username, "600519")


if __name__ == "__main__":
    unittest.main()
