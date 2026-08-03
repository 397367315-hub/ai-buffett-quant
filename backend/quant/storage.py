"""Small, atomic JSON stores used by the quantitative strategy workspace."""

from __future__ import annotations

import copy
import json
import os
import threading
import uuid
from pathlib import Path
from typing import Any, Callable

from config import settings


DEFAULT_DOCUMENTS: dict[str, dict] = {
    "strategies": {"version": 1, "strategies": []},
    "signals": {
        "version": 1,
        "generated_at": None,
        "data_date": None,
        "source": "none",
        "is_realtime": False,
        "stale": False,
        "warning": None,
        "scanned_stocks": 0,
        "signals": [],
    },
    "signal_history": {"version": 1, "scans": []},
    "paper_portfolio": {
        "version": 1,
        "account": {
            "initial_capital": 100000.0,
            "available_cash": 100000.0,
            "total_value": 100000.0,
            "total_return_pct": 0.0,
        },
        "holdings": [],
        "history": [],
        "updated_at": None,
        "price_source": "none",
        "price_updated_at": None,
        "price_is_realtime": False,
        "price_warning": None,
    },
    "jobs": {"version": 1, "scan": {}, "backtest": {}},
    "market_snapshot": {
        "version": 1,
        "stocks": [],
        "total": 0,
        "source": "none",
        "data_date": None,
        "is_realtime": False,
        "fetched_at": None,
        "complete": False,
    },
}


class QuantJsonStore:
    """Thread-safe JSON persistence with crash-resistant file replacement."""

    def __init__(self, root: str | Path | None = None):
        configured = str(settings.quant_data_dir or "").strip()
        self.root = Path(root or configured or Path(__file__).with_name("data")).resolve()
        self.results_dir = self.root / "backtest_results"
        self._lock = threading.RLock()
        self.ensure()

    def ensure(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        self.results_dir.mkdir(parents=True, exist_ok=True)
        for name, default in DEFAULT_DOCUMENTS.items():
            path = self._path(name)
            if not path.exists():
                self._write_path(path, default)

    def _path(self, name: str) -> Path:
        if name not in DEFAULT_DOCUMENTS:
            raise KeyError(f"未知量化数据文档: {name}")
        return self.root / f"{name}.json"

    @staticmethod
    def _read_path(path: Path) -> dict:
        try:
            with path.open("r", encoding="utf-8") as handle:
                value = json.load(handle)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"量化数据文件损坏: {path.name}") from exc
        if not isinstance(value, dict):
            raise RuntimeError(f"量化数据文件格式错误: {path.name}")
        return value

    @staticmethod
    def _write_path(path: Path, value: dict) -> None:
        temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2, allow_nan=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)

    def read(self, name: str) -> dict:
        with self._lock:
            path = self._path(name)
            if not path.exists():
                self._write_path(path, DEFAULT_DOCUMENTS[name])
            return copy.deepcopy(self._read_path(path))

    def write(self, name: str, value: dict) -> dict:
        with self._lock:
            self._write_path(self._path(name), value)
            return copy.deepcopy(value)

    def update(self, name: str, mutator: Callable[[dict], Any]) -> dict:
        with self._lock:
            document = self.read(name)
            mutator(document)
            self._write_path(self._path(name), document)
            return copy.deepcopy(document)

    def write_backtest_result(self, job_id: str, value: dict) -> Path:
        if not job_id.startswith("bt_"):
            raise ValueError("无效的回测任务编号")
        path = self.results_dir / f"{job_id}.json"
        with self._lock:
            self._write_path(path, value)
        return path

    def read_backtest_result(self, job_id: str) -> dict | None:
        if not job_id.startswith("bt_"):
            return None
        path = self.results_dir / f"{job_id}.json"
        with self._lock:
            return copy.deepcopy(self._read_path(path)) if path.exists() else None

    def list_backtest_results(self) -> list[dict]:
        results = []
        with self._lock:
            for path in sorted(self.results_dir.glob("bt_*.json"), reverse=True):
                try:
                    results.append(self._read_path(path))
                except RuntimeError:
                    continue
        return results


quant_store = QuantJsonStore()
