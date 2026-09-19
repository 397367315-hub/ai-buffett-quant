from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from main import app
from services.wildman_classic_service import wildman_classic_service


def test_classic_toolbox_routes_are_registered():
    paths = set(app.openapi()["paths"])
    assert "/api/v1/wildman/toolbox/{strategy_id}/scan" in paths
    assert "/api/v1/wildman/toolbox/{strategy_id}/stocks/{symbol}" in paths


def test_invalid_classic_strategy_is_422():
    with patch.object(wildman_classic_service, "scan", new=AsyncMock(side_effect=ValueError("未知经典战法"))):
        response = TestClient(app).get("/api/v1/wildman/toolbox/invalid/scan")
    assert response.status_code == 422


def test_classic_scan_exposes_running_contract():
    payload = {
        "status": "running",
        "trade_date": "2026-09-18",
        "progress": {"total": 0, "scanned": 0, "eligible": 0, "excluded": 0, "missing_history": 0, "stale_history": 0},
        "rows": [],
        "cache_hit": False,
    }
    with patch.object(wildman_classic_service, "scan", new=AsyncMock(return_value=payload)):
        response = TestClient(app).get("/api/v1/wildman/toolbox/WM_CLASSIC_520/scan?refresh=true")
    assert response.status_code == 200
    assert response.json()["data"]["status"] == "running"
