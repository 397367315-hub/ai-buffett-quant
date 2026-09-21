import unittest
from datetime import date, timedelta
from unittest.mock import AsyncMock, PropertyMock, patch

import services.wildman_mainline_service as mainline_service


TARGET = date(2026, 9, 18)
TRIGGER = TARGET
THEME = "机器人"
THEME_SYMBOL = "T1"
CORE_CODES = [f"60000{index}" for index in range(1, 7)]


def _board_rows():
    rows = []
    for index in range(20):
        day = TARGET - timedelta(days=20 - index)
        rows.append({
            "symbol": "IDX1",
            "name": THEME,
            "type": "hy",
            "is_index": True,
            "tradedate": day.isoformat(),
            "open": 100,
            "high": 101,
            "low": 99,
            "close": 100,
            "vol": 100,
            "amount": 1_000_000,
            "pct_chg": 0,
        })
    rows.append({
        "symbol": "IDX1",
        "name": THEME,
        "type": "hy",
        "is_index": True,
        "tradedate": TARGET.isoformat(),
        "open": 101,
        "high": 112,
        "low": 100,
        "close": 111,
        "vol": 150,
        "amount": 2_000_000,
        "pct_chg": 11,
    })
    rows.append({
        "symbol": "IDX1",
        "name": THEME,
        "type": "hy",
        "is_index": True,
        "tradedate": (TARGET + timedelta(days=1)).isoformat(),
        "open": 111,
        "high": 113,
        "low": 110,
        "close": 112,
        "vol": 160,
        "amount": 2_100_000,
        "pct_chg": 1,
    })
    rows.append({
        "symbol": "IDX1",
        "name": "机器人股份",
        "type": "stock",
        "tradedate": TARGET.isoformat(),
        "pct_chg": 99,
    })
    return rows


def _legacy_theme():
    return [{
        "theme_id": THEME,
        "theme_name": THEME,
        "theme_symbol": THEME_SYMBOL,
        "rows": [{"code": CORE_CODES[0], "name": "机器人股份", "continuous_days": 1}],
    }]


def _inputs():
    return {
        "limit_history": [{
            "code": CORE_CODES[0], "name": "机器人股份", "trade_date": TARGET.isoformat(),
            "continuous_days": 1, "first_limit_time": "09:35",
        }],
        "member_history": [
            {"theme_symbol": THEME_SYMBOL, "theme_name": THEME, "trade_date": TARGET.isoformat(), "symbols": CORE_CODES},
            {"theme_symbol": THEME_SYMBOL, "theme_name": THEME, "trade_date": (TARGET + timedelta(days=1)).isoformat(), "symbols": ["999999"]},
        ],
        "theme_symbols": {THEME: THEME_SYMBOL},
    }


def _history():
    return {"mainline_inputs": _inputs()}


