"""DeepSeek（默认供应商）：共享宿主配置的聊天补全。

官方文档（2026-10-21 核对）：
- Chat：`POST {base}/chat/completions`，OpenAI 兼容；模型 `deepseek-flash` / `deepseek-v4-pro`；
- Search：`POST {base}/v1/tools/search`，请求 `{"query","count","need_content"}`，返回 `results[]`；
- Fetch：`POST {base}/v1/tools/fetch`，请求 `{"url"}`，返回 `{"url","title","content"}`。

公共部分（预算、重试、状态、审计）在 `llm_base.BaseProvider`；换供应商见 `llm_registry`（ADR-008）。
"""
from __future__ import annotations

import os
from pathlib import Path

from .llm_base import (BaseProvider, Budget, LlmUnavailable, chat_json_with_retry, env_path, resolve_secret,
                       configure_shared_llm, clear_shared_llm, shared_llm_config)

DEFAULT_BASE = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-flash"
REASONING_MODEL = "deepseek-v4-pro"


class DeepSeekClient(BaseProvider):
    provider_id = "deepseek"
    label = "DeepSeek（官方）"
    supports_search = True
    supports_fetch = True
    default_base_url = DEFAULT_BASE
    default_model = DEFAULT_MODEL
    default_key_env = "DEEPSEEK_API_KEY"

    def __init__(self, base_url: str | None = None, model: str | None = None, timeout: float = 45.0,
                 max_retry: int = 2, runtime_dir: Path | None = None, transport=None, config: dict | None = None,
                 api_key_env: str | None = None, profile_id: str | None = None):
        shared_base, shared_model, _ = shared_llm_config()
        super().__init__(model=model or os.environ.get("DEEPSEEK_MODEL") or shared_model,
                         base_url=base_url or os.environ.get("DEEPSEEK_BASE_URL") or shared_base,
                         api_key_env=api_key_env or "DEEPSEEK_API_KEY", timeout=timeout, max_retry=max_retry,
                         runtime_dir=runtime_dir, transport=transport, config=config, profile_id=profile_id)
        self.supports_search = self.base_url == DEFAULT_BASE
        self.supports_fetch = self.supports_search

    # 兼容旧配置：DEEPSEEK_ENV_FILE 优先于通用 LLM_ENV_FILE
    def api_key(self) -> str | None:
        _, _, shared_key_resolver = shared_llm_config()
        if self.profile_id in (None, "", "default") and shared_key_resolver is not None:
            value = shared_key_resolver()
            return str(value).strip() if value else None
        f = env_path("DEEPSEEK_ENV_FILE")
        if f:
            try:
                for line in f.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if line.startswith("DEEPSEEK_API_KEY="):
                        v = line.partition("=")[2].strip().strip('"').strip("'")
                        if v:
                            return v
            except OSError:
                pass
        return resolve_secret(self.api_key_env, self.runtime_dir, self.profile_id)[0]

    def key_source(self) -> str:
        _, _, shared_key_resolver = shared_llm_config()
        if self.profile_id in (None, "", "default") and shared_key_resolver is not None:
            return "host:deepseek_api_key" if self.api_key() else "none"
        if env_path("DEEPSEEK_ENV_FILE") and self.api_key():
            return f"file:{env_path('DEEPSEEK_ENV_FILE').name}"
        return resolve_secret(self.api_key_env, self.runtime_dir, self.profile_id)[1]

    def chat_json(self, system: str, user: str, *, max_tokens: int = 1200, temperature: float = 0.0) -> dict:
        def request(prompt: str, output_cap: int) -> dict:
            payload = {"model": self.model, "messages": [{"role": "system", "content": system},
                                                         {"role": "user", "content": prompt}],
                       "temperature": temperature, "max_tokens": output_cap, "stream": False,
                       "response_format": {"type": "json_object"}}
            return self._post("/chat/completions", payload)

        return chat_json_with_retry(self, system, user, max_tokens=max_tokens,
                                    temperature=temperature, request=request)

    def search(self, query: str, count: int = 5, need_content: bool = True) -> dict:
        if not self.supports_search:
            raise LlmUnavailable("LLM_SEARCH_UNSUPPORTED", "当前 DeepSeek base URL 未核验搜索能力")
        self.budget.check(estimate=2000)
        doc = self._post("/v1/tools/search", {"query": query, "count": count, "need_content": need_content})
        results = [{"title": r.get("title"), "url": r.get("url"), "snippet": r.get("snippet"),
                    "summary": r.get("summary"), "content": r.get("content"), "site_name": r.get("site_name"),
                    "date": r.get("date"), "authority": r.get("authority"), "score": r.get("rerank_score")}
                   for r in doc.get("results") or []]
        self.budget.add(int((doc.get("usage") or {}).get("total_tokens") or 0))
        return {"query": query, "results": results, "count": len(results)}

    def fetch(self, url: str) -> dict:
        if not self.supports_fetch:
            raise LlmUnavailable("LLM_FETCH_UNSUPPORTED", "当前 DeepSeek base URL 未核验抓取能力")
        self.budget.check(estimate=2000)
        doc = self._post("/v1/tools/fetch", {"url": url})
        self.budget.add(int((doc.get("usage") or {}).get("total_tokens") or 0))
        return {"url": doc.get("url") or url, "title": doc.get("title"), "content": doc.get("content")}
