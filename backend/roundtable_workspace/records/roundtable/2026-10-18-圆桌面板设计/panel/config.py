"""运行配置：路径、端口、允许的 Host/Origin、阶段开关。

所有路径都能用环境变量覆盖；默认值按 WORK_ROOT 的相对结构推出来，不写死任何用户绝对路径。
凭据不在这里——Numcat 密钥只由 meoz CLI 从 ~/.codex/credentials/numcat.env 或
NUMCAT_API_KEY / NUMCAT_ENV_FILE 读取，本进程既不读也不打印。
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

PANEL_DIR = Path(__file__).resolve().parent                 # .../panel
WORKBENCH = PANEL_DIR.parent                                # .../2026-10-18-圆桌面板设计
WORK_ROOT = PANEL_DIR.parents[3]                            # .../交易圆桌_十三席_WORK

# Render/host startup unpacks private, possibly encrypted assets before the
# first panel import.  Resolve these once from the environment so every module
# sees the same immutable asset tree.
ASSET_ROOT = Path(os.environ.get("ROUNDTABLE_ASSET_ROOT") or WORK_ROOT).expanduser()
RUNTIME_ROOT = Path(os.environ.get("ROUNDTABLE_RUNTIME") or (WORK_ROOT / "runtime")).expanduser()
CONTRACTS_ROOT = Path(os.environ.get("ROUNDTABLE_CONTRACTS_ROOT") or (WORK_ROOT / "contracts")).expanduser()

# Contracts are public schemas shipped with the vendor; private asset bundles
# intentionally contain only skills/knowledge and must not replace them.
CONTRACTS_DIR = CONTRACTS_ROOT
SKILLS_DIR = ASSET_ROOT / "skills"
FRONTEND_DIST = WORKBENCH / "frontend" / "dist"
# Generated legacy snapshots are runtime state.  The checked-in panel/data
# tree remains read-only fixtures and is never a production write target.
LEGACY_DATA_DIR = RUNTIME_ROOT / "legacy_data"


SOURCE_PACKAGE_NAME = "交易圆桌_AI交接包_20261019"


def _default_source_root() -> Path:
    """只读源包：默认在 ~/Desktop；工作区与源包同在一个父目录时（如挂载环境）用相邻目录。"""
    home = Path("~/Desktop").expanduser() / SOURCE_PACKAGE_NAME
    if home.is_dir():
        return home
    sibling = WORK_ROOT.parent / SOURCE_PACKAGE_NAME
    return sibling if sibling.is_dir() else home


def _env_path(name: str, default: Path) -> Path:
    v = os.environ.get(name)
    return Path(v).expanduser() if v else default


@dataclass
class Settings:
    runtime_dir: Path = field(default_factory=lambda: RUNTIME_ROOT)
    db_path: Path | None = None
    source_root: Path = field(default_factory=lambda: _env_path("ROUNDTABLE_SOURCE_ROOT", _default_source_root()))
    meoz_cli: Path = field(default_factory=lambda: _env_path(
        "MEOZ_CLI", Path("~/Downloads/meoz-2/scripts/meoz.py").expanduser()))
    host: str = "127.0.0.1"
    port: int = 8787
    dev_origins: tuple[str, ...] = ()            # vite dev 服务器（显式开启才加入）
    session_ttl_hours: int = 12
    max_body_bytes: int = 1 << 20                # 1 MiB
    max_import_body_bytes: int = 24 << 20        # legacy 导入的 HTTP 体上限（转义后）
    max_import_raw_bytes: int = 4 << 20          # legacy 导入原始 UTF-8 合计上限
    max_active_jobs: int = 4
    max_waiting_jobs: int = 100
    provider_max_retry: int = 2
    provider_timeout_seconds: float = 90.0
    # 旧 refresh.py 的日线缓存/分红年数表（panel/data/）：生产可复用；验收为隔离设为 False
    use_legacy_data_cache: bool = True
    validate_responses: bool = field(default_factory=lambda: os.environ.get("ROUNDTABLE_VALIDATE_RESPONSES") == "1")
    test_mode: bool = field(default_factory=lambda: os.environ.get("ROUNDTABLE_TEST_MODE") == "1")
    # file_import 只从这里预配置的只读归档读取，客户端不能传路径
    file_import_roots: tuple[Path, ...] = ()

    def __post_init__(self) -> None:
        if self.db_path is None:
            self.db_path = _env_path("ROUNDTABLE_DB", self.runtime_dir / "roundtable.sqlite3")
        if not self.file_import_roots:
            roots = os.environ.get("ROUNDTABLE_FILE_IMPORT_ROOTS")
            if roots:
                self.file_import_roots = tuple(Path(p).expanduser() for p in roots.split(os.pathsep) if p)
            else:
                self.file_import_roots = (
                    self.runtime_dir / "legacy_data" / "daily",
                    self.source_root / "records/roundtable/2026-10-19-席位回测/data/daily",
                )

    # ── 派生路径 ──
    @property
    def evidence_dir(self) -> Path:
        return self.runtime_dir / "evidence"

    @property
    def backup_dir(self) -> Path:
        return self.runtime_dir / "backups"

    @property
    def work_dir(self) -> Path:
        return self.runtime_dir / "work"

    @property
    def audit_dir(self) -> Path:
        return self.runtime_dir / "audit"

    def allowed_hosts(self) -> set[str]:
        hosts = {f"127.0.0.1:{self.port}", f"localhost:{self.port}"}
        if self.port == 80:
            hosts |= {"127.0.0.1", "localhost"}
        return hosts

    def allowed_origins(self) -> set[str]:
        o = {f"http://127.0.0.1:{self.port}", f"http://localhost:{self.port}"}
        o |= set(self.dev_origins)
        return o

    def ensure_dirs(self) -> None:
        for p in (self.runtime_dir, self.evidence_dir, self.backup_dir, self.work_dir, self.audit_dir):
            p.mkdir(parents=True, exist_ok=True)


# 阶段开关：P0–P3 为默认完整交付；P4 由提醒能力闸门决定（见 services/alert_service.py）。
ENABLED_PHASES = ("P0", "P1", "P2", "P3")

_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings


def set_settings(s: Settings) -> Settings:
    """测试与验收 runner 用：替换进程内配置（每个用例独立 runtime）。"""
    global _settings
    _settings = s
    return s
