"""Host lifecycle and shared provider bindings for the independent roundtable."""
from __future__ import annotations

import asyncio
import concurrent.futures
import hashlib
import os
from pathlib import Path

from config import settings
from market_data.numcat.gateway import numcat_gateway
from services.roundtable_runtime import RoundtableRuntime


VENDOR_ROOT = Path(__file__).resolve().parents[1] / "roundtable_workspace"
_runtime: RoundtableRuntime | None = None
_checkpoint_task: asyncio.Task | None = None
_initialize_lock = asyncio.Lock()


def _bind_shared_providers(loop: asyncio.AbstractEventLoop, panel_settings) -> None:
    from panel.adapters.shared_hooks import configure_shared_numcat

    def query_sync(apiname: str, params: dict, fields: str) -> dict:
        task = asyncio.run_coroutine_threadsafe(
            numcat_gateway.query(apiname, params=params, fields=fields, cache_ttl=60), loop,
        )
        try:
            return task.result(timeout=90)
        except concurrent.futures.TimeoutError as exc:
            task.cancel()
            raise RuntimeError("共享猫爪数据查询超时") from exc
        except Exception as exc:
            raise RuntimeError("共享猫爪数据暂不可用：" + type(exc).__name__) from exc

    configure_shared_numcat(query_sync, lambda: numcat_gateway.configured)
    # The shared LLM binding is implemented by the provider adapter, keeping
    # provider credentials in the same host Settings as the existing system.
    from panel.adapters.llm_base import configure_shared_llm
    configure_shared_llm(settings.deepseek_base_url, settings.deepseek_model,
                         lambda: settings.deepseek_api_key)


async def get_roundtable_runtime() -> RoundtableRuntime:
    global _runtime, _checkpoint_task
    async with _initialize_lock:
        if _runtime is None:
            loop = asyncio.get_running_loop()
            key = "roundtable-v1:" + hashlib.sha256(settings.admin_username.encode()).hexdigest()[:20]
            _runtime = RoundtableRuntime(
                VENDOR_ROOT, key=key,
                before_build=lambda panel_settings: _bind_shared_providers(loop, panel_settings),
            )
        await _runtime.initialize()
        if _checkpoint_task is None or _checkpoint_task.done():
            _checkpoint_task = asyncio.create_task(
                _runtime.background_checkpoint(
                    interval=max(2.0, float(os.environ.get("ROUNDTABLE_CHECKPOINT_INTERVAL", "15")))
                ), name="roundtable-checkpoint",
            )
        return _runtime


async def close_roundtable_runtime() -> None:
    global _runtime, _checkpoint_task
    if _checkpoint_task is not None:
        _checkpoint_task.cancel()
        try:
            await _checkpoint_task
        except asyncio.CancelledError:
            pass
        _checkpoint_task = None
    if _runtime is not None:
        from services.roundtable_legacy import roundtable_legacy_refresh
        await roundtable_legacy_refresh.shutdown(_runtime)
        await _runtime.shutdown()
        _runtime = None
