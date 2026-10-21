"""SQLite 底座（BASE-003）。

- 每个线程独立连接；连接级 PRAGMA：foreign_keys=ON、busy_timeout=5000、WAL（文件库）；
- 连接用 autocommit 模式（isolation_level=None），写事务一律走 txn()（BEGIN IMMEDIATE），
  失败整体 ROLLBACK，不留半账；
- 迁移：schema_migrations(version, checksum, applied_at, description)；已应用迁移的文件被改动 →
  拒绝启动（MigrationChecksumMismatch），不重算校验和去抹平；
- 迁移前对已有数据的库先用 SQLite backup API 做一致性备份并校验可打开，再升级；
- 备份 / 恢复演练：backup_now() / restore_to() / integrity_report()。

注意：SQLite 需要支持文件锁的本地磁盘；网络盘/某些共享挂载会报 disk I/O error。
"""
from __future__ import annotations

import hashlib
import re
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path

from ..domain import clock

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"
_MIG_RE = re.compile(r"^(\d{3})_([a-z0-9_]+)\.sql$")


class MigrationChecksumMismatch(RuntimeError):
    pass


class DatabaseUnavailable(RuntimeError):
    pass


def file_checksum(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def list_migrations() -> list[tuple[int, str, Path]]:
    out = []
    for p in sorted(MIGRATIONS_DIR.glob("*.sql")):
        m = _MIG_RE.match(p.name)
        if m:
            out.append((int(m.group(1)), m.group(2), p))
    return out


def connect(path: Path | str) -> sqlite3.Connection:
    path = str(path)
    memory = path == ":memory:" or path.startswith("file::memory:")
    if not memory:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=5.0, isolation_level=None, check_same_thread=False,
                           uri=path.startswith("file:"))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    if not memory:
        try:
            conn.execute("PRAGMA journal_mode = WAL")
        except sqlite3.OperationalError:
            pass
        conn.execute("PRAGMA synchronous = NORMAL")
    return conn


