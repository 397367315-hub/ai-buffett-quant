from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from main import app
from services.wildman_service import wildman_service


def test_wildman_routes_are_registered():
    paths = set(app.openapi()["paths"])

    assert "/api/v1/wildman/dashboard" in paths
    assert "/api/v1/wildman/candidates/{symbol}" in paths
    assert "/api/v1/wildman/intraday/{symbol}" in paths
    assert "/api/v1/wildman/manual-review" in paths


def test_dashboard_read_contract_and_market_filters():
    payload = {
        "trade_date": "2026-09-19",
        "rule_version": "WM_RULE_CORE_V1_0",
        "cycle": {"cycle": "发酵/主升"},
        "market_facts": {},
        "mainlines": [],
        "candidates": [],
        "filters": {"exclude_star_market": False, "exclude_gem": True},
    }
    with patch.object(wildman_service, "dashboard", new=AsyncMock(return_value=payload)) as dashboard:
        response = TestClient(app).get(
            "/api/v1/wildman/dashboard?exclude_star_market=false&exclude_gem=true"
        )

    assert response.status_code == 200
    assert response.json()["data"]["rule_version"] == "WM_RULE_CORE_V1_0"
    dashboard.assert_awaited_once()
    assert dashboard.await_args.kwargs["exclude_star_market"] is False
    assert dashboard.await_args.kwargs["exclude_gem"] is True


def test_intraday_contract_exposes_numcat_level2_evidence():
    payload = {
        "symbol": "600519",
        "trade_date": "2026-09-19",
        "support": {"state": "良性", "level2_available": True},
        "level2": {
            "available": True,
            "provider": "numcat",
            "summary": {
                "absorption": {"buy": {"value": 78}},
                "obi": {"value": 0.22},
            },
        },
    }
    with patch.object(wildman_service, "candidate", new=AsyncMock(return_value=payload)):
        response = TestClient(app).get("/api/v1/wildman/intraday/600519")

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["support"]["state"] == "良性"
    assert data["level2"]["provider"] == "numcat"
    assert data["level2"]["summary"]["obi"]["value"] == 0.22


def test_manual_review_write_requires_login():
    with patch.object(wildman_service, "save_review", new=AsyncMock()) as save_review:
        response = TestClient(app).post(
            "/api/v1/wildman/manual-review",
            json={"symbol": "600519", "entry_date": "2026-09-19"},
        )

    assert response.status_code == 401
    save_review.assert_not_awaited()


def test_invalid_date_is_rejected_before_dashboard_call():
    with patch.object(wildman_service, "dashboard", new=AsyncMock()) as dashboard:
        response = TestClient(app).get("/api/v1/wildman/dashboard?date=not-a-date")

    assert response.status_code == 422
    dashboard.assert_not_awaited()
