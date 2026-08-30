from datetime import date, datetime
from types import SimpleNamespace
from unittest.mock import patch

import httpx
import pytest

from config import settings
from engines.microstructure import build_feature_series, build_summary
from market_data.level2.fetcher import Level2Fetcher
from market_data.level2.models import BookLevel, OrderBookSnapshot, TradeTick
from market_data.level2.normalizer import normalize_quote_row, normalize_trade_row
from market_data.level2.providers.base import Level2Page, Level2Provider, ProviderCapabilities
from market_data.level2.providers.numcat import NumCatProvider
from market_data.level2.repository import Level2Repository
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from database import Base


class _FakeClient:
    def __init__(self, response, captured):
        self.response = response
        self.captured = captured

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def post(self, url, **kwargs):
        self.captured["url"] = url
        self.captured["json"] = kwargs["json"]
        return self.response


@pytest.mark.asyncio
async def test_numcat_provider_uses_documented_post_contract_and_cursor_payload():
    captured = {}
    response = httpx.Response(
        200,
        json={
            "code": 0,
            "data": {
                "fields": ["symbol", "tradedate", "time", "price", "volume"],
                "items": [["600519", "20260829", "09:31:00", 1500, 100]],
                "next_cursor": "page-2",
                "has_more": True,
            },
        },
        request=httpx.Request("POST", "https://numcat.test/api"),
    )

    async def no_sleep(_delay):
        return None

    def client_factory(**_kwargs):
        return _FakeClient(response, captured)

    with patch.object(settings, "level2_enabled", True), patch.object(settings, "numcat_api_key", "server-only-key"), patch.object(settings, "numcat_api_base", "https://numcat.test/api"):
        page = await NumCatProvider(client_factory=client_factory, sleep=no_sleep).fetch_page(
            "trade", "600519", date(2026, 8, 29), cursor="page-1"
        )

    assert captured["url"] == "https://numcat.test/api"
    assert captured["json"]["apiname"] == "level2_trade_history"
    assert captured["json"]["apikey"] == "server-only-key"
    assert captured["json"]["params"] == {
        "symbol": "600519",
        "tradedate": "20260829",
        "page_size": 5000,
        "cursor": "page-1",
    }
    assert page.has_more is True
    assert page.next_cursor == "page-2"
    assert page.rows[0]["price"] == 1500


def test_level2_normalizer_supports_field_arrays_and_ten_level_quotes():
    trade = normalize_trade_row(
        ["600519", "20260829", "09:31:00", "t1", "1500", "100", "150000", "B"],
        ["symbol", "tradedate", "time", "trade_id", "price", "volume", "amount", "bs_flag"],
    )
    quote = normalize_quote_row(
        ["600519", "20260829", "09:31:00", 1490, 120, 1510, 80],
        ["symbol", "tradedate", "time", "bid1", "bid_vol1", "ask1", "ask_vol1"],
    )

    assert trade.symbol == "600519"
    assert trade.side == "buy"
    assert trade.direction_method == "explicit_bs_flag"
    assert quote.bids[0].price == 1490
    assert quote.asks[0].volume == 80
    assert quote.bids[9].price is None


class _StubProvider(Level2Provider):
    name = "stub"

    def __init__(self, pages):
        self.pages = pages
        self.calls = []

    @property
    def configured(self):
        return True

    @property
    def capabilities(self):
        return ProviderCapabilities(supports_history_trade=True)

    async def fetch_page(self, data_type, symbol, trade_date, **kwargs):
        self.calls.append(kwargs.get("cursor"))
        return self.pages[len(self.calls) - 1]


class _MemoryRepository:
    def __init__(self):
        self.jobs = {}
        self.rows = []

    async def get_job(self, symbol, trade_date, data_type):
        return self.jobs.get((symbol, trade_date, data_type))

    async def save_job(self, values):
        self.jobs[(values["symbol"], values["trade_date"], values["data_type"])] = SimpleNamespace(**values)

    async def save_trades(self, rows):
        rows = list(rows)
        self.rows.extend(rows)
        return len(rows)


