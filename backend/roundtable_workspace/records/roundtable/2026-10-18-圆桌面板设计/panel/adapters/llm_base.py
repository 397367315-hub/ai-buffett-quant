"""模型供应商的公共底座（ADR-008）：换模型只改配置，不改业务代码。

- `BaseProvider`：统一的状态、预算、HTTP 重试、审计入口；子类只实现自己的请求形状；
- 密钥解析：环境变量（名字由配置指定）优先，其次包外 env 文件（`LLM_ENV_FILE` / `~/.llm.env` /
  兼容旧的 `~/.deepseek.env`）；只返回字符串，不写盘、不打印、不进证据；
- 单日 token 预算与 DeepSeek 版共用同一份账本（`<runtime>/llm_budget.json`），换模型不会绕过上限。
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

_shared_llm_base: str | None = None
_shared_llm_model: str | None = None
_shared_llm_key_resolver = None


def configure_shared_llm(base_url: str | None, model: str | None, api_key_resolver) -> None:
    """Bind the roundtable's default profile to the host LLM settings.

    The resolver is retained in memory and called on demand; the key is never
    copied into a profile or written to disk.  Custom profiles continue to use
    their own configured resolver.
    """
    global _shared_llm_base, _shared_llm_model, _shared_llm_key_resolver
    _shared_llm_base = str(base_url).strip().rstrip("/") if base_url else None
    _shared_llm_model = str(model).strip() if model else None
    _shared_llm_key_resolver = api_key_resolver


def clear_shared_llm() -> None:
    configure_shared_llm(None, None, None)


def shared_llm_config() -> tuple[str | None, str | None, object]:
    return _shared_llm_base, _shared_llm_model, _shared_llm_key_resolver


class LlmUnavailable(RuntimeError):
    """没有密钥、预算用尽、搜索不可用或网络失败：调用方要如实说明，不能编答案。"""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def env_path(name: str) -> Path | None:
    v = os.environ.get(name)
    return Path(v).expanduser() if v else None


def secrets_path(runtime_dir: Path | None) -> Path | None:
    return (Path(runtime_dir) / "config" / "llm_secrets.json") if runtime_dir else None


def resolve_secret(name: str, runtime_dir: Path | None = None, profile_id: str | None = None) -> tuple[str | None, str]:
    """按优先级找密钥：环境变量 → LLM_ENV_FILE → ~/.llm.env → ~/.deepseek.env（兼容）
    → 运行目录里的私有文件 config/llm_secrets.json（页面里填的密钥存这里，0600；按存档分开存，
      先找本存档的条目，再找按环境变量名存的旧条目）。

    返回 (值, 来源)。来源只写「环境变量名 / 文件名」，绝不返回值本身。
    """
    v = (os.environ.get(name) or "").strip()
    if v:
        return v, f"env:{name}"
    candidates = [env_path("LLM_ENV_FILE"), Path.home() / ".llm.env", Path.home() / ".deepseek.env"]
    for f in [c for c in candidates if c]:
        try:
            text = Path(f).read_text(encoding="utf-8")
        except OSError:
            continue
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, val = line.partition("=")
            if k.strip() == name:
                val = val.strip().strip('"').strip("'")
                if val:
                    return val, f"file:{Path(f).name}"
    sp = secrets_path(runtime_dir)
    if sp and sp.is_file():
        try:
            d = json.loads(sp.read_text(encoding="utf-8"))
            for key in ([profile_id, name] if profile_id else [name]):
                v = str(d.get(key) or "").strip() if key else ""
                if v:
                    return v, "runtime:llm_secrets.json"
        except (OSError, ValueError):
            pass
    return None, "none"


class Budget:
    """单日 token 账本（只算总量，不记内容）；所有供应商共用一份。"""

    def __init__(self, runtime_dir: Path, limit: int | None = None):
        self.path = Path(runtime_dir) / "llm_budget.json"
        self.limit = limit if limit is not None else int(os.environ.get("LLM_DAILY_TOKEN_LIMIT", "200000"))

    def _today(self) -> str:
        return datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d")

    def used(self) -> int:
        try:
            d = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return 0
        return int(d.get(self._today(), 0))

    def check(self, estimate: int = 0) -> None:
        if self.used() + estimate > self.limit:
            raise LlmUnavailable("LLM_BUDGET_EXCEEDED",
                                 f"今天的模型额度用完了（已用 {self.used():,} / 上限 {self.limit:,} tokens）；"
                                 "明天再试或调高 LLM_DAILY_TOKEN_LIMIT")

    def add(self, tokens: int) -> None:
        if tokens <= 0:
            return
        try:
            d = json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else {}
        except (OSError, ValueError):
            d = {}
        day = self._today()
        d[day] = int(d.get(day, 0)) + int(tokens)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")


class BaseProvider:
    provider_id = "base"
    label = "基础"
    supports_search = False
    supports_fetch = False
    default_base_url = ""
    default_model = ""
    default_key_env = "LLM_API_KEY"

    def __init__(self, *, model: str | None = None, base_url: str | None = None, api_key_env: str | None = None,
                 timeout: float = 45.0, max_retry: int = 2, runtime_dir: Path | None = None, transport=None,
                 config: dict | None = None, profile_id: str | None = None):
        self.model = model or self.default_model
        self.base_url = (base_url or self.default_base_url).rstrip("/")
        self.api_key_env = api_key_env or self.default_key_env
        self.profile_id = profile_id
        self.timeout = timeout
        self.max_retry = max_retry
        self.budget = Budget(runtime_dir or Path(".").resolve(),
                             (config or {}).get("daily_token_limit"))
        self.transport = transport
        self.config = config or {}
        self.calls: list[dict] = []          # 只记方法/路径/状态/耗时，不记正文

    # ── 密钥与状态 ──
    @property
    def runtime_dir(self) -> Path:
        return self.budget.path.parent

    def api_key(self) -> str | None:
        return resolve_secret(self.api_key_env, self.runtime_dir, self.profile_id)[0]

    def key_source(self) -> str:
        return resolve_secret(self.api_key_env, self.runtime_dir, self.profile_id)[1]

    def available(self) -> tuple[bool, str]:
        if not self.model:
            return False, f"{self.provider_id} 没有配置模型名（LLM_MODEL）"
        if not self.base_url:
            return False, f"{self.provider_id} 没有配置接口地址（LLM_BASE_URL）"
        if not self.api_key():
            return False, f"{self.api_key_env} 未设置：模型关闭，全部功能退回仅规则"
        return True, "ok"

    def status(self) -> dict:
        ok, why = self.available()
        return {"provider": self.provider_id, "label": self.label, "model": self.model, "enabled": ok,
                "reason": why, "base_url": self.base_url, "key_source": self.key_source(),
                "supports_search": self.supports_search, "supports_fetch": self.supports_fetch,
                "budget": {"used": self.budget.used(), "limit": self.budget.limit}, "default_off": True}

    # ── HTTP ──
    def _headers(self, key: str) -> dict:
        return {"Content-Type": "application/json", "Accept": "application/json",
                "Authorization": f"Bearer {key}"}

    def _post(self, path: str, payload: dict) -> dict:
        return self._request("POST", path, payload)

    def _get(self, path: str) -> dict:
        return self._request("GET", path, None)

    def _request(self, method: str, path: str, payload: dict | None) -> dict:
        key = self.api_key()
        if not key:
            raise LlmUnavailable("LLM_DISABLED", f"没有 {self.api_key_env}：模型调用关闭")
        url = path if path.startswith("http") else f"{self.base_url}{path}"
        body = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
        hdrs = self._headers(key)
        last = None
        for attempt in range(self.max_retry + 1):
            t0 = time.monotonic()
            try:
                if self.transport:
                    status, raw = self.transport(method, url, hdrs, body)
                else:
                    req = urllib.request.Request(url, data=body, headers=hdrs, method=method)
                    with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                        status, raw = resp.status, resp.read()
            except urllib.error.HTTPError as ex:
                status, raw = ex.code, ex.read()
            except (urllib.error.URLError, TimeoutError, OSError) as ex:
                last = LlmUnavailable("LLM_NETWORK", f"连不上 {self.provider_id}：{type(ex).__name__}")
                if attempt < self.max_retry:
                    time.sleep(1.5 ** attempt)
                    continue
                raise last
            took = round((time.monotonic() - t0) * 1000, 1)
            self.calls.append({"method": method,
                               "path": url.split(self.base_url)[-1] if self.base_url else path, "status": status,
                               "ms": took, "attempt": attempt + 1})
            if status == 200:
                try:
                    return json.loads(raw)
                except ValueError:
                    raise LlmUnavailable("LLM_BAD_RESPONSE", f"{self.provider_id} 返回的不是 JSON")
            if status in (429, 500, 502, 503, 504) and attempt < self.max_retry:
                time.sleep(1.5 ** attempt)
                continue
            detail = ""
            try:
                d = json.loads(raw)
                detail = (d.get("error") or {}).get("message") or d.get("message") or ""
            except ValueError:
                detail = raw[:200].decode("utf-8", "replace")
            code = {401: "LLM_AUTH", 403: "LLM_FORBIDDEN", 402: "LLM_BALANCE", 429: "LLM_RATE_LIMIT"}.get(status, "LLM_ERROR")
            raise LlmUnavailable(code, f"{self.provider_id} {status}：{detail[:200]}")
        raise last or LlmUnavailable("LLM_ERROR", "调用失败")

    # ── 模型清单（ADR-010） ──
    models_path = "/models"   # 取模型清单的路径；Anthropic 是 /v1/models

    def list_models(self) -> dict:
        """问供应商「这把钥匙能用哪些模型」。只认 data[] / models[] 里的 id，别的一律不猜。"""
        doc = self._get(self.models_path)
        items = doc.get("data")
        if not isinstance(items, list):
            items = doc.get("models")
        if not isinstance(items, list):
            raise LlmUnavailable("LLM_BAD_RESPONSE", f"{self.provider_id} 的模型清单不是 data[] 形状")
        out: list[dict] = []
        for it in items:
            if isinstance(it, str):
                name, label = it.strip(), ""
            elif isinstance(it, dict):
                name = str(it.get("id") or it.get("name") or it.get("model") or "").strip()
                label = str(it.get("display_name") or it.get("owned_by") or "")
            else:
                continue
            if name:
                out.append({"name": name, "label": label[:60]})
        out.sort(key=lambda m: m["name"])
        return {"models": out, "count": len(out)}


    # ── 能力（子类按需实现） ──
    def chat_json(self, system: str, user: str, *, max_tokens: int = 1200, temperature: float = 0.0) -> dict:
        raise LlmUnavailable("LLM_UNSUPPORTED", f"{self.provider_id} 没有实现 chat_json")

    def search(self, query: str, count: int = 5, need_content: bool = True) -> dict:
        raise LlmUnavailable("LLM_SEARCH_UNSUPPORTED", f"{self.provider_id} 不提供搜索能力")

    def fetch(self, url: str) -> dict:
        raise LlmUnavailable("LLM_FETCH_UNSUPPORTED", f"{self.provider_id} 不提供网页抓取能力")

    # ── 审计 ──
    def audit_entry(self, kind: str, *, prompt_sha: str, output_sha: str | None, status: str,
                    model: str | None = None, tokens: int = 0, error_codes: list[str] | None = None) -> dict:
        from ..domain import clock
        return {"kind": kind, "provider_id": self.provider_id, "model_id": model or self.model,
                "prompt_sha256": prompt_sha, "output_sha256": output_sha, "status": status,
                "tokens": tokens, "error_codes": error_codes or [], "at": clock.utc_iso(),
                "calls": list(self.calls)}
