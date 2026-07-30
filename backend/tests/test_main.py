import unittest

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


if __name__ == "__main__":
    unittest.main()
