"""模型供应商注册表与「存档」（ADR-008 / ADR-009）：换模型不改代码，随时切换。

三种说法在这里要分清：
- **供应商**（provider）：`deepseek` / `openai_compatible` / `anthropic`，对应一个请求形状；
- **存档**（profile）：一份可保存的配置（名字 + 供应商 + 模型 + 接口地址 + 密钥环境变量 + 预算 + JSON 模式），
  可以存很多份，随时切换当前用哪一份（像 CC Switch 那样）；
- **当前存档**（active）：所有调用只读它，改完立即生效，不用重启。

配置来源（后者覆盖前者）：
1. 代码里的默认值：`provider=deepseek`；
2. 运行目录下的 `config/llm.json`：
   ```json
   {"version": 1, "active": "p-1a2b3c4d",
    "profiles": [{"id": "p-1a2b3c4d", "name": "DeepSeek 官方", "provider": "deepseek",
                  "model": "deepseek-flash", "base_url": "", "api_key_env": "DEEPSEEK_API_KEY",
                  "daily_token_limit": 200000, "json_mode": true}]}
   ```
   旧版单份扁平配置（`{"provider": ...}`）读的时候自动当成一份叫「默认（导入）」的存档，不需要手动迁移；
3. 环境变量：`LLM_PROVIDER`、`LLM_MODEL`、`LLM_BASE_URL`、`LLM_API_KEY_ENV`、`LLM_DAILY_TOKEN_LIMIT`、`LLM_JSON_MODE`。

密钥不进配置文件：只写「去哪个环境变量找」（`api_key_env`），或者在页面里填到
`<运行目录>/config/llm_secrets.json`（0600，按存档分开存，接口永不回显）。

新增供应商：在 `PROVIDERS` 里加一个 `BaseProvider` 子类即可（实现 `chat_json`，需要搜索再实现 `search`）。
"""
from __future__ import annotations

import json
import os
import secrets
from datetime import datetime, timezone
from pathlib import Path

from .anthropic_compatible import AnthropicProvider
from .deepseek import DeepSeekClient
from .llm_base import BaseProvider, LlmUnavailable
from .openai_compatible import OpenAICompatibleProvider

PROVIDERS: dict[str, type[BaseProvider]] = {
    "deepseek": DeepSeekClient,
    "openai_compatible": OpenAICompatibleProvider,
    "anthropic": AnthropicProvider,
}
DEFAULT_PROVIDER = "deepseek"
CONFIG_KEYS = ("provider", "model", "base_url", "api_key_env", "daily_token_limit", "json_mode")
ENV_KEYS = {
    "LLM_PROVIDER": ("provider", str),
    "LLM_MODEL": ("model", str),
    "LLM_BASE_URL": ("base_url", str),
    "LLM_API_KEY_ENV": ("api_key_env", str),
    "LLM_DAILY_TOKEN_LIMIT": ("daily_token_limit", int),
    "LLM_JSON_MODE": ("json_mode", lambda v: str(v).strip().lower() not in ("0", "false", "no")),
}
DEFAULT_PROFILE_ID = "default"
PROFILE_LIMIT = 20          # 存档数量上限（只是防手滑，正常用不了这么多）
PROFILE_MODEL_LIMIT = 60    # 每份存档能记多少个可选模型
NAME_MAX = 40
MODEL_NAME_MAX = 80


class ProfileError(ValueError):
    """存档操作被拒（供应商不存在、上限、重名等）：带机器码，处理函数映射成 422。"""

    def __init__(self, code: str, message: str, field: str | None = None):
        super().__init__(message)
        self.code = code
        self.field = field


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def config_path(settings) -> Path:
    return Path(settings.runtime_dir) / "config" / "llm.json"


def config_path_hint() -> str:
    return "<运行目录>/config/llm.json"


def _cls(provider_id: str) -> type[BaseProvider] | None:
    return PROVIDERS.get(provider_id)


