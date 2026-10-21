import base64
import io
import os
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from services import roundtable_assets as assets


class RoundtableAssetsTests(unittest.TestCase):
    def _encrypted(self, root: Path, key: bytes, *, member_name: str = "skills/demo/SKILL.md") -> None:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        payload = io.BytesIO()
        with tarfile.open(fileobj=payload, mode="w:gz") as tf:
            for name, data in (
                ("skills", None),
                ("skills/demo", None),
                (member_name, b"demo skill"),
                ("knowledge", None),
                ("knowledge/manifest.json", b"{}"),
            ):
                info = tarfile.TarInfo(name)
                if data is None:
                    info.type = tarfile.DIRTYPE
                    tf.addfile(info)
                else:
                    info.size = len(data)
                    tf.addfile(info, io.BytesIO(data))
        nonce = os.urandom(12)
        encrypted = AESGCM(key).encrypt(nonce, payload.getvalue(), assets.MAGIC)
        (root / "private_assets.aesgcm").write_bytes(assets.MAGIC + nonce + encrypted)

    def _env(self, root: Path, key: bytes):
        return patch.dict(os.environ, {
            "ROUNDTABLE_ASSET_ROOT": "",
            "ROUNDTABLE_ASSET_KEY": base64.urlsafe_b64encode(key).decode(),
            "ROUNDTABLE_ALLOW_PLAINTEXT_ASSETS": "",
            "RENDER": "1",
        }, clear=False)

    def test_aes_gcm_roundtrip(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            key = os.urandom(32)
            self._encrypted(root, key)
            with self._env(root, key):
                result = assets.prepare_roundtable_assets(root)
            self.assertTrue((result / "knowledge/manifest.json").is_file())
            self.assertEqual((result / "skills/demo/SKILL.md").read_bytes(), b"demo skill")

    def test_wrong_key_and_tampering_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            key = os.urandom(32)
            self._encrypted(root, key)
            with self._env(root, os.urandom(32)):
                with self.assertRaises(assets.RoundtableAssetsError):
                    assets.prepare_roundtable_assets(root)
            data = bytearray((root / "private_assets.aesgcm").read_bytes())
            data[-1] ^= 1
            (root / "private_assets.aesgcm").write_bytes(data)
            with self._env(root, key):
                with self.assertRaises(assets.RoundtableAssetsError):
                    assets.prepare_roundtable_assets(root)

    def test_path_traversal_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            key = os.urandom(32)
            self._encrypted(root, key, member_name="skills/../escape.txt")
            with self._env(root, key):
                with self.assertRaises(assets.RoundtableAssetsError):
                    assets.prepare_roundtable_assets(root)

    def test_saved_model_credentials_are_encrypted_and_authenticated(self):
        key = os.urandom(32)
        with self._env(Path("/unused"), key):
            private = b'{"profiles":{"test":"private-test-key"}}'
            encrypted = assets.encrypt_private_state(private)
            self.assertNotIn(b"private-test-key", encrypted)
            self.assertEqual(assets.decrypt_private_state(encrypted), private)
            altered = encrypted[:-1] + bytes([encrypted[-1] ^ 1])
            with self.assertRaises(assets.RoundtableAssetsError):
                assets.decrypt_private_state(altered)


if __name__ == "__main__":
    unittest.main()
