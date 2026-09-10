import asyncio
import json
import unittest
from unittest.mock import patch

import httpx
from fastapi import FastAPI
from starlette.requests import Request

from api.quant_routes import router as quant_router
from api.routes import auth_login, router as main_router
from services.admin_auth import (
    create_admin_token,
    require_admin_for_mutation,
)


def _request(method: str, path: str, authorization: str | None = None) -> Request:
    headers = []
    if authorization:
        headers.append((b"authorization", authorization.encode("ascii")))
    return Request({
        "type": "http",
        "method": method,
        "path": path,
        "headers": headers,
        "query_string": b"",
        "scheme": "http",
        "server": ("test", 80),
        "client": ("test", 1),
    })


class AdminMutationDependencyTests(unittest.TestCase):
    def test_read_requests_keep_existing_access_semantics(self):
        result = asyncio.run(require_admin_for_mutation(_request("GET", "/api/v1/quant/rules"), None))
        self.assertEqual(result, "")

    def test_login_is_the_only_public_mutation_in_main_router(self):
        result = asyncio.run(require_admin_for_mutation(_request("POST", "/api/v1/auth/login"), None))
        self.assertEqual(result, "")

    def test_other_mutations_require_a_bearer_token(self):
        with self.assertRaisesRegex(Exception, "登录已失效"):
            asyncio.run(require_admin_for_mutation(_request("POST", "/api/v1/ai/chat"), None))

    def test_valid_bearer_token_is_accepted(self):
        with (
            patch("services.admin_auth.settings.admin_username", "admin"),
            patch("services.admin_auth.settings.admin_password", "private-password"),
        ):
            token = create_admin_token("admin", now=1000)
            with patch("services.admin_auth.time.time", return_value=1001):
                result = asyncio.run(require_admin_for_mutation(
                    _request("POST", "/api/v1/ai/chat", f"Bearer {token}"),
                    f"Bearer {token}",
                ))
            self.assertEqual(result, "admin")


class AuthApiContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_invalid_credentials_return_real_401_and_structured_error(self):
        with (
            patch("api.routes.settings.admin_username", "admin"),
            patch("api.routes.settings.admin_password", "private-password"),
        ):
            response = await auth_login({"username": "admin", "password": "wrong"})

        self.assertEqual(response.status_code, 401)
        payload = json.loads(response.body)
        self.assertEqual(payload["code"], 401)
        self.assertEqual(payload["error"], "INVALID_CREDENTIALS")


class MountedRouteAuthTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.app = FastAPI()
        self.app.include_router(main_router)
        self.app.include_router(quant_router)

    async def test_unauthed_mutation_is_rejected_before_handler_validation(self):
        transport = httpx.ASGITransport(app=self.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post("/api/v1/quant/strategy", json={})
        self.assertEqual(response.status_code, 401)

    async def test_unauthed_read_remains_available(self):
        transport = httpx.ASGITransport(app=self.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/api/v1/quant/rules")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json().get("code"), 0)


if __name__ == "__main__":
    unittest.main()