def _blank_profile(provider: str = DEFAULT_PROVIDER, *, pid: str = "", name: str = "") -> dict:
    cls = _cls(provider) or PROVIDERS[DEFAULT_PROVIDER]
    return {"id": pid, "name": name or cls.label, "provider": provider, "model": "", "base_url": "",
            "api_key_env": "", "daily_token_limit": None, "json_mode": True, "models": [], "updated_at": None}


def _clean_models(raw) -> list[dict]:
    """可选模型清单：[{name, long_context}]，去重、限长、最多 PROFILE_MODEL_LIMIT 条。"""
    out: list[dict] = []
    seen: set[str] = set()
    if not isinstance(raw, list):
        return out
    for m in raw[:PROFILE_MODEL_LIMIT]:
        if isinstance(m, str):
            name, long_ctx = m, False
        elif isinstance(m, dict):
            name, long_ctx = m.get("name") or m.get("id") or "", bool(m.get("long_context"))
        else:
            continue
        name = str(name or "").strip()[:MODEL_NAME_MAX]
        if name and name not in seen:
            out.append({"name": name, "long_context": long_ctx})
            seen.add(name)
    return out


def _clean_profile(raw: dict, *, pid: str | None = None) -> dict:
    """把任意来源的一份配置整理成存档形状。空字段＝用供应商默认值（不存在文件里的密钥）。"""
    provider = str(raw.get("provider") or DEFAULT_PROVIDER).strip()
    cls = _cls(provider) or PROVIDERS[DEFAULT_PROVIDER]
    name = str(raw.get("name") or "").strip()[:NAME_MAX] or cls.label
    out = {"id": str(pid or raw.get("id") or "").strip() or "p-" + secrets.token_hex(4),
           "name": name, "provider": provider}
    for k in ("model", "base_url", "api_key_env"):
        v = raw.get(k)
        out[k] = str(v).strip() if v not in (None, "") else ""
    lim = raw.get("daily_token_limit")
    out["daily_token_limit"] = None
    if lim not in (None, ""):
        try:
            out["daily_token_limit"] = int(lim)
        except (TypeError, ValueError):
            out["daily_token_limit"] = None
    out["json_mode"] = bool(raw.get("json_mode", True))
    out["models"] = _clean_models(raw.get("models"))
    out["updated_at"] = raw.get("updated_at")
    return out


def _implicit_doc() -> dict:
    """没有任何配置时的内置存档（不落盘，直到用户真的保存）。"""
    prof = _blank_profile(DEFAULT_PROVIDER, pid=DEFAULT_PROFILE_ID, name="DeepSeek（默认）")
    return {"version": 1, "active": DEFAULT_PROFILE_ID, "profiles": [prof], "seats": {}}


def _read_doc(settings) -> tuple[dict, bool]:
    """读配置并整理成 {version, active, profiles}。第二个返回值表示「文件是否存在且可用」。"""
    p = config_path(settings)
    raw = None
    if p.is_file():
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError) as ex:
            doc = _implicit_doc()
            doc["_config_error"] = f"{type(ex).__name__}: {ex}"
            return doc, False
    else:
        return _implicit_doc(), False
    if not isinstance(raw, dict):
        return _implicit_doc(), False
    if isinstance(raw.get("profiles"), list) and raw["profiles"]:
        profs: list[dict] = []
        seen: set[str] = set()
        for r in raw["profiles"]:
            if not isinstance(r, dict):
                continue
            q = _clean_profile(r)
            if q["id"] in seen:
                q["id"] = "p-" + secrets.token_hex(4)
            seen.add(q["id"])
            profs.append(q)
        if profs:
            active = str(raw.get("active") or "")
            if active not in seen:
                active = profs[0]["id"]
            seats = {str(k): str(v) for k, v in (raw.get("seats") or {}).items()
                     if isinstance(v, str) and v in seen} if isinstance(raw.get("seats"), dict) else {}
            doc = {"version": 1, "active": active, "profiles": profs, "seats": seats}
            if raw.get("_config_error"):
                doc["_config_error"] = raw["_config_error"]
            return doc, True
    # 旧格式（2026-10-21 之前的单份扁平配置）：当成一份叫「默认（导入）」的存档
    flat = {k: raw.get(k) for k in CONFIG_KEYS if raw.get(k) not in (None, "")}
    if not flat:
        return _implicit_doc(), True
    prof = _clean_profile({**flat, "name": raw.get("name") or ""}, pid=DEFAULT_PROFILE_ID)
    if not raw.get("name"):
        cls = _cls(prof["provider"])
        prof["name"] = f"{cls.label if cls else prof['provider']}（导入）"[:NAME_MAX]
    return {"version": 1, "active": DEFAULT_PROFILE_ID, "profiles": [prof], "seats": {}}, True


