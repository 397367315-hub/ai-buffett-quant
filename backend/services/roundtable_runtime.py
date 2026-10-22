"""Lifecycle and durable checkpoint manager for the roundtable panel."""

from __future__ import annotations

import asyncio
import gzip
import hashlib
import io
import json
import logging
import os
from pathlib import Path
import shutil
import sqlite3
import stat
import tarfile
import tempfile
import threading
import traceback
from http.cookies import SimpleCookie
from typing import Any, Callable

from roundtable_runtime_store import RoundtableRuntimeCASConflict, RoundtableRuntimeStore
from services.roundtable_assets import prepare_roundtable_assets, encrypt_private_state, decrypt_private_state

logger = logging.getLogger(__name__)

class RoundtableRuntimeError(RuntimeError):
    pass


class RoundtableRuntimePersistenceError(RoundtableRuntimeError):
    """Checkpoint persistence failed; the runtime must stop accepting writes."""


class RoundtableRuntime:
    """Owns one in-process panel instance and its isolated filesystem.

    ``handle`` remains synchronous because the panel contract is synchronous;
    callers should invoke it from a threadpool.  ``initialize``, ``flush`` and
    ``shutdown`` are async so they can be composed with FastAPI lifespan.
    """

    def __init__(self, vendor_root: str | Path, *, store: RoundtableRuntimeStore | None = None,
                 key: str = "default", runtime_parent: str | Path | None = None,
                 before_build: Callable[[Any], None] | None = None) -> None:
        self.vendor_root = Path(vendor_root).resolve()
        self.store = store or RoundtableRuntimeStore(key)
        self.key = key
        self.runtime_parent = Path(runtime_parent or tempfile.gettempdir()).resolve()
        self.runtime_dir: Path | None = None
        self.app = None
        self.pool = None
        self.settings = None
        self.db = None
        self.revision: int | None = None
        self.session_tokens: dict[str, str] = {}
        self._dirty = False
        self._initialized = False
        self._stopped = False
        self._unavailable = False
        self.persistence_error: str | None = None
        self.persistence_diagnostic: str | None = None
        self._last_archive_digest: str | None = None
        self._archive_unchanged = False
        self._last_data_version: int | None = None
        self._monitor_conn: sqlite3.Connection | None = None
        self._monitor_lock = threading.Lock()
        self.asset_root: Path | None = None
        self._lock = asyncio.Lock()
        self.before_build = before_build
        self._blob_file_cache: dict[tuple[str, int, int], tuple[str, int]] = {}
        self._pending_blob_dir: Path | None = None
        self._blob_manifest: list[dict[str, Any]] = []
        self._known_blob_hashes: set[str] = set()

    @property
    def unavailable(self) -> bool:
        return self._unavailable

    @property
    def panel_parent(self) -> Path:
        return self.vendor_root / "records/roundtable/2026-10-18-圆桌面板设计"

    def vendor_import_root(self) -> Path:
        """Public hook for the host to add the vendor tree to import paths."""
        return self.panel_parent

    async def initialize(self) -> "RoundtableRuntime":
        async with self._lock:
            if self._initialized:
                return self
            self.vendor_root = self.vendor_root.resolve()
            if not self.vendor_root.is_dir():
                raise RoundtableRuntimeError(f"roundtable vendor root missing: {self.vendor_root}")
            checkpoint = await self.store.load()
            runtime_dir = Path(tempfile.mkdtemp(prefix="roundtable-runtime-", dir=str(self.runtime_parent)))
            try:
                if checkpoint is not None:
                    self._extract_archive(checkpoint.payload, runtime_dir)
                    self.revision = checkpoint.revision
                    self._last_archive_digest = hashlib.sha256(checkpoint.payload).hexdigest()
                    self._load_session_map(runtime_dir)
                    await self._restore_manifest_blobs(runtime_dir)
                panel_parent = self.panel_parent
                panel_file = panel_parent / "panel/api.py"
                if not panel_file.is_file():
                    raise RoundtableRuntimeError(f"roundtable panel missing: {panel_file}")
                # Prepare private skills/knowledge before importing any panel
                # module. Plain source material is never used in production.
                assets_root = await asyncio.to_thread(prepare_roundtable_assets, self.vendor_root)
                secret_backup = runtime_dir / "config/llm_secrets.aesgcm"
                if secret_backup.is_file():
                    private = runtime_dir / "config/llm_secrets.json"
                    private.write_bytes(decrypt_private_state(secret_backup.read_bytes()))
                    private.chmod(0o600)
                self.asset_root = Path(assets_root).resolve()
                # panel.config reads these at import time; set them before
                # importing panel.api/config so legacy writes stay in the
                # checkpointed runtime rather than the source tree.
                os.environ["ROUNDTABLE_RUNTIME"] = str(runtime_dir)
                os.environ["ROUNDTABLE_LEGACY_DATA_DIR"] = str(runtime_dir / "legacy_data")
                (runtime_dir / "legacy_data").mkdir(parents=True, exist_ok=True)
                os.environ["ROUNDTABLE_ASSET_ROOT"] = str(self.asset_root)
                os.environ["ROUNDTABLE_KB_ROOT"] = str(self.asset_root / "knowledge")
                # api.py expects its workbench parent on sys.path.
                import sys
                if str(panel_parent) not in sys.path:
                    sys.path.insert(0, str(panel_parent))
                python_paths = os.environ.get("PYTHONPATH", "").split(os.pathsep)
                if str(panel_parent) not in python_paths:
                    os.environ["PYTHONPATH"] = os.pathsep.join([str(panel_parent), *filter(None, python_paths)])
                from panel.api import build_app
                from panel.config import Settings
                panel_settings = Settings(
                    runtime_dir=runtime_dir,
                    source_root=self.vendor_root,
                    file_import_roots=(runtime_dir / "archive",),
                    host="127.0.0.1", port=8787,
                )
                if self.before_build is not None:
                    self.before_build(panel_settings)
                self.settings = panel_settings
                self.app, self.pool, _ = await asyncio.to_thread(build_app, panel_settings, True)
                self.db = self.app.db
                self.runtime_dir = runtime_dir
                self._monitor_conn = sqlite3.connect(
                    str(panel_settings.db_path), timeout=5.0, check_same_thread=False
                )
                self._initialized = True
                self._stopped = False
                self._dirty = True  # boot migration/registry/seed are state
                self._last_data_version = await asyncio.to_thread(self._read_data_version)
                return self
            except Exception:
                if self.pool is not None:
                    await asyncio.to_thread(self.pool.stop)
                self.pool = None
                if self._monitor_conn is not None:
                    self._monitor_conn.close()
                    self._monitor_conn = None
                shutil.rmtree(runtime_dir, ignore_errors=True)
                raise

    def handle(self, request: Any):
        if not self._initialized or self.app is None:
            raise RoundtableRuntimeError("roundtable runtime is not initialized")
        if self._unavailable and getattr(request, "method", "").upper() in {"POST", "PUT", "PATCH", "DELETE"}:
            raise RoundtableRuntimePersistenceError(self.persistence_error or "roundtable runtime unavailable")
        response = self.app.handle(request)
        if getattr(request, "method", "").upper() == "POST" and response.status < 400:
            self._capture_session_cookie(response)
            self._dirty = True
        return response

    def _capture_session_cookie(self, response: Any) -> None:
        headers = getattr(response, "headers", ())
        for name, value in headers:
            if str(name).lower() != "set-cookie":
                continue
            cookie = SimpleCookie()
            cookie.load(value)
            morsel = cookie.get("rt_local_session")
            if morsel:
                token = morsel.value
                # session_id is read from the panel DB, never from client input.
                row = self.db.one(
                    "SELECT session_id FROM local_sessions WHERE session_token_hash=?",
                    (self._token_hash(token),),
                )
                if row:
                    self.session_tokens[str(row["session_id"])] = token

    @staticmethod
    def _token_hash(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    def mark_dirty(self) -> None:
        if self._unavailable:
            raise RoundtableRuntimePersistenceError(self.persistence_error or "roundtable runtime unavailable")
        self._dirty = True

    async def background_checkpoint(self, interval: float = 15.0,
                                    stop_event: asyncio.Event | None = None) -> None:
        """Poll cross-thread SQLite writes and checkpoint at bounded intervals."""
        while not self._stopped and not self._unavailable:
            if stop_event is not None and stop_event.is_set():
                return
            await asyncio.sleep(max(0.25, float(interval)))
            try:
                version = await asyncio.to_thread(self._read_data_version)
                if self._last_data_version is not None and version != self._last_data_version:
                    self._dirty = True
                self._last_data_version = version
                await self.flush()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._unavailable = True
                self.persistence_error = self._format_persistence_error(exc)
                self.persistence_diagnostic = self.persistence_error
                logger.error("Roundtable checkpoint failed: %s", self.persistence_diagnostic)
                return

    @staticmethod
    def _format_persistence_error(exc: Exception) -> str:
        """Stable diagnostic without SQL parameters, cookies, or credentials."""
        frame = traceback.extract_tb(exc.__traceback__)[-1] if exc.__traceback__ else None
        where = f" @ {Path(frame.filename).name}:{frame.lineno} {frame.name}" if frame else ""
        # Exception text can contain SQL parameters or provider credentials.
        # Only class and code location are safe to expose or log.
        return f"{type(exc).__name__}{where}"

    def _read_data_version(self) -> int:
        if self._monitor_conn is None:
            return 0
        with self._monitor_lock:
            return int(self._monitor_conn.execute("PRAGMA data_version").fetchone()[0])

    async def flush(self, *, force: bool = False) -> int | None:
        if not self._initialized or self.runtime_dir is None:
            return self.revision
        async with self._lock:
            if not force and not self._dirty:
                return self.revision
            archive = await asyncio.to_thread(self._make_archive)
            try:
                await self._persist_pending_blobs()
            except RoundtableRuntimePersistenceError:
                self._cleanup_pending_blobs()
                raise
            except Exception as exc:
                self._cleanup_pending_blobs()
                self._unavailable = True
                self.persistence_error = f"roundtable checkpoint persistence failed: {exc}"
                raise RoundtableRuntimePersistenceError(self.persistence_error) from exc
            if self._archive_unchanged and self.revision is not None:
                self._dirty = False
                self._cleanup_pending_blobs()
                return self.revision
            try:
                next_revision = await self.store.save(archive, self.revision)
            except RoundtableRuntimeCASConflict as exc:
                self._unavailable = True
                self.persistence_error = "roundtable checkpoint conflict; reload runtime"
                raise RoundtableRuntimePersistenceError(self.persistence_error) from exc
            except Exception as exc:
                self._unavailable = True
                self.persistence_error = f"roundtable checkpoint persistence failed: {exc}"
                raise RoundtableRuntimePersistenceError(self.persistence_error) from exc
            self.revision = next_revision
            self._dirty = False
            self._cleanup_pending_blobs()
            return self.revision

    async def shutdown(self) -> None:
        async with self._lock:
            if self._stopped:
                return
            failure: Exception | None = None
            try:
                if self.pool is not None:
                    await asyncio.to_thread(self.pool.stop)
                if self._initialized and self._dirty and not self._unavailable:
                    archive = await asyncio.to_thread(self._make_archive)
                    await self._persist_pending_blobs()
                    if not self._archive_unchanged or self.revision is None:
                        self.revision = await self.store.save(archive, self.revision)
                    self._dirty = False
            except Exception as exc:
                self._unavailable = True
                self.persistence_error = f"roundtable checkpoint persistence failed: {exc}"
                failure = RoundtableRuntimePersistenceError(self.persistence_error)
            finally:
                self.pool = None
                if self.db is not None:
                    try:
                        await asyncio.to_thread(self.db.close)
                    except Exception as exc:
                        failure = failure or exc
                if self._monitor_conn is not None:
                    with self._monitor_lock:
                        self._monitor_conn.close()
                    self._monitor_conn = None
                self._stopped = True
                self._initialized = False
                self._cleanup_pending_blobs()
            if failure is not None:
                raise failure

    def _make_archive(self) -> bytes:
        from panel.services.workload import memory_work, release_temporary_memory
        with memory_work:
            release_temporary_memory()
            try:
                return self._make_archive_locked()
            finally:
                release_temporary_memory()

    def _make_archive_locked(self) -> bytes:
        assert self.runtime_dir is not None
        stage = Path(tempfile.mkdtemp(prefix="roundtable-checkpoint-", dir=str(self.runtime_parent)))
        try:
            db_copy = self.db.backup_now(stage / "backups", label="checkpoint")
            archive_root = stage / "runtime"
            archive_root.mkdir()
            shutil.copy2(db_copy, archive_root / "roundtable.sqlite3")
            blob_mode = callable(getattr(self.store, "save_blob", None)) and callable(getattr(self.store, "load_blob", None))
            manifest: list[dict[str, Any]] = []
            blob_dir = stage / "blobs"
            if blob_mode:
                blob_dir.mkdir()
            for name in ("evidence", "config", "archive", "data", "legacy_data"):
                src = self.runtime_dir / name
                if src.exists():
                    self._assert_tree_safe(src)
                    if blob_mode and name in {"evidence", "archive", "data", "legacy_data"}:
                        excluded = ("features.json", "features_compact.json", "seat_*.json", "_req*.json", ".*") if name == "legacy_data" else ()
                        self._stage_runtime_blobs(src, name, blob_dir, manifest, excluded=excluded)
                        continue
                    ignore = shutil.ignore_patterns("llm_secrets.json") if name == "config" else (
                        shutil.ignore_patterns("features.json", "features_compact.json", "seat_*.json", "_req*.json", ".*")
                        if name == "legacy_data" else None
                    )
                    shutil.copytree(src, archive_root / name, symlinks=False, ignore=ignore)
            private = self.runtime_dir / "config/llm_secrets.json"
            if private.is_file():
                dest = archive_root / "config/llm_secrets.aesgcm"
                # Keep an unchanged encrypted version stable between checkpoints.
                existing = self.runtime_dir / "config/llm_secrets.aesgcm"
                raw_secret = private.read_bytes()
                encrypted = existing.read_bytes() if existing.is_file() else None
                if encrypted is None or decrypt_private_state(encrypted) != raw_secret:
                    encrypted = encrypt_private_state(raw_secret)
                    existing.write_bytes(encrypted)
                    existing.chmod(0o600)
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(encrypted)
                dest.chmod(0o600)
            budget = self.runtime_dir / "llm_budget.json"
            if budget.exists():
                shutil.copy2(budget, archive_root / budget.name)
            refresh_status = self.runtime_dir / "legacy_refresh.json"
            if refresh_status.is_file():
                shutil.copy2(refresh_status, archive_root / refresh_status.name)
            if blob_mode:
                (archive_root / "artifact_manifest.json").write_text(
                    json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")), encoding="utf-8"
                )
                self._blob_manifest = manifest
                self._pending_blob_dir = blob_dir
            # Secrets are intentionally excluded; session tokens are private
            # state and are stored with restrictive permissions.
            (archive_root / "session_map.json").write_text(
                json.dumps(self.session_tokens, ensure_ascii=False, sort_keys=True), encoding="utf-8"
            )
            os.chmod(archive_root / "session_map.json", stat.S_IRUSR | stat.S_IWUSR)
            out = stage / "checkpoint.tar.gz"
            raw = io.BytesIO()
            with gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as gz:
                with tarfile.open(fileobj=gz, mode="w") as tf:
                    for path in sorted(archive_root.rglob("*")):
                        rel = path.relative_to(archive_root.parent)
                        info = tarfile.TarInfo(str(rel))
                        info.mtime = 0
                        if path.is_dir():
                            info.type = tarfile.DIRTYPE
                            info.mode = 0o700
                            tf.addfile(info)
                        elif path.is_file():
                            info.size = path.stat().st_size
                            info.mode = 0o600 if path.name == "session_map.json" else 0o640
                            with path.open("rb") as fh:
                                tf.addfile(info, fh)
                        else:
                            raise RoundtableRuntimeError("unsupported file in runtime checkpoint")
            payload = raw.getvalue()
            digest = hashlib.sha256(payload).hexdigest()
            self._archive_unchanged = self._last_archive_digest == digest and self.revision is not None
            self._last_archive_digest = digest
            out.write_bytes(payload)
            return payload
        finally:
            # Blob payloads are handed to the async store after this method;
            # retain only that small temporary directory on disk.
            if self._pending_blob_dir is not None and self._pending_blob_dir.parent == stage:
                keep = self._pending_blob_dir
                for item in stage.iterdir():
                    if item != keep:
                        if item.is_dir(): shutil.rmtree(item, ignore_errors=True)
                        else: item.unlink(missing_ok=True)
                try:
                    stage.rmdir()
                except OSError:
                    pass
            else:
                shutil.rmtree(stage, ignore_errors=True)

    def _stage_runtime_blobs(self, root: Path, root_name: str, blob_dir: Path,
                             manifest: list[dict[str, Any]], excluded: tuple[str, ...] = ()) -> None:
        """Stream immutable runtime files to gzip temp files, one file at a time."""
        assert self.runtime_dir is not None
        candidates = []
        for path in root.rglob("*"):
            # EvidenceStore and atomic JSON writers use hidden sibling files
            # while rename is in progress. They are not runtime artifacts and
            # must never enter a checkpoint or race stat/open.
            if any(part.startswith(".") or part.endswith(".tmp") for part in path.relative_to(root).parts):
                continue
            if not path.is_file():
                continue
            if any(path.match(pattern) or path.name == pattern for pattern in excluded):
                continue
            candidates.append(path)
        for path in sorted(candidates):
            rel = path.relative_to(self.runtime_dir).as_posix()
            self._validate_relative_path(rel)
            size = path.stat().st_size
            if size > 32 * 1024 * 1024:
                raise RoundtableRuntimeError(f"runtime blob exceeds 32MiB: {rel}")
            stat_result = path.stat()
            cache_key = (rel, int(stat_result.st_mtime_ns), int(size))
            cached = self._blob_file_cache.get(cache_key)
            if cached is not None:
                digest, cached_size = cached
                if digest in self._known_blob_hashes:
                    manifest.append({"path": rel, "sha256": digest, "size": int(cached_size), "mtime_ns": int(stat_result.st_mtime_ns)})
                    continue
            else:
                digest = ""
                cached_size = int(size)
                raw_path = blob_dir / (path.name + ".raw")
                hasher = hashlib.sha256()
                with path.open("rb") as source, raw_path.open("wb") as raw_target, gzip.GzipFile(fileobj=raw_target, mode="wb", mtime=0) as target:
                    while True:
                        chunk = source.read(1024 * 1024)
                        if not chunk:
                            break
                        hasher.update(chunk)
                        target.write(chunk)
                digest = hasher.hexdigest()
                final = blob_dir / digest
                raw_path.replace(final)
                self._blob_file_cache[cache_key] = (digest, cached_size)
            source_blob = blob_dir / digest
            if not source_blob.is_file():
                # A cached file may already have been persisted and cleaned;
                # regenerate it without retaining raw bytes in memory.
                hasher = hashlib.sha256()
                with path.open("rb") as source, source_blob.open("wb") as raw_target, gzip.GzipFile(fileobj=raw_target, mode="wb", mtime=0) as target:
                    while True:
                        chunk = source.read(1024 * 1024)
                        if not chunk: break
                        hasher.update(chunk); target.write(chunk)
                if hasher.hexdigest() != digest:
                    raise RoundtableRuntimeError(f"runtime blob changed while checkpointing: {rel}")
            manifest.append({"path": rel, "sha256": digest, "size": int(cached_size), "mtime_ns": int(stat_result.st_mtime_ns)})

    async def _persist_pending_blobs(self) -> None:
        if self._pending_blob_dir is None:
            return
        if not callable(getattr(self.store, "save_blob", None)):
            return
        seen: set[str] = set()
        for item in self._blob_manifest:
            digest = str(item["sha256"])
            if digest in seen:
                continue
            seen.add(digest)
            if digest in self._known_blob_hashes:
                continue
            if callable(getattr(self.store, "blob_exists", None)) and await self.store.blob_exists(digest):
                self._known_blob_hashes.add(digest)
                continue
            compressed_path = self._pending_blob_dir / digest
            if not compressed_path.is_file():
                raise RoundtableRuntimeError(f"missing staged runtime blob: {digest}")
            # Typical blobs are a few MB and this keeps the process bounded to
            # one payload at a time rather than the entire checkpoint.
            payload = compressed_path.read_bytes()
            await self.store.save_blob(digest, int(item["size"]), payload)
            self._known_blob_hashes.add(digest)

    def _cleanup_pending_blobs(self) -> None:
        if self._pending_blob_dir is not None:
            shutil.rmtree(self._pending_blob_dir.parent, ignore_errors=True)
        self._pending_blob_dir = None
        self._blob_manifest = []

    async def _restore_manifest_blobs(self, runtime_dir: Path) -> None:
        manifest_path = runtime_dir / "artifact_manifest.json"
        if not manifest_path.is_file():
            return
        if not callable(getattr(self.store, "load_blob", None)):
            return  # legacy embedded checkpoint
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise RoundtableRuntimeError("invalid roundtable artifact manifest") from exc
        if not isinstance(manifest, list) or len(manifest) > 100_000:
            raise RoundtableRuntimeError("invalid roundtable artifact manifest")
        expanded_total = 0
        allowed_roots = {"evidence", "archive", "data", "legacy_data"}
        for item in manifest:
            if not isinstance(item, dict):
                raise RoundtableRuntimeError("invalid roundtable artifact entry")
            rel = str(item.get("path", "")); digest = str(item.get("sha256", "")); size = int(item.get("size", -1)); mtime_ns = int(item.get("mtime_ns", 0))
            self._validate_relative_path(rel)
            if Path(rel).parts[0] not in allowed_roots or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest) or size < 0 or size > 32 * 1024 * 1024 or mtime_ns < 0:
                raise RoundtableRuntimeError("invalid roundtable artifact entry")
            expanded_total += size
            if expanded_total > 1_024 * 1024 * 1024:
                raise RoundtableRuntimeError("roundtable artifacts expand beyond limit")
            blob = await self.store.load_blob(digest)
            if blob is None:
                raise RoundtableRuntimeError(f"missing roundtable runtime blob: {digest}")
            compressed = getattr(blob, "payload", blob)
            dest = runtime_dir / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            total = 0; hasher = hashlib.sha256()
            try:
                with gzip.GzipFile(fileobj=io.BytesIO(bytes(compressed)), mode="rb") as source, dest.open("wb") as target:
                    while True:
                        chunk = source.read(1024 * 1024)
                        if not chunk: break
                        total += len(chunk); hasher.update(chunk); target.write(chunk)
                        if total > size or total > 32 * 1024 * 1024 or total + (expanded_total - size) > 1_024 * 1024 * 1024:
                            raise RoundtableRuntimeError("roundtable runtime blob exceeds declared limits")
            except (OSError, EOFError) as exc:
                dest.unlink(missing_ok=True)
                raise RoundtableRuntimeError("invalid roundtable runtime blob") from exc
            if total != size or hasher.hexdigest() != digest:
                dest.unlink(missing_ok=True)
                raise RoundtableRuntimeError(f"roundtable runtime blob verification failed: {rel}")
            self._known_blob_hashes.add(digest)
            if mtime_ns:
                os.utime(dest, ns=(mtime_ns, mtime_ns))
            stat_result = dest.stat()
            self._blob_file_cache[(rel, int(stat_result.st_mtime_ns), int(size))] = (digest, int(size))

    @staticmethod
    def _validate_relative_path(rel: str) -> None:
        path = Path(rel)
        if not rel or path.is_absolute() or ".." in path.parts or path.parts[-1] in {"", "."}:
            raise RoundtableRuntimeError("unsafe roundtable artifact path")

    @staticmethod
    def _extract_archive(payload: bytes, target: Path) -> None:
        if len(payload) > 512 * 1024 * 1024:
            raise RoundtableRuntimeError("roundtable checkpoint is too large")
        stage = Path(tempfile.mkdtemp(prefix="roundtable-archive-", dir=str(target.parent)))
        try:
            archive = stage / "checkpoint.tar.gz"
            archive.write_bytes(payload)
            with tarfile.open(archive, "r:gz") as tf:
                members = tf.getmembers()
                if len(members) > 100_000:
                    raise RoundtableRuntimeError("too many checkpoint members")
                expanded = 0
                for member in members:
                    name = Path(member.name)
                    if name.is_absolute() or ".." in name.parts or not (member.isdir() or member.isreg()):
                        raise RoundtableRuntimeError("unsafe roundtable checkpoint path")
                    expanded += int(member.size or 0)
                    if expanded > 1_024 * 1024 * 1024:
                        raise RoundtableRuntimeError("checkpoint expands beyond limit")
                unpacked = stage / "unpacked"
                for member in members:
                    dest = unpacked / Path(member.name)
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    if member.isdir():
                        dest.mkdir(exist_ok=True)
                    else:
                        source = tf.extractfile(member)
                        if source is None:
                            raise RoundtableRuntimeError("invalid checkpoint member")
                        with dest.open("wb") as fh:
                            shutil.copyfileobj(source, fh, length=1024 * 1024)
            root = stage / "unpacked" / "runtime"
            if not root.is_dir() or not (root / "roundtable.sqlite3").is_file():
                raise RoundtableRuntimeError("invalid roundtable checkpoint archive")
            for item in root.iterdir():
                dest = target / item.name
                if item.is_dir():
                    shutil.copytree(item, dest, symlinks=False)
                else:
                    shutil.copy2(item, dest)
        except (tarfile.TarError, OSError, ValueError) as exc:
            raise RoundtableRuntimeError("invalid roundtable checkpoint archive") from exc
        finally:
            shutil.rmtree(stage, ignore_errors=True)

    @staticmethod
    def _assert_tree_safe(root: Path) -> None:
        for path in [root, *root.rglob("*")]:
            if path.is_symlink():
                raise RoundtableRuntimeError(f"unsafe runtime path: {path}")
            if path != root and any(part.startswith(".") or part.endswith(".tmp") for part in path.relative_to(root).parts):
                continue
            if not (path.is_dir() or path.is_file()):
                raise RoundtableRuntimeError(f"unsafe runtime path: {path}")

    def _load_session_map(self, runtime_dir: Path) -> None:
        path = runtime_dir / "session_map.json"
        if not path.is_file():
            return
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise RoundtableRuntimeError("invalid roundtable session map")
        self.session_tokens = {str(k): str(v) for k, v in data.items() if str(k) and str(v)}
