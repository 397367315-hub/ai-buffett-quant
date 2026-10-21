import json
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api import roundtable_routes
from services.admin_auth import require_admin as host_require_admin
from services.roundtable_runtime import RoundtableRuntimePersistenceError

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "roundtable_workspace" / "records/roundtable/2026-10-18-圆桌面板设计"))


class FakeRuntime:
    def __init__(self):
        self.session_tokens = {"session-1": "opaque-cookie"}
        self.unavailable = False
        self.revision = 3
        self.asset_root = Path(__file__).resolve().parents[1] / "roundtable_workspace"
        self.vendor_root = self.asset_root
        self.requests = []
        self.fail_persistence = False

    def handle(self, request):
        self.requests.append(request)
        if request.path == "/api/v1/session/bootstrap":
            return SimpleNamespace(
                status=201,
                body=json.dumps({"session_id": "session-1", "csrf_token": "csrf-1"}).encode(),
                headers=[("Content-Type", "application/json")],
            )
        if request.path == "/api/v1/session/context" and not request.headers.get("x-csrf-token"):
            return SimpleNamespace(
                status=403,
                body=b'{"error":{"code":"CSRF_REQUIRED"}}',
                headers=[("Content-Type", "application/json")],
            )
        return SimpleNamespace(
            status=200,
            body=b'{"ok":true}',
            headers=[("Content-Type", "application/json")],
        )

    def mark_dirty(self):
        return None

    async def flush(self, *, force=False):
        if self.fail_persistence:
            raise RoundtableRuntimePersistenceError("checkpoint unavailable")
        return self.revision


class RoundtableRouteTests(unittest.TestCase):
    def setUp(self):
        self.app = FastAPI()
        self.app.include_router(roundtable_routes.router)
        self.app.dependency_overrides[host_require_admin] = lambda: "admin"
        self.runtime = FakeRuntime()
        self.runtime_patch = patch.object(
            roundtable_routes, "get_roundtable_runtime", return_value=self.runtime
        )
        self.runtime_patch.start()
        self.client = TestClient(self.app)

    def tearDown(self):
        self.runtime_patch.stop()

    @staticmethod
    def headers(**extra):
        return {"Authorization": "Bearer test", "Origin": "http://localhost:3000", **extra}

    def test_missing_host_auth_is_rejected(self):
        self.app.dependency_overrides.pop(host_require_admin, None)
        response = self.client.post(
            "/api/v1/roundtable/v1/session/bootstrap",
            headers={"Origin": "http://localhost:3000", "Content-Type": "application/json"},
            json={},
        )
        self.assertEqual(response.status_code, 401)
        self.app.dependency_overrides[host_require_admin] = lambda: "admin"

    def test_host_status_uses_registry_payload_with_skills_root(self):
        response = self.client.get("/api/v1/roundtable/host/status", headers=self.headers())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["data"]["seat_count"], 13)

    def test_bootstrap_then_csrf_write_and_session_token_recovery(self):
        response = self.client.post(
            "/api/v1/roundtable/v1/session/bootstrap",
            headers=self.headers(**{"Content-Type": "application/json"}), json={},
        )
        self.assertEqual(response.status_code, 201)
        context = response.json()
        self.assertEqual(context["session_id"], "session-1")
        response = self.client.post(
            "/api/v1/roundtable/v1/session/context",
            headers=self.headers(**{
                "Content-Type": "application/json",
                "X-Roundtable-Session": context["session_id"],
                "X-CSRF-Token": context["csrf_token"],
            }),
            json={"expected_context_revision": 0, "mode": "live", "replay_id": None,
                  "acknowledge_training_end": False},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.runtime.requests[-1].headers.get("cookie"), "rt_local_session=opaque-cookie")

    def test_write_without_csrf_is_rejected(self):
        response = self.client.post(
            "/api/v1/roundtable/v1/session/context",
            headers=self.headers(**{
                "Content-Type": "application/json",
                "X-Roundtable-Session": "session-1",
            }),
            json={"expected_context_revision": 0, "mode": "live", "replay_id": None,
                  "acknowledge_training_end": False},
        )
        self.assertEqual(response.status_code, 403)

    def test_path_traversal_is_rejected(self):
        response = self.client.get(
            "/api/v1/roundtable/v1/%2E%2E/%2E%2E/etc/passwd",
            headers=self.headers(),
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"]["code"], "INVALID_PATH")

    def test_persistence_failure_returns_503(self):
        self.runtime.fail_persistence = True
        response = self.client.post(
            "/api/v1/roundtable/v1/session/bootstrap",
            headers=self.headers(**{"Content-Type": "application/json"}), json={},
        )
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["error"]["code"], "PERSISTENCE_UNAVAILABLE")

    def test_job_poll_only_flushes_terminal_projection(self):
        self.assertFalse(roundtable_routes._terminal_job_response(b'{"job":{"state":"running"}}'))
        self.assertTrue(roundtable_routes._terminal_job_response(b'{"job":{"state":"failed"}}'))


if __name__ == "__main__":
    unittest.main()
