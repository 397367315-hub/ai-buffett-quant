from unittest.mock import AsyncMock, patch
from pathlib import Path

from fastapi.testclient import TestClient

from config import settings
from main import app
from services.admin_auth import create_admin_token
from services.level2_service import level2_service


def _auth_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {create_admin_token(settings.admin_username)}"}


def test_level2_routes_are_registered_without_replacing_stock_routes():
    paths = set(app.openapi()["paths"])

    assert "/api/v1/stocks/{symbol}/level2/summary" in paths
    assert "/api/v1/stocks/{symbol}/level2/timeline" in paths
    assert "/api/v1/stocks/{symbol}/level2/events" in paths
    assert "/api/v1/stocks/{symbol}/level2/sync/status" in paths
    assert "/api/v1/stocks/{symbol}/decision-profile" in paths


def test_unconfigured_level2_returns_truthful_degraded_status():
    payload = {
        "symbol": "600519",
        "trade_date": "2026-08-28",
        "provider": "numcat",
        "configured": False,
        "pending": False,
        "available": False,
        "data_quality": {"status": "not_available", "warnings": []},
        "sync": {
            "status": "provider_not_configured",
            "message": "Level-2数据源未配置API密钥，普通个股页面继续正常使用。",
        },
        "summary": {"available": False},
    }
    with patch.object(level2_service, "summary", new=AsyncMock(return_value=payload)):
        response = TestClient(app).get(
            "/api/v1/stocks/600519/level2/summary?trade_date=2026-08-28",
            headers=_auth_headers(),
        )

    assert response.status_code == 200
    body = response.json()
    assert body["code"] == 0
    assert body["data"]["configured"] is False
    assert body["data"]["available"] is False
    assert body["data"]["sync"]["status"] == "provider_not_configured"


def test_level2_failure_is_isolated_from_ordinary_stock_profile():
    stock_payload = {
        "meta": {"symbol": "600519"},
        "company": {"name": "贵州茅台"},
        "decision": {"state": "OBSERVE"},
    }
    with (
        patch.object(
            level2_service,
            "summary",
            new=AsyncMock(side_effect=RuntimeError("provider down")),
        ),
        patch(
            "api.routes.stock_essence_decision_service.get",
            new=AsyncMock(return_value=stock_payload),
        ),
    ):
        level2_response = TestClient(app).get(
            "/api/v1/stocks/600519/level2/summary",
            headers=_auth_headers(),
        )
        stock_response = TestClient(app).get(
            "/api/v1/stocks/600519/decision-profile"
        )

    assert level2_response.status_code == 503
    assert "普通行情不受影响" in level2_response.json()["detail"]
    assert stock_response.status_code == 200
    assert stock_response.json()["data"]["company"]["name"] == "贵州茅台"


def test_level2_admin_sync_requires_bearer_token():
    response = TestClient(app).post(
        "/api/v1/internal/level2/sync/600519",
        json={"trade_date": "2026-08-28"},
    )

    assert response.status_code == 401
    assert response.headers["www-authenticate"].lower() == "bearer"


def test_level2_read_cannot_trigger_vendor_work_without_login():
    with patch.object(level2_service, "summary", new=AsyncMock()) as summary:
        response = TestClient(app).get(
            "/api/v1/stocks/600519/level2/summary"
        )

    assert response.status_code == 401
    summary.assert_not_awaited()


def test_level2_response_and_logs_do_not_expose_provider_key(caplog):
    secret = "numcat-test-secret-never-returned"
    payload = {
        "symbol": "600519",
        "trade_date": "2026-08-28",
        "provider": "numcat",
        "configured": True,
        "available": False,
        "pending": False,
        "data_quality": {"status": "no_data", "warnings": []},
        "sync": {
            "status": "pending",
            "message": "历史样本已进入后台同步队列。",
        },
        "summary": {"available": False},
    }
    with (
        patch.object(settings, "numcat_api_key", secret),
        patch.object(level2_service, "summary", new=AsyncMock(return_value=payload)),
    ):
        response = TestClient(app).get(
            "/api/v1/stocks/600519/level2/summary",
            headers=_auth_headers(),
        )

    messages = "\n".join(record.getMessage() for record in caplog.records)
    assert response.status_code == 200
    assert secret not in response.text
    assert secret not in messages


def test_frontend_never_contains_numcat_credentials():
    frontend = Path(__file__).resolve().parents[2] / "frontend"
    source_files = [
        *frontend.glob("app/**/*.ts"),
        *frontend.glob("app/**/*.tsx"),
        *frontend.glob("components/**/*.ts"),
        *frontend.glob("components/**/*.tsx"),
        *frontend.glob("lib/**/*.ts"),
        *frontend.glob("lib/**/*.tsx"),
    ]
    source = "\n".join(path.read_text(encoding="utf-8") for path in source_files)

    assert "NUMCAT_API_KEY" not in source
    assert "numcat-test-secret-never-returned" not in source


def test_sync_status_route_rejects_invalid_date_before_service_call():
    with patch.object(level2_service, "sync_status", new=AsyncMock()) as sync_status:
        response = TestClient(app).get(
            "/api/v1/stocks/600519/level2/sync/status?trade_date=bad-date",
            headers=_auth_headers(),
        )

    assert response.status_code == 422
    sync_status.assert_not_awaited()
