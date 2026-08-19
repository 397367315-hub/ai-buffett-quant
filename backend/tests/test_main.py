import unittest

from fastapi.testclient import TestClient

from main import app


class MainAppTests(unittest.TestCase):
    def test_root_supports_render_head_health_checks(self):
        health_check_paths = {"/", "/health"}
        health_check_routes = [
            route
            for route in app.routes
            if getattr(route, "path", None) in health_check_paths
        ]

        self.assertEqual(
            {route.path for route in health_check_routes if "HEAD" in route.methods},
            health_check_paths,
        )

    def test_production_origin_preflight_explicitly_allows_auth_headers(self):
        origin = "https://ai-buffett-quant.netlify.app"
        response = TestClient(app).options(
            "/api/v1/stocks/601069/decision-profile",
            headers={
                "Origin": origin,
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "authorization,content-type",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["access-control-allow-origin"], origin)
        allowed_headers = response.headers["access-control-allow-headers"].lower()
        self.assertIn("authorization", allowed_headers)
        self.assertIn("content-type", allowed_headers)

    def test_netlify_deploy_preview_origin_is_allowed(self):
        origin = "https://deploy-preview-42--ai-buffett-quant.netlify.app"
        response = TestClient(app).options(
            "/api/v1/stocks/601069/decision-profile",
            headers={
                "Origin": origin,
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "authorization,content-type",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["access-control-allow-origin"], origin)

    def test_local_qa_origin_is_allowed(self):
        origin = "http://127.0.0.1:3100"
        response = TestClient(app).options(
            "/api/v1/market/workbench",
            headers={
                "Origin": origin,
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "authorization,content-type",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["access-control-allow-origin"], origin)


if __name__ == "__main__":
    unittest.main()
