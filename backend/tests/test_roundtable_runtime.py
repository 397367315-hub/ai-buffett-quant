import asyncio
import base64
import io
import json
import os
import tarfile
import tempfile
import unittest
import gzip
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from services.roundtable_runtime import (
    RoundtableRuntime,
    RoundtableRuntimeError,
    RoundtableRuntimePersistenceError,
)
from roundtable_runtime_store import (
    RoundtableRuntimeCASConflict,
    RoundtableRuntimeStore,
    RoundtableRuntimeStoreError,
)


class MemoryCheckpointStore:
    def __init__(self):
        self.payload = None
        self.revision = None

    async def load(self):
        if self.payload is None:
            return None
        from roundtable_runtime_store import RoundtableRuntimeState
        return RoundtableRuntimeState(self.payload, self.revision, "")

    async def save(self, payload, expected_revision):
        if expected_revision != self.revision:
            if expected_revision is None and self.revision is None:
                pass
            else:
                from roundtable_runtime_store import RoundtableRuntimeCASConflict
                raise RoundtableRuntimeCASConflict("conflict")
        self.revision = 1 if self.revision is None else self.revision + 1
        self.payload = payload
        return self.revision


class BlobMemoryCheckpointStore(MemoryCheckpointStore):
    def __init__(self):
        super().__init__()
        self.blobs = {}
        self.blob_writes = 0

    async def blob_exists(self, sha256):
        return sha256 in self.blobs

    async def save_blob(self, sha256, size, payload):
        self.blob_writes += 1
        self.blobs.setdefault(sha256, (size, bytes(payload)))

    async def load_blob(self, sha256):
        from roundtable_runtime_store import RoundtableRuntimeBlobState
        item = self.blobs.get(sha256)
        return None if item is None else RoundtableRuntimeBlobState(sha256, item[0], item[1])


class FailingCheckpointStore(MemoryCheckpointStore):
    async def save(self, payload, expected_revision):
        raise OSError("database unavailable")


class FailOnSecondSaveStore(MemoryCheckpointStore):
    def __init__(self):
        super().__init__()
        self.calls = 0

    async def save(self, payload, expected_revision):
        self.calls += 1
        if self.calls > 1:
            raise OSError("shutdown database unavailable")
        return await super().save(payload, expected_revision)


class FakeStoreSession:
    def __init__(self, row=None, rowcount=0):
        self.row = row
        self.rowcount = rowcount

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def get(self, model, key):
        return self.row

    async def execute(self, statement, parameters=None):
        updated = statement.compile().params.get("updated_at")
        if updated is not None:
            assert updated.tzinfo is None, "PostgreSQL TIMESTAMP WITHOUT TIME ZONE requires naive UTC"
        return SimpleNamespace(rowcount=self.rowcount)

    def add(self, row):
        self.row = row

    async def commit(self):
        return None

    async def rollback(self):
        return None


class RoundtableRuntimeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.vendor = Path(__file__).resolve().parents[1] / "roundtable_workspace"
        self.assets_patch = patch(
            "services.roundtable_runtime.prepare_roundtable_assets",
            return_value=self.vendor,
        )
        self.assets_patch.start()
        self.addCleanup(self.assets_patch.stop)

    async def test_empty_runtime_migrates_and_registers_thirteen_seats(self):
        store = MemoryCheckpointStore()
        with tempfile.TemporaryDirectory() as parent:
            runtime = RoundtableRuntime(self.vendor, store=store, runtime_parent=parent)
            await runtime.initialize()
            self.assertEqual(os.environ.get("ROUNDTABLE_KB_ROOT"), str(self.vendor / "knowledge"))
            row = runtime.db.one("SELECT canonical_payload FROM seat_registry_versions")
            self.assertEqual(len(json.loads(row["canonical_payload"])["seats"]), 13)
            await runtime.shutdown()

    async def test_flush_then_rebuild_restores_sqlite_evidence_and_session_map(self):
        store = MemoryCheckpointStore()
        with tempfile.TemporaryDirectory() as parent:
            first = RoundtableRuntime(self.vendor, store=store, runtime_parent=parent)
            await first.initialize()
            first.session_tokens["session-test"] = "opaque-cookie-token"
            (first.runtime_dir / "legacy_data").mkdir(parents=True, exist_ok=True)
            (first.runtime_dir / "legacy_data" / "dashboard.json").write_text(
                '{"source":"checkpoint-test"}', encoding="utf-8"
            )
            (first.runtime_dir / "evidence" / "blobs").mkdir(parents=True, exist_ok=True)
            (first.runtime_dir / "evidence" / "blobs" / "aa-test").write_bytes(b"evidence")
            first.mark_dirty()
            await first.flush(force=True)
            await first.shutdown()

            second = RoundtableRuntime(self.vendor, store=store, runtime_parent=parent)
            await second.initialize()
            self.assertEqual(second.revision, 1)
            self.assertEqual(second.session_tokens["session-test"], "opaque-cookie-token")
            self.assertTrue((second.runtime_dir / "evidence" / "blobs" / "aa-test").is_file())
            self.assertEqual(
                (second.runtime_dir / "legacy_data" / "dashboard.json").read_text(encoding="utf-8"),
                '{"source":"checkpoint-test"}',
            )
            row = second.db.one("SELECT canonical_payload FROM seat_registry_versions")
            self.assertEqual(len(json.loads(row["canonical_payload"])["seats"]), 13)
            await second.shutdown()

    async def test_content_addressed_blobs_restore_and_are_not_reuploaded(self):
        store = BlobMemoryCheckpointStore()
        with tempfile.TemporaryDirectory() as parent:
            first = RoundtableRuntime(self.vendor, store=store, runtime_parent=parent)
            await first.initialize()
            artifact = first.runtime_dir / "evidence" / "blobs" / "dedupe-test"
            artifact.parent.mkdir(parents=True, exist_ok=True)
            artifact.write_bytes(b"immutable evidence")
            await first.flush(force=True)
            self.assertEqual(store.blob_writes, 1)
            first.mark_dirty()
            await first.flush(force=True)
            self.assertEqual(store.blob_writes, 1)
            await first.shutdown()
            second = RoundtableRuntime(self.vendor, store=store, runtime_parent=parent)
            await second.initialize()
            self.assertEqual((second.runtime_dir / "evidence" / "blobs" / "dedupe-test").read_bytes(), b"immutable evidence")
            await second.shutdown()

    async def test_blob_mode_ignores_generated_legacy_feature_dump_and_reuses_gzip(self):
        store = BlobMemoryCheckpointStore()
        with tempfile.TemporaryDirectory() as parent:
            runtime = RoundtableRuntime(self.vendor, store=store, runtime_parent=parent)
            await runtime.initialize()
            legacy = runtime.runtime_dir / "legacy_data"
            legacy.mkdir(parents=True, exist_ok=True)
            # Sparse files reproduce the production size without allocating 140 MiB.
            with (legacy / "features.json").open("wb") as fh:
                fh.seek(140 * 1024 * 1024 - 1); fh.write(b"x")
            daily = legacy / "daily" / "20261022.json"
            daily.parent.mkdir(parents=True, exist_ok=True)
            daily.write_bytes(b"[1]")
            with patch("services.roundtable_runtime.gzip.GzipFile", wraps=gzip.GzipFile) as gzip_ctor:
                await runtime.flush(force=True)
                first_calls = gzip_ctor.call_count
                runtime.mark_dirty()
                await runtime.flush(force=True)
                second_calls = gzip_ctor.call_count - first_calls
            self.assertEqual(store.blob_writes, 1)
            self.assertGreaterEqual(first_calls, 2)  # checkpoint plus daily blob
            self.assertEqual(second_calls, 1)  # checkpoint only; daily blob is reused
            await runtime.shutdown()

    async def test_checkpoint_skips_atomic_writer_temp_files_and_formats_failure(self):
        store = BlobMemoryCheckpointStore()
        with tempfile.TemporaryDirectory() as parent:
            runtime = RoundtableRuntime(self.vendor, store=store, runtime_parent=parent)
            await runtime.initialize()
            tmp = runtime.runtime_dir / "evidence" / "blobs" / "aa" / ".tmp-race-writer"
            tmp.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_bytes(b"partial")
            original_stat = Path.stat
            def renamed_during_scan(path, *args, **kwargs):
                if path == tmp:
                    raise FileNotFoundError("atomic writer already renamed its temporary file")
                return original_stat(path, *args, **kwargs)
            with patch.object(Path, "stat", renamed_during_scan):
                await runtime.flush(force=True)
            with tarfile.open(fileobj=io.BytesIO(store.payload), mode="r:gz") as tf:
                manifest = json.loads(tf.extractfile("runtime/artifact_manifest.json").read())
            self.assertFalse(any(item["path"].endswith(".tmp-race-writer") for item in manifest))
            try:
                raise OSError("database unavailable secret-placeholder SQL parameters")
            except OSError as exc:
                error = RoundtableRuntime._format_persistence_error(exc)
            self.assertTrue(error.startswith("OSError"))
            self.assertNotIn("secret-placeholder", error)
            self.assertNotIn("SQL parameters", error)
            self.assertIn("test_roundtable_runtime.py:", error)
            await runtime.shutdown()

    async def test_user_model_credentials_survive_only_as_encrypted_checkpoint(self):
        key = base64.urlsafe_b64encode(os.urandom(32)).decode()
        store = MemoryCheckpointStore()
        secret = b'{"test-profile":"private-key-for-test-only"}'
        with tempfile.TemporaryDirectory() as parent, patch.dict(os.environ, {"ROUNDTABLE_ASSET_KEY": key}):
            first = RoundtableRuntime(self.vendor, store=store, runtime_parent=parent)
            await first.initialize()
            config = first.runtime_dir / "config"
            config.mkdir(exist_ok=True)
            (config / "llm_secrets.json").write_bytes(secret)
            await first.flush(force=True)
            with tarfile.open(fileobj=io.BytesIO(store.payload), mode="r:gz") as tf:
                self.assertNotIn("runtime/config/llm_secrets.json", tf.getnames())
                self.assertIn("runtime/config/llm_secrets.aesgcm", tf.getnames())
            revision = first.revision
            await first.flush(force=True)
            self.assertEqual(first.revision, revision)
            await first.shutdown()
            second = RoundtableRuntime(self.vendor, store=store, runtime_parent=parent)
            await second.initialize()
            restored = second.runtime_dir / "config/llm_secrets.json"
            self.assertEqual(restored.read_bytes(), secret)
            self.assertEqual(restored.stat().st_mode & 0o777, 0o600)
            await second.shutdown()

    async def test_unsafe_archive_is_rejected(self):
        payload = io.BytesIO()
        with tarfile.open(fileobj=payload, mode="w:gz") as tf:
            info = tarfile.TarInfo("runtime/../../escape")
            info.size = 1
            tf.addfile(info, io.BytesIO(b"x"))
        with tempfile.TemporaryDirectory() as target:
            with self.assertRaises(RoundtableRuntimeError):
                RoundtableRuntime._extract_archive(payload.getvalue(), Path(target))

    async def test_session_bootstrap_cookie_is_captured_and_restored(self):
        store = MemoryCheckpointStore()
        with tempfile.TemporaryDirectory() as parent:
            runtime = RoundtableRuntime(self.vendor, store=store, runtime_parent=parent)
            await runtime.initialize()
            from panel.web.app import Request
            request = Request.build(
                "POST", "/api/v1/session/bootstrap",
                {"Host": "127.0.0.1:8787", "Origin": "http://127.0.0.1:8787",
                 "Content-Type": "application/json"}, b"{}",
            )
            response = runtime.handle(request)
            self.assertEqual(response.status, 201)
            self.assertEqual(len(runtime.session_tokens), 1)
            runtime.mark_dirty()
            await runtime.flush(force=True)
            await runtime.shutdown()
            restored = RoundtableRuntime(self.vendor, store=store, runtime_parent=parent)
            await restored.initialize()
            self.assertEqual(len(restored.session_tokens), 1)
            await restored.shutdown()

    async def test_failed_checkpoint_marks_runtime_unavailable(self):
        with tempfile.TemporaryDirectory() as parent:
            runtime = RoundtableRuntime(self.vendor, store=FailingCheckpointStore(), runtime_parent=parent)
            await runtime.initialize()
            with self.assertRaises(RoundtableRuntimePersistenceError):
                await runtime.flush(force=True)
            self.assertTrue(runtime.unavailable)
            self.assertIn("database unavailable", runtime.persistence_error)
            runtime._dirty = False
            await runtime.shutdown()

    async def test_shutdown_closes_resources_when_final_flush_fails(self):
        with tempfile.TemporaryDirectory() as parent:
            runtime = RoundtableRuntime(self.vendor, store=FailOnSecondSaveStore(), runtime_parent=parent)
            await runtime.initialize()
            await runtime.flush(force=True)
            (runtime.runtime_dir / "legacy_data" / "shutdown.json").write_text("changed", encoding="utf-8")
            runtime.mark_dirty()
            with self.assertRaises(RoundtableRuntimePersistenceError):
                await runtime.shutdown()
            self.assertFalse(runtime._initialized)
            self.assertIsNone(runtime._monitor_conn)
            self.assertIsNone(runtime.pool)

    async def test_background_checkpoint_detects_external_worker_commit(self):
        store = MemoryCheckpointStore()
        with tempfile.TemporaryDirectory() as parent:
            runtime = RoundtableRuntime(self.vendor, store=store, runtime_parent=parent)
            await runtime.initialize()
            await runtime.flush(force=True)
            import sqlite3
            conn = sqlite3.connect(str(runtime.settings.db_path))
            conn.execute("CREATE TABLE background_marker (value TEXT)")
            conn.execute("INSERT INTO background_marker(value) VALUES ('worker-commit')")
            conn.commit()
            conn.close()
            task = asyncio.create_task(runtime.background_checkpoint(interval=0.25))
            await asyncio.sleep(0.65)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            self.assertEqual(runtime.revision, 2)
            await runtime.shutdown()

    async def test_missing_private_asset_key_rejects_startup(self):
        self.assets_patch.stop()
        with patch(
            "services.roundtable_runtime.prepare_roundtable_assets",
            side_effect=RuntimeError("missing private asset key"),
        ):
            with tempfile.TemporaryDirectory() as parent:
                runtime = RoundtableRuntime(self.vendor, store=MemoryCheckpointStore(), runtime_parent=parent)
                with self.assertRaises(RuntimeError):
                    await runtime.initialize()
                self.assertIsNone(runtime.pool)

    async def test_store_detects_hash_corruption_and_cas_conflict(self):
        corrupted = SimpleNamespace(payload=b"payload", revision=1, sha256="0" * 64, updated_at=None)
        with patch("roundtable_runtime_store.async_session", lambda: FakeStoreSession(corrupted)):
            with self.assertRaises(RoundtableRuntimeStoreError):
                await RoundtableRuntimeStore("x").load()
        with patch("roundtable_runtime_store.async_session", lambda: FakeStoreSession(rowcount=0)):
            with self.assertRaises(RoundtableRuntimeCASConflict):
                await RoundtableRuntimeStore("x").save(b"payload", 2)


if __name__ == "__main__":
    unittest.main()