class WildmanMainlineServiceTests(unittest.IsolatedAsyncioTestCase):
    def test_candidate_pool_excludes_style_low_price_transfer_and_keeps_industry_topic(self):
        legacy = [
            {"theme_name": "广义风格", "rows": []},
            {"theme_name": "低价股", "rows": []},
            {"theme_name": "股权转让", "rows": []},
            {"theme_name": "电力设备", "rows": []},
            {"theme_name": "legacy-only行业", "rows": []},
        ]
        inputs = {
            "limit_history": [{"code": "000001", "trade_date": TARGET.isoformat(), "continuous_days": 1}],
            "member_history": [
                {"theme_name": name, "theme_symbol": name, "trade_date": TARGET.isoformat(), "symbols": ["000001"]}
                for name in ("广义风格", "低价股", "股权转让", "电力设备")
            ],
        }

        themes = mainline_service.candidate_themes(TARGET, legacy, inputs)

        self.assertEqual([row["theme_name"] for row in themes], ["电力设备"])

    async def test_acquisition_uses_full_dated_membership_and_bounded_rmb_cores(self):
        derive_calls = []

        def fake_derive(target, theme, **kwargs):
            derive_calls.append({"theme": theme, **kwargs})
            return {
                "trigger_date": TRIGGER.isoformat(),
                "mainline_five_steps": {
                    "step3": {"actual": {}, "sources": [], "reason": "mock"},
                },
            }

        async def rows(_apiname, *, params=None, fields=None, **_kwargs):
            if fields == mainline_service.BOARD_FIELDS:
                return _board_rows()
            if fields == mainline_service.QUOTE_FIELDS:
                return [
                    {
                        "symbol": code,
                        "name": f"中军{code}",
                        "tradedate": TARGET.isoformat(),
                        "total_mv": 12_000_000_000,
                        "amount": 100 - index,
                    }
                    for index, code in enumerate(CORE_CODES)
                ]
            raise AssertionError(f"unexpected fields: {fields}")

        async def history_batch(codes, **_kwargs):
            self.assertEqual(len(codes), mainline_service.MAX_CORES_PER_THEME)
            self.assertEqual(codes, sorted(CORE_CODES[:5]))
            return {code: [{
                "date": TARGET.isoformat(), "open": 20, "high": 21,
                "low": 19, "close": 20.5, "volume": 1_000,
            }] for code in codes}

        async def theme_members(**_kwargs):
            return [{
                "theme_symbol": THEME_SYMBOL,
                "theme_name": THEME,
                "trade_date": TRIGGER.isoformat(),
                "symbols": CORE_CODES,
            }]

        with (
            patch.object(mainline_service, "derive_mainline_steps", side_effect=fake_derive),
            patch.object(mainline_service, "select_trigger_date", return_value=TRIGGER),
            patch.object(mainline_service, "_stored_policy", new=AsyncMock(return_value=[])),
            patch.object(type(mainline_service.numcat_market_provider), "configured", new_callable=PropertyMock, return_value=True),
            patch.object(mainline_service.numcat_market_provider, "theme_members", side_effect=theme_members),
            patch.object(mainline_service.numcat_market_provider, "news", new=AsyncMock(return_value=[])),
            patch.object(mainline_service.numcat_extended_provider, "industry_boards", new=AsyncMock(return_value=[{"symbol": "IDX1", "name": THEME}])),
            patch.object(mainline_service.numcat_extended_provider, "concept_boards", new=AsyncMock(return_value=[])),
            patch.object(mainline_service.numcat_extended_provider, "rows", side_effect=rows),
            patch.object(mainline_service, "fetch_numcat_history_batch", side_effect=history_batch),
        ):
            results, source = await mainline_service.enrich_mainlines(TARGET, _legacy_theme(), _history(), {})

        self.assertEqual(source["max_cores_per_theme"], 5)
        self.assertEqual(len(derive_calls), 1)
        final = derive_calls[-1]
        self.assertEqual(set(final["theme"]["member_codes"]), set(CORE_CODES))
        self.assertEqual(set(final["stock_quotes"]), set(CORE_CODES[:5]))
        self.assertTrue(all(value["market_cap"] == 12_000_000_000 for value in final["stock_quotes"].values()))
        self.assertEqual(final["stock_bars"][CORE_CODES[0]][0]["trade_date"], TARGET.isoformat())
        self.assertEqual(len(results), 1)

    async def test_board_acquisition_passes_real_ohlcv_index_rows_and_discards_future_rows(self):
        derive_calls = []

        def fake_derive(target, theme, **kwargs):
            derive_calls.append(kwargs)
            return {"trigger_date": TRIGGER.isoformat(), "mainline_five_steps": {}}

        async def rows(_apiname, *, fields=None, **_kwargs):
            if fields == mainline_service.BOARD_FIELDS:
                return _board_rows()
            if fields == mainline_service.QUOTE_FIELDS:
                return [{"symbol": CORE_CODES[0], "tradedate": TRIGGER.isoformat(), "total_mv": 1_000_000_000, "amount": 1}]
            raise AssertionError(fields)

        async def theme_members(**_kwargs):
            return [{"theme_symbol": THEME_SYMBOL, "trade_date": TRIGGER.isoformat(), "symbols": CORE_CODES[:1]}]

        with (
            patch.object(mainline_service, "derive_mainline_steps", side_effect=fake_derive),
            patch.object(mainline_service, "select_trigger_date", return_value=TRIGGER),
            patch.object(mainline_service, "_stored_policy", new=AsyncMock(return_value=[])),
            patch.object(type(mainline_service.numcat_market_provider), "configured", new_callable=PropertyMock, return_value=True),
            patch.object(mainline_service.numcat_market_provider, "theme_members", side_effect=theme_members),
            patch.object(mainline_service.numcat_market_provider, "news", new=AsyncMock(return_value=[])),
            patch.object(mainline_service.numcat_extended_provider, "industry_boards", new=AsyncMock(return_value=[{"symbol": "IDX1", "name": THEME}])),
            patch.object(mainline_service.numcat_extended_provider, "concept_boards", new=AsyncMock(return_value=[])),
            patch.object(mainline_service.numcat_extended_provider, "rows", side_effect=rows),
            patch.object(mainline_service, "fetch_numcat_history_batch", new=AsyncMock(return_value={})),
        ):
            await mainline_service.enrich_mainlines(TARGET, _legacy_theme(), _history(), {})

        board_rows = derive_calls[-1]["board_bars"]
        self.assertTrue(board_rows)
        self.assertEqual(len(board_rows), 21)
        self.assertTrue(all(row["trade_date"] <= TARGET.isoformat() for row in board_rows))
        self.assertTrue(all(row.get("close") is not None and row.get("volume") is not None for row in board_rows))
        self.assertTrue(any(row.get("high") is not None for row in board_rows))

    async def test_future_member_rows_are_not_forwarded_into_final_point_in_time_facts(self):
        derive_calls = []

        def fake_derive(target, theme, **kwargs):
            derive_calls.append(kwargs)
            return {"trigger_date": TRIGGER.isoformat(), "mainline_five_steps": {}}

        async def rows(_apiname, *, fields=None, **_kwargs):
            if fields == mainline_service.BOARD_FIELDS:
                return []
            if fields == mainline_service.QUOTE_FIELDS:
                return []
            raise AssertionError(fields)

        async def theme_members(**_kwargs):
            return [{"theme_symbol": THEME_SYMBOL, "trade_date": TRIGGER.isoformat(), "symbols": CORE_CODES[:1]}]

        with (
            patch.object(mainline_service, "derive_mainline_steps", side_effect=fake_derive),
            patch.object(mainline_service, "select_trigger_date", return_value=TRIGGER),
            patch.object(mainline_service, "_stored_policy", new=AsyncMock(return_value=[])),
            patch.object(type(mainline_service.numcat_market_provider), "configured", new_callable=PropertyMock, return_value=True),
            patch.object(mainline_service.numcat_market_provider, "theme_members", side_effect=theme_members),
            patch.object(mainline_service.numcat_market_provider, "news", new=AsyncMock(return_value=[])),
            patch.object(mainline_service.numcat_extended_provider, "industry_boards", new=AsyncMock(return_value=[])),
            patch.object(mainline_service.numcat_extended_provider, "concept_boards", new=AsyncMock(return_value=[])),
            patch.object(mainline_service.numcat_extended_provider, "rows", side_effect=rows),
            patch.object(mainline_service, "fetch_numcat_history_batch", new=AsyncMock(return_value={})),
        ):
            await mainline_service.enrich_mainlines(TARGET, _legacy_theme(), _history(), {})

        final_members = derive_calls[-1]["member_history"]
        self.assertTrue(final_members)
        self.assertTrue(all(row["trade_date"] <= TARGET.isoformat() for row in final_members))


if __name__ == "__main__":
    unittest.main()
