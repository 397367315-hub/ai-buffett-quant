"""Anthropic 供应商（ADR-008）：`POST {base}/v1/messages`，`x-api-key` + `anthropic-version`。

Anthropic 没有 `response_format`：JSON 靠提示约束，回复里若带 ```json 围栏会先剥掉再解析；
解析失败就拒收（`LLM_BAD_JSON`），不做“猜内容”。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from .llm_base import BaseProvider, LlmUnavailable

_FENCE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.S)


class AnthropicProvider(BaseProvider):
    provider_id = "anthropic"
    label = "Anthropic 兼容接口"
    supports_search = False
    supports_fetch = False
    default_base_url = "https://api.anthropic.com"
    default_model = ""
    default_key_env = "ANTHROPIC_API_KEY"
    api_version = "2023-06-01"
    models_path = "/v1/models"

    def __init__(self, *, model: str | None = None, base_url: str | None = None,
                 api_key_env: str | None = None, timeout: float = 60.0, max_retry: int = 2,
                 runtime_dir: Path | None = None, transport=None, config: dict | None = None,
                 profile_id: str | None = None):
        super().__init__(model=model or "", base_url=base_url, api_key_env=api_key_env, timeout=timeout,
                         max_retry=max_retry, runtime_dir=runtime_dir, transport=transport, config=config,
                         profile_id=profile_id)

    def _headers(self, key: str) -> dict:
        return {"Content-Type": "application/json", "Accept": "application/json",
                "x-api-key": key, "anthropic-version": self.api_version}

    def chat_json(self, system: str, user: str, *, max_tokens: int = 1200, temperature: float = 0.0) -> dict:
        self.budget.check(estimate=max_tokens)
        payload = {"model": self.model, "max_tokens": max_tokens, "temperature": temperature,
                   "system": system, "messages": [{"role": "user", "content": user}]}
        doc = self._post("/v1/messages", payload)
        usage = doc.get("usage") or {}
        tokens = int(usage.get("input_tokens") or 0) + int(usage.get("output_tokens") or 0)
        self.budget.add(tokens)
        blocks = doc.get("content") or []
        text = "".join(b.get("text") or "" for b in blocks if isinstance(b, dict))
        if not text.strip():
            raise LlmUnavailable("LLM_BAD_RESPONSE", "响应没有文本内容")
        m = _FENCE.match(text)
        if m:
            text = m.group(1)
        try:
            data = json.loads(text)
        except ValueError:
            raise LlmUnavailable("LLM_BAD_JSON", "模型没有按 JSON 输出（已拒收，不猜内容）")
        return {"data": data, "raw": text, "usage": usage, "model": doc.get("model") or self.model,
                "tokens": tokens}
