"""OpenAI 兼容供应商（ADR-008）：OpenAI、Ollama、vLLM、通义、智谱等凡是用
`POST {base}/chat/completions` + `Authorization: Bearer` 的接口都能接。

只做聊天（JSON 模式）；没有内置搜索——需要搜索时把 `provider` 设回 deepseek，或在配置里单独指定。
"""
from __future__ import annotations

import os
from pathlib import Path

from .llm_base import BaseProvider, LlmUnavailable


class OpenAICompatibleProvider(BaseProvider):
    provider_id = "openai_compatible"
    label = "OpenAI 兼容接口"
    supports_search = False
    supports_fetch = False
    default_base_url = ""
    default_model = ""
    default_key_env = "LLM_API_KEY"

    def __init__(self, *, model: str | None = None, base_url: str | None = None,
                 api_key_env: str | None = None, timeout: float = 45.0, max_retry: int = 2,
                 runtime_dir: Path | None = None, transport=None, config: dict | None = None,
                 profile_id: str | None = None):
        super().__init__(model=model or os.environ.get("LLM_MODEL"), base_url=base_url,
                         api_key_env=api_key_env, timeout=timeout, max_retry=max_retry,
                         runtime_dir=runtime_dir, transport=transport, config=config, profile_id=profile_id)
        self.json_mode = bool((config or {}).get("json_mode", True))

    def chat_json(self, system: str, user: str, *, max_tokens: int = 1200, temperature: float = 0.0) -> dict:
        self.budget.check(estimate=max_tokens)
        payload = {"model": self.model, "messages": [{"role": "system", "content": system},
                                                     {"role": "user", "content": user}],
                   "temperature": temperature, "max_tokens": max_tokens, "stream": False}
        if self.json_mode:
            payload["response_format"] = {"type": "json_object"}
        try:
            doc = self._post("/chat/completions", payload)
        except LlmUnavailable as ex:
            # 有些兼容实现不认 response_format：去掉再试一次（仍失败就如实报错）
            if self.json_mode and ex.code == "LLM_ERROR" and "response_format" in str(ex):
                payload.pop("response_format", None)
                doc = self._post("/chat/completions", payload)
            else:
                raise
        usage = doc.get("usage") or {}
        tokens = int(usage.get("total_tokens") or 0)
        self.budget.add(tokens)
        try:
            content = doc["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            raise LlmUnavailable("LLM_BAD_RESPONSE", "响应缺少 choices[0].message.content")
        import json
        try:
            data = json.loads(content)
        except ValueError:
            raise LlmUnavailable("LLM_BAD_JSON", "模型没有按 JSON 输出（已拒收，不猜内容）")
        return {"data": data, "raw": content, "usage": usage, "model": doc.get("model") or self.model,
                "tokens": tokens}
