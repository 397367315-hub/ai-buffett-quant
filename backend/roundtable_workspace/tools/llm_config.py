#!/usr/bin/env python3
"""查看 / 切换模型（ADR-008 / ADR-009）：写运行目录下的 config/llm.json，不改代码、不碰密钥。

想存多份、随时切换，用「存档」：
    python3 tools/llm_config.py profiles                    # 已保存的模型存档（当前那份带 *）
    python3 tools/llm_config.py use "OpenAI 主力"            # 设为当前（按名字或 id）
    python3 tools/llm_config.py save --name "DeepSeek 官方" --provider deepseek --model deepseek-flash
    python3 tools/llm_config.py drop <id>                    # 删掉一份存档

改当前存档（不新建）：
    python3 tools/llm_config.py set --provider openai_compatible --model gpt-4.1 \
        --base-url https://api.openai.com/v1 --api-key-env OPENAI_API_KEY
    python3 tools/llm_config.py show                        # 当前配置与状态（不含密钥）
    python3 tools/llm_config.py providers                   # 已注册的供应商
    python3 tools/llm_config.py reset                       # 删掉配置文件，回到默认（deepseek）

密钥本身不写进这些文件：只写「去哪个环境变量里找」（--api-key-env）；页面里填的密钥另有私有文件。
运行目录默认同服务（ROUNDTABLE_RUNTIME）。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

WORK_ROOT = Path(__file__).resolve().parents[1]
WB = WORK_ROOT / "records/roundtable/2026-10-18-圆桌面板设计"
sys.path.insert(0, str(WB))

from panel.adapters import llm_registry as reg   # noqa: E402


def runtime_dir(arg: str | None) -> Path:
    if arg:
        return Path(arg).expanduser().resolve()
    return Path(os.environ.get("ROUNDTABLE_RUNTIME") or (WORK_ROOT / "runtime")).expanduser().resolve()


def settings(rt: Path):
    return SimpleNamespace(runtime_dir=rt)


def _print_status(rt: Path) -> dict:
    st = reg.status(settings(rt))
    out = {k: st.get(k) for k in ("provider", "label", "model", "base_url", "enabled", "reason", "key_source",
                                  "profile_id", "profile_name", "supports_search", "budget", "config_path",
                                  "config_source", "config_error")}
    out["providers"] = [p["id"] for p in st.get("providers") or []]
    out["profiles"] = [{"id": p["id"], "name": p["name"], "provider": p["provider"], "model": p["model"],
                        "active": p["active"], "enabled": p["enabled"], "reason": p["reason"]}
                       for p in st.get("profiles") or []]
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return st


def _find(st: dict, key: str) -> str:
    for p in st.get("profiles") or []:
        if key in (p["id"], p["name"]):
            return p["id"]
    raise SystemExit(f"没有这个存档：{key}（python3 tools/llm_config.py profiles 查看）")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="查看或切换模型（支持多份存档）")
    ap.add_argument("--runtime", help="运行目录（默认 ROUNDTABLE_RUNTIME 或 WORK_ROOT/runtime）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("providers")
    sub.add_parser("show")
    sub.add_parser("profiles")
    p_use = sub.add_parser("use")
    p_use.add_argument("name", help="存档名或 id")
    p_save = sub.add_parser("save")
    p_save.add_argument("--name", required=True)
    p_save.add_argument("--provider", required=True)
    p_save.add_argument("--model")
    p_save.add_argument("--base-url")
    p_save.add_argument("--api-key-env")
    p_save.add_argument("--daily-token-limit", type=int)
    p_save.add_argument("--activate", dest="activate", action="store_true", default=None)
    p_save.add_argument("--no-activate", dest="activate", action="store_false")
    p_drop = sub.add_parser("drop")
    p_drop.add_argument("name", help="存档名或 id")
    p_set = sub.add_parser("set")
    p_set.add_argument("--provider", required=True)
    p_set.add_argument("--model")
    p_set.add_argument("--base-url")
    p_set.add_argument("--api-key-env")
    p_set.add_argument("--daily-token-limit", type=int)
    p_set.add_argument("--json-mode", dest="json_mode", action="store_true", default=None)
    p_set.add_argument("--no-json-mode", dest="json_mode", action="store_false")
    sub.add_parser("reset")
    a = ap.parse_args(argv)
    rt = runtime_dir(a.runtime)
    st = reg.status(settings(rt))

    if a.cmd == "providers":
        rows = []
        for pid, cls in reg.PROVIDERS.items():
            rows.append({"id": pid, "label": cls.label, "supports_search": cls.supports_search,
                         "default_model": cls.default_model or None,
                         "default_base_url": cls.default_base_url or None, "key_env": cls.default_key_env})
        print(json.dumps(rows, ensure_ascii=False, indent=1))
        return 0
    if a.cmd == "show":
        _print_status(rt)
        return 0
    if a.cmd == "profiles":
        for p in st.get("profiles") or []:
            print(f"{'*' if p['active'] else ' '} {p['id']:<14} {p['name']:<20} {p['provider']} · "
                  f"{p['model'] or '默认模型'} · {'可用' if p['enabled'] else p['reason']}")
        return 0
    if a.cmd == "use":
        reg.activate_profile(settings(rt), _find(st, a.name))
        _print_status(rt)
        return 0
    if a.cmd == "save":
        prof, _ = reg.save_profile(settings(rt), {
            "name": a.name, "provider": a.provider, "model": a.model or "", "base_url": a.base_url or "",
            "api_key_env": a.api_key_env or "", "daily_token_limit": a.daily_token_limit},
            activate=a.activate)
        print(f"已保存存档 {prof['id']}：{prof['name']}")
        _print_status(rt)
        return 0
    if a.cmd == "drop":
        reg.delete_profile(settings(rt), _find(st, a.name))
        _print_status(rt)
        return 0
    if a.cmd == "reset":
        f = rt / "config" / "llm.json"
        if f.exists():
            f.unlink()
        _print_status(rt)
        return 0

    # set：改当前存档
    patch = {"provider": a.provider}
    for key, val in (("model", a.model), ("base_url", a.base_url), ("api_key_env", a.api_key_env),
                     ("daily_token_limit", a.daily_token_limit), ("json_mode", a.json_mode)):
        if val is None or (isinstance(val, str) and not val.strip()):
            continue
        patch[key] = val
    cfg = reg.update_active(settings(rt), patch)
    print(f"已更新当前存档 {cfg.get('_profile_name')}（{cfg.get('_profile_id')}）")
    _print_status(rt)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