@pytest.mark.asyncio
async def test_fetcher_reads_all_cursor_pages_and_stops_on_repeated_cursor():
    fields = ["symbol", "tradedate", "time", "trade_id", "price", "volume", "bs_flag"]
    first = Level2Page("trade", fields, [["600519", "20260829", "09:31:00", "1", 10, 100, "B"]], 1, "next", True)
    second = Level2Page("trade", fields, [["600519", "20260829", "09:32:00", "2", 10.1, 100, "S"]], 1, None, False)
    provider = _StubProvider([first, second])
    repository = _MemoryRepository()

    result = await Level2Fetcher(provider, repository).run("600519", date(2026, 8, 29), data_types=("trade",))

    assert result.statuses == {"trade": "completed"}
    assert result.rows == {"trade": 2}
    assert result.pagination_complete == {"trade": True}
    assert provider.calls == [None, "next"]
    assert repository.jobs[("600519", date(2026, 8, 29), "trade")].status == "completed"

    repeated_provider = _StubProvider([first, Level2Page("trade", fields, [], 0, "next", True)])
    repeated_repository = _MemoryRepository()
    repeated = await Level2Fetcher(repeated_provider, repeated_repository).run("600519", date(2026, 8, 29), data_types=("trade",))
    assert repeated.statuses["trade"] == "partial"
    assert repeated.pagination_complete["trade"] is False
    assert "重复游标" in repeated.errors["trade"]


def test_microstructure_summary_exposes_observable_features_without_identity_claims():
    target = date(2026, 8, 29)
    quote = OrderBookSnapshot(
        symbol="600519", trade_date=target, timestamp=datetime(2026, 8, 29, 9, 31),
        last_price=10.1,
        bids=[BookLevel(10.0, 1000, 1), BookLevel(9.9, 800, 2)],
        asks=[BookLevel(10.1, 200, 1), BookLevel(10.2, 300, 2)],
    )
    trades = [
        TradeTick("600519", target, datetime(2026, 8, 29, 9, 31, 1), price=10.0, volume=100, amount=1000, side="buy", direction_method="test", direction_confidence=1),
        TradeTick("600519", target, datetime(2026, 8, 29, 9, 31, 30), price=10.1, volume=100, amount=1010, side="buy", direction_method="test", direction_confidence=1),
        TradeTick("600519", target, datetime(2026, 8, 29, 9, 31, 50), price=10.1, volume=20, amount=202, side="sell", direction_method="test", direction_confidence=1),
    ]

    features = build_feature_series(trades, quotes=[quote])
    summary = build_summary(features, {"status": "complete"})

    assert features
    assert summary["available"] is True
    assert summary["hfi"]["available"] is True
    assert summary["obi"]["available"] is True
    assert "账户身份" in "".join(summary["explanation"])


@pytest.mark.asyncio
async def test_feature_rows_with_engine_metadata_can_be_persisted():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    try:
        target = date(2026, 8, 29)
        features = [{
            "symbol": "600519",
            "trade_date": target,
            "minute": datetime(2026, 8, 29, 9, 31),
            "qas": 55.0,
            "qas_type": "中等活跃",
            "hfi": 12.0,
            "hfi_components": {"active_flow": {"normalized": 0.2}},
            "components": {},
            "explanation": ["测试样本"],
            "data_quality": "complete",
            "source": "test",
        }]
        with patch("market_data.level2.repository.async_session", session_factory):
            assert await Level2Repository().save_features(features) == 1
            loaded = await Level2Repository().load_features("600519", target)
        assert loaded[0]["qas_type"] == "中等活跃"
        assert loaded[0]["hfi_components"]["active_flow"]["normalized"] == 0.2
    finally:
        await engine.dispose()
