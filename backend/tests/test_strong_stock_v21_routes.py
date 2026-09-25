from copy import deepcopy
from unittest.mock import AsyncMock, patch

import pytest

from api.strong_stock_v21_routes import v21_overview


@pytest.mark.asyncio
async def test_v21_overview_compact_only_removes_large_optional_sections():
    payload = {
        "sector_trajectories": {"S1": []},
        "lifecycle": [{"state": "STARTING"}],
        "market_history": [{"trade_date": "2026-09-25"}],
        "sectors": [{"sector_id": "S1"}],
        "opportunities": [{"symbol": "000001"}],
        "data_quality": {"status": "COMPLETE"},
    }
    with patch(
        "api.strong_stock_v21_routes.strong_stock_v21_service.overview",
        new=AsyncMock(side_effect=[deepcopy(payload), deepcopy(payload)]),
    ):
        compact = await v21_overview(date_value=None, refresh=False, exclude_star_market=True, exclude_gem=True, compact=True)
        full = await v21_overview(date_value=None, refresh=False, exclude_star_market=True, exclude_gem=True, compact=False)

    assert compact["code"] == 0
    assert set(compact["data"]) == {"sectors", "opportunities", "data_quality"}
    assert compact["data"]["sectors"] == payload["sectors"]
    assert compact["data"]["opportunities"] == payload["opportunities"]
    assert set(full["data"]) == set(payload)
    assert full["data"]["market_history"] == payload["market_history"]