def _write_doc(settings, doc: dict) -> None:
    p = config_path(settings)
    p.parent.mkdir(parents=True, exist_ok=True)
    out = {"version": 1, "active": doc.get("active") or "",
           "profiles": [{k: v for k, v in prof.items() if not k.startswith("_")} for prof in doc.get("profiles") or []],
           "seats": {k: v for k, v in (doc.get("seats") or {}).items() if v}}
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(out, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    tmp.replace(p)


def _cfg_from(prof: dict, *, path: Path, source: str) -> dict:
    cfg: dict = {"provider": prof["provider"], "model": prof["model"], "base_url": prof["base_url"],
                 "api_key_env": prof["api_key_env"], "json_mode": prof["json_mode"]}
    if prof.get("daily_token_limit"):
        cfg["daily_token_limit"] = prof["daily_token_limit"]
    cfg["_source"] = {k: source for k in CONFIG_KEYS if k in cfg}
    cfg["_path"] = str(path)
    cfg["_profile_id"] = prof["id"]
    cfg["_profile_name"] = prof["name"]
    cfg["_models"] = list(prof.get("models") or [])
    return cfg


def _apply_env(cfg: dict) -> dict:
    for env, (key, cast) in ENV_KEYS.items():
        v = os.environ.get(env)
        if v in (None, ""):
            continue
        try:
            cfg[key] = cast(v)
        except (TypeError, ValueError):
            continue
        cfg.setdefault("_source", {})[key] = f"env:{env}"
    return cfg


def active_profile(doc: dict) -> dict:
    profs = doc.get("profiles") or []
    if not profs:
        return _implicit_doc()["profiles"][0]
    for p in profs:
        if p["id"] == doc.get("active"):
            return p
    return profs[0]


def load(settings) -> dict:
    """当前存档（含环境变量覆盖）。每次调用都重新读文件：改完立即生效。"""
    doc, _ = _read_doc(settings)
    cfg = _cfg_from(active_profile(doc), path=config_path(settings), source="file")
    cfg["_doc"] = doc
    if doc.get("_config_error"):
        cfg["_config_error"] = doc["_config_error"]
    return _apply_env(cfg)


def config_for(settings, profile_id: str) -> dict:
    """指定存档的配置（试连、预览用）：找不到就报 ProfileError。"""
    doc, _ = _read_doc(settings)
    for prof in doc.get("profiles") or []:
        if prof["id"] == profile_id:
            return _cfg_from(prof, path=config_path(settings), source="file")
    raise ProfileError("PROFILE_NOT_FOUND", f"没有这个模型存档：{profile_id}", "id")


# ── 每席绑定模型（ADR-012） ──
def bind_seat(settings, seat_id: str, profile_id: str | None) -> dict:
    """把某个席位绑到一份存档；profile_id=None 表示跟随全局（当前存档）。"""
    seat_id = str(seat_id or "").strip()
    if not seat_id:
        raise ProfileError("VALIDATION_ERROR", "缺 seat_id", "seat_id")
    doc, _ = _read_doc(settings)
    ids = [p["id"] for p in doc.get("profiles") or []]
    seats = dict(doc.get("seats") or {})
    if profile_id:
        if profile_id not in ids:
            raise ProfileError("PROFILE_NOT_FOUND", f"没有这个模型存档：{profile_id}", "profile_id")
        seats[seat_id] = profile_id
    else:
        seats.pop(seat_id, None)
    doc["seats"] = seats
    _write_doc(settings, doc)
    return load(settings)


def seat_profile_id(settings, seat_id: str | None) -> str | None:
    if not seat_id:
        return None
    doc, _ = _read_doc(settings)
    pid = (doc.get("seats") or {}).get(seat_id)
    if pid and any(p["id"] == pid for p in doc.get("profiles") or []):
        return pid
    return None


def config_for_seat(settings, seat_id: str | None) -> dict:
    """这一席该用哪份配置：绑定了就用绑定的，否则用当前存档（再叠加环境变量）。"""
    pid = seat_profile_id(settings, seat_id)
    return config_for(settings, pid) if pid else load(settings)


def _validate(raw: dict, *, pid: str | None = None) -> dict:
    prof = _clean_profile(raw, pid=pid)
    if prof["provider"] not in PROVIDERS:
        raise ProfileError("UNKNOWN_PROVIDER",
                           f"未注册的供应商：{prof['provider']}（可用：{'、'.join(PROVIDERS)}）", "provider")
    lim = prof.get("daily_token_limit")
    if lim is not None and int(lim) <= 0:
        raise ProfileError("VALIDATION_ERROR", "每天 token 上限必须是大于 0 的整数", "daily_token_limit")
    if len(str(raw.get("name") or "")) > NAME_MAX:
        raise ProfileError("VALIDATION_ERROR", f"存档名不要超过 {NAME_MAX} 个字", "name")
    return prof


def fetch_models(settings, profile_id: str | None = None) -> dict:
    """问供应商要模型清单（ADR-010）。失败也返回 200 结构，让页面如实显示原因。"""
    cfg = config_for(settings, profile_id) if profile_id else load(settings)
    provider = build(settings, cfg)
    pid = cfg.get("_profile_id")
    # 拉清单只需要「接口地址 + 密钥」：还没选模型也应该能先看看有哪些模型
    if not provider.base_url:
        return {"ok": False, "code": "LLM_DISABLED", "reason": "这份存档没有接口地址", "models": [], "profile_id": pid}
    if not provider.api_key():
        return {"ok": False, "code": "LLM_DISABLED",
                "reason": f"{provider.api_key_env} 未设置：拿不到模型清单", "models": [], "profile_id": pid}
    try:
        data = provider.list_models()
    except LlmUnavailable as ex:
        return {"ok": False, "code": ex.code, "reason": str(ex), "models": [], "profile_id": pid}
    doc, _ = _read_doc(settings)
    saved = next((p for p in doc.get("profiles") or [] if p["id"] == pid), {})
    flags = {m["name"]: bool(m.get("long_context")) for m in saved.get("models") or []}
    models = [{"name": m["name"], "label": m.get("label") or "",
               "long_context": flags.get(m["name"], False), "selected": m["name"] == provider.model}
              for m in data.get("models") or []]
    return {"ok": True, "models": models, "count": len(models), "source": "api", "profile_id": pid}


def save_profile(settings, profile: dict, *, activate: bool | None = None) -> tuple[dict, dict]:
    """新建或更新一份存档。新建默认设为当前；更新默认不动当前。返回 (存档, 当前配置)。"""
    doc, existed = _read_doc(settings)
    profs = doc.get("profiles") or []
    ids = [p["id"] for p in profs]
    pid = str(profile.get("id") or "").strip()
    is_new = pid not in ids
    if is_new and not existed:
        profs = []          # 第一次保存：不要留下内置的占位存档
        ids = []
    if is_new and len(profs) >= PROFILE_LIMIT:
        raise ProfileError("PROFILE_LIMIT", f"最多保存 {PROFILE_LIMIT} 份存档；先删掉不用的", "profiles")
    prof = _validate(profile, pid=pid or None)
    prof["updated_at"] = _now_iso()
    if is_new:
        profs.append(prof)
    else:
        profs = [prof if p["id"] == prof["id"] else p for p in profs]
    doc["profiles"] = profs
    if (is_new and activate is not False) or activate:
        doc["active"] = prof["id"]
    elif doc.get("active") not in [p["id"] for p in profs]:
        doc["active"] = prof["id"]
    _write_doc(settings, doc)
    return prof, load(settings)


def update_active(settings, patch: dict) -> dict:
    """只改当前存档（兼容旧的 `POST /api/v1/llm/config`）。换供应商且没给模型时，回到新供应商的默认值。"""
    doc, _ = _read_doc(settings)
    prof = active_profile(doc)
    new = dict(prof)
    for k in CONFIG_KEYS:
        if k in patch and patch[k] not in (None, ""):
            new[k] = patch[k]
    if patch.get("provider") and patch["provider"] != prof["provider"] and \
            not any(k in patch for k in ("model", "base_url", "api_key_env")):
        new["model"] = new["base_url"] = new["api_key_env"] = ""
    prof2 = _validate(new, pid=prof["id"])
    prof2["updated_at"] = _now_iso()
    doc["profiles"] = [prof2 if p["id"] == prof["id"] else p for p in doc.get("profiles") or [prof2]]
    _write_doc(settings, doc)
    return load(settings)


def save(settings, patch: dict) -> dict:
    """兼容旧名字：等价于 update_active。"""
    return update_active(settings, patch)


def activate_profile(settings, profile_id: str) -> dict:
    doc, _ = _read_doc(settings)
    ids = [p["id"] for p in doc.get("profiles") or []]
    if profile_id not in ids:
        raise ProfileError("PROFILE_NOT_FOUND", f"没有这个模型存档：{profile_id}", "id")
    doc["active"] = profile_id
    _write_doc(settings, doc)
    return load(settings)


def duplicate_profile(settings, profile_id: str) -> tuple[dict, dict]:
    """复制一份存档（名字加「副本」），不动当前。"""
    doc, _ = _read_doc(settings)
    src = next((p for p in doc.get("profiles") or [] if p["id"] == profile_id), None)
    if src is None:
        raise ProfileError("PROFILE_NOT_FOUND", f"没有这个模型存档：{profile_id}", "id")
    if len(doc.get("profiles") or []) >= PROFILE_LIMIT:
        raise ProfileError("PROFILE_LIMIT", f"最多保存 {PROFILE_LIMIT} 份存档；先删掉不用的", "profiles")
    copy = dict(src, id="", name=f"{src['name']} 副本"[:NAME_MAX], updated_at=None)
    doc["profiles"].append(_validate(copy))
    doc["active"] = doc.get("active") or doc["profiles"][0]["id"]
    _write_doc(settings, doc)
    return doc["profiles"][-1], load(settings)


def delete_profile(settings, profile_id: str) -> dict:
    """删掉一份存档（连同它单独保存的密钥）；删的是当前存档就切到剩下的第一份。"""
    doc, _ = _read_doc(settings)
    profs = doc.get("profiles") or []
    keep = [p for p in profs if p["id"] != profile_id]
    if len(keep) == len(profs):
        raise ProfileError("PROFILE_NOT_FOUND", f"没有这个模型存档：{profile_id}", "id")
    doc["profiles"] = keep
    if doc.get("active") == profile_id:
        doc["active"] = keep[0]["id"] if keep else ""
    _write_doc(settings, doc)
    clear_secret(settings, "", profile_id=profile_id)
    return load(settings)


def secrets_file(settings) -> Path:
    return Path(settings.runtime_dir) / "config" / "llm_secrets.json"


def _read_secrets(settings) -> dict:
    f = secrets_file(settings)
    if not f.is_file():
        return {}
    try:
        d = json.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return d if isinstance(d, dict) else {}


def _write_secrets(settings, d: dict) -> None:
    f = secrets_file(settings)
    if not d:
        try:
            f.unlink()
        except OSError:
            pass
        return
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(f)
    try:
        os.chmod(f, 0o600)
    except OSError:
        pass


def save_secret(settings, name: str, value: str, profile_id: str | None = None) -> None:
    """页面里填的密钥存到运行目录私有文件（0600），按存档分开存；接口从不回显、不写日志。

    `name` 是环境变量名：没有存档（旧调用）时按变量名存，保持兼容。
    """
    d = _read_secrets(settings)
    d[profile_id or name] = value
    _write_secrets(settings, d)


def clear_secret(settings, name: str, profile_id: str | None = None) -> None:
    d = _read_secrets(settings)
    d.pop(profile_id or name, None)
    if not profile_id:
        d.pop(name, None)
    _write_secrets(settings, d)


def profile_secret_configured(settings, prof: dict) -> tuple[bool, str]:
    from .llm_base import resolve_secret
    value, src = resolve_secret(prof.get("api_key_env") or (PROVIDERS[prof["provider"]].default_key_env
                                                            if prof["provider"] in PROVIDERS else "LLM_API_KEY"),
                                settings.runtime_dir, prof["id"])
    return bool(value), src


def _summary(settings, prof: dict, active_id: str) -> dict:
    cls = _cls(prof["provider"])
    provider = build(settings, _cfg_from(prof, path=config_path(settings), source="file"))
    ok, why = provider.available()
    src = provider.key_source()
    return {"id": prof["id"], "name": prof["name"], "provider": prof["provider"],
            "label": cls.label if cls else prof["provider"], "model": provider.model,
            "base_url": provider.base_url, "api_key_env": provider.api_key_env,
            "daily_token_limit": prof.get("daily_token_limit"), "json_mode": bool(prof.get("json_mode", True)),
            "active": prof["id"] == active_id, "enabled": ok, "reason": why,
            "key_configured": src not in (None, "none"), "key_source": src,
            "models": [dict(m, selected=(m["name"] == provider.model)) for m in prof.get("models") or []],
            "updated_at": prof.get("updated_at")}


def build(settings, cfg: dict | None = None) -> BaseProvider:
    cfg = cfg or load(settings)
    pid = str(cfg.get("provider") or DEFAULT_PROVIDER)
    cls = PROVIDERS.get(pid)
    if cls is None:
        return UnknownProvider(pid, settings, cfg)
    kwargs = {k: cfg[k] for k in ("model", "base_url", "api_key_env") if cfg.get(k)}
    return cls(runtime_dir=settings.runtime_dir, config=cfg, profile_id=cfg.get("_profile_id"), **kwargs)


class UnknownProvider(BaseProvider):
    provider_id = "unknown"
    label = "未知供应商"

    def __init__(self, requested: str, settings, config: dict):
        super().__init__(runtime_dir=settings.runtime_dir, config=config)
        self.requested = requested
        self.provider_id = requested or "unknown"

    def available(self) -> tuple[bool, str]:
        return False, (f"未注册的模型供应商：{self.requested}（可用：{'、'.join(PROVIDERS)}）；"
                       f"改 {config_path_hint()} 或环境变量 LLM_PROVIDER")


def status(settings) -> dict:
    cfg = load(settings)
    provider = build(settings, cfg)
    st = provider.status()
    doc = cfg.get("_doc") or _implicit_doc()
    active_id = cfg.get("_profile_id") or doc.get("active")
    st.update({
        "profile_id": cfg.get("_profile_id"), "profile_name": cfg.get("_profile_name"),
        "active_profile": active_id,
        "profiles": [_summary(settings, p, active_id) for p in doc.get("profiles") or []],
        "seat_bindings": {sid: {"profile_id": pid,
                                "profile_name": next((p["name"] for p in doc.get("profiles") or [] if p["id"] == pid), "")}
                          for sid, pid in (doc.get("seats") or {}).items()},
        "profile_limit": PROFILE_LIMIT,
        "config_saved": config_path(settings).is_file(),
        "config_path": cfg.get("_path"),
        "config_source": cfg.get("_source") or {},
        "config_error": cfg.get("_config_error"),
        "search_available": provider.supports_search,
        "key_configured": st.get("key_source") not in (None, "none"),
        "providers": [{"id": pid, "label": cls.label, "supports_search": cls.supports_search,
                       "default_model": cls.default_model or None, "default_base_url": cls.default_base_url or None,
                       "key_env": cls.default_key_env, "registered": True} for pid, cls in PROVIDERS.items()],
    })
    st["providers"].append({"id": "custom", "label": "自定义（在 llm_registry.PROVIDERS 里加子类）",
                            "supports_search": None, "default_model": None, "default_base_url": None,
                            "key_env": None, "registered": False})
    return st
