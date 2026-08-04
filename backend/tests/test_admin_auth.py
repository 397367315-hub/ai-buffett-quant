import unittest
from unittest.mock import patch

from fastapi import HTTPException

from services.admin_auth import TOKEN_TTL_SECONDS, create_admin_token, require_admin, verify_admin_token


class AdminAuthTests(unittest.TestCase):
    def test_signed_token_round_trip_and_expiration(self):
        with (
            patch("services.admin_auth.settings.admin_username", "admin"),
            patch("services.admin_auth.settings.admin_password", "private-password"),
        ):
            token = create_admin_token("admin", now=1000)
            self.assertEqual(verify_admin_token(token, now=1001), "admin")
            self.assertIsNone(verify_admin_token(token, now=1000 + TOKEN_TTL_SECONDS + 1))

    def test_tampered_or_missing_token_is_rejected(self):
        with (
            patch("services.admin_auth.settings.admin_username", "admin"),
            patch("services.admin_auth.settings.admin_password", "private-password"),
        ):
            token = create_admin_token("admin", now=1000)
            self.assertIsNone(verify_admin_token(token + "tampered", now=1001))
            with patch("services.admin_auth.time.time", return_value=1001):
                self.assertEqual(require_admin(f"Bearer {token}"), "admin")
            with self.assertRaises(HTTPException) as context:
                require_admin(None)
            self.assertEqual(context.exception.status_code, 401)


if __name__ == "__main__":
    unittest.main()