class Database:
    """线程本地连接池 + 迁移 + 备份。"""

    def __init__(self, path: Path | str):
        self.path = str(path)
        self._local = threading.local()
        self._write_lock = threading.RLock()     # 进程内写入串行化（SQLite 本身也只有一个写者）

    def conn(self) -> sqlite3.Connection:
        c = getattr(self._local, "conn", None)
        if c is None:
            try:
                c = connect(self.path)
            except sqlite3.OperationalError as ex:
                raise DatabaseUnavailable(str(ex)) from ex
            self._local.conn = c
        return c

    def close(self) -> None:
        c = getattr(self._local, "conn", None)
        if c is not None:
            c.close()
            self._local.conn = None

    @contextmanager
    def txn(self):
        """写事务：BEGIN IMMEDIATE … COMMIT；异常 ROLLBACK。可重入（嵌套时只最外层提交）。"""
        c = self.conn()
        depth = getattr(self._local, "depth", 0)
        if depth:
            self._local.depth = depth + 1
            try:
                yield c
            finally:
                self._local.depth -= 1
            return
        with self._write_lock:
            for attempt in range(50):
                try:
                    c.execute("BEGIN IMMEDIATE")
                    break
                except sqlite3.OperationalError as ex:
                    if "locked" in str(ex) or "busy" in str(ex):
                        time.sleep(0.05 * (attempt + 1))
                        continue
                    raise DatabaseUnavailable(str(ex)) from ex
            else:
                raise DatabaseUnavailable("数据库忙，无法开始写事务")
            self._local.depth = 1
            try:
                yield c
                c.execute("COMMIT")
            except BaseException:
                if c.in_transaction:
                    c.execute("ROLLBACK")
                raise
            finally:
                self._local.depth = 0

    def query(self, sql: str, params=()) -> list[sqlite3.Row]:
        return self.conn().execute(sql, params).fetchall()

    def one(self, sql: str, params=()) -> sqlite3.Row | None:
        return self.conn().execute(sql, params).fetchone()

    def scalar(self, sql: str, params=()):
        r = self.conn().execute(sql, params).fetchone()
        return None if r is None else r[0]

    # ── 迁移 ──
    def applied(self) -> dict[int, str]:
        c = self.conn()
        has = c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_migrations'").fetchone()
        if not has:
            return {}
        cols = {r[1] for r in c.execute("PRAGMA table_info(schema_migrations)")}
        if "checksum" not in cols or "description" not in cols:
            raise MigrationChecksumMismatch("schema_migrations 不是 v1 契约结构：疑似旧开发库，拒绝在其上升级")
        return {int(r[0]): r[1] for r in c.execute("SELECT version, checksum FROM schema_migrations")}

    def migrate(self, backup_dir: Path | None = None) -> list[int]:
        """应用未执行的迁移，返回新应用的版本号。"""
        applied = self.applied()
        migs = list_migrations()
        for ver, name, p in migs:
            if ver in applied and applied[ver] != file_checksum(p):
                raise MigrationChecksumMismatch(
                    f"迁移 {p.name} 已应用但文件内容变了（库内 {applied[ver][:12]}… ≠ 文件 {file_checksum(p)[:12]}…）")
        pending = [(v, n, p) for v, n, p in migs if v not in applied]
        if not pending:
            return []
        if applied and backup_dir is not None:
            self.backup_now(backup_dir, label=f"pre-migration-{pending[0][0]:03d}")
        done = []
        c = self.conn()
        with self._write_lock:
            for ver, name, p in pending:
                sql = p.read_text(encoding="utf-8")
                checksum = file_checksum(p)
                stmt = (f"INSERT INTO schema_migrations(version, checksum, applied_at, description) "
                        f"VALUES({ver}, '{checksum}', '{clock.utc_iso()}', '{name}');")
                try:
                    c.executescript("BEGIN IMMEDIATE;\n" + sql + "\n" + stmt + "\nCOMMIT;")
                except Exception:
                    if c.in_transaction:
                        c.execute("ROLLBACK")
                    raise
                done.append(ver)
        return done

    # ── 备份 / 恢复 ──
    def backup_now(self, backup_dir: Path, label: str = "backup") -> Path:
        """SQLite backup API：一致性快照（含 WAL 中已提交的页），不是直接复制主文件。"""
        backup_dir.mkdir(parents=True, exist_ok=True)
        ts = clock.now().strftime("%Y%m%dT%H%M%S%fZ")
        dst = backup_dir / f"roundtable-{label}-{ts}.sqlite3"
        out = sqlite3.connect(str(dst))
        try:
            self.conn().backup(out)
            r = out.execute("PRAGMA integrity_check").fetchone()[0]
            if r != "ok":
                raise DatabaseUnavailable(f"备份完整性检查失败：{r}")
        finally:
            out.close()
        return dst

    def integrity_report(self) -> dict:
        return integrity_report(self.conn())


def integrity_report(c: sqlite3.Connection) -> dict:
    ic = c.execute("PRAGMA integrity_check").fetchone()[0]
    fk = [tuple(r) for r in c.execute("PRAGMA foreign_key_check").fetchall()]
    counts = {}
    for (name,) in c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"):
        counts[name] = c.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]
    versions = [r[0] for r in c.execute("SELECT version FROM schema_migrations ORDER BY version")] \
        if "schema_migrations" in counts else []
    return {"integrity_check": ic, "foreign_key_errors": fk, "row_counts": counts, "schema_versions": versions}


def restore_to(backup_file: Path, target: Path) -> dict:
    """把备份恢复到一个新的（空）位置并核对；绝不覆盖正在使用的库。"""
    target = Path(target)
    if target.exists():
        raise FileExistsError(f"恢复目标已存在，拒绝覆盖：{target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    src = sqlite3.connect(str(backup_file))
    dst = sqlite3.connect(str(target))
    try:
        src.backup(dst)
    finally:
        src.close()
        dst.close()
    c = connect(target)
    try:
        return integrity_report(c)
    finally:
        c.close()


_db: Database | None = None
_db_lock = threading.Lock()


def get_db() -> Database:
    global _db
    with _db_lock:
        if _db is None:
            from ..config import get_settings
            s = get_settings()
            s.ensure_dirs()
            _db = Database(s.db_path)
            _db.migrate(backup_dir=s.backup_dir)
        return _db


def reset_db(db: Database | None) -> None:
    """测试/验收：切换进程内数据库实例。"""
    global _db
    with _db_lock:
        _db = db
