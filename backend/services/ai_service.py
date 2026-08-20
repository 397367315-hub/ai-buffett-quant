import json
import re
from typing import AsyncGenerator, Optional
from config import settings

try:
    from openai import AsyncOpenAI
    _has_openai = True
except ImportError:
    _has_openai = False


def clean_ai_text(value: object) -> str:
    """Return plain, readable Chinese text for terminal-style AI panels.

    AI answers are rendered as plain text in this product. Removing formatting
    markers centrally also keeps saved history and non-chat AI panels consistent.
    """
    text = str(value or "")
    text = re.sub(r"```(?:[a-zA-Z0-9_+-]+)?\s*", "", text)
    text = text.replace("```", "")
    text = text.replace("**", "").replace("__", "")
    text = re.sub(r"(?m)^\s{0,3}#{1,6}\s*", "", text)
    text = re.sub(r"(?m)^\s*[-*+]\s+", "- ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


class AIService:
    def __init__(self):
        self.model = settings.deepseek_model
        self.client = None
        if _has_openai and settings.deepseek_api_key:
            self.client = AsyncOpenAI(
                api_key=settings.deepseek_api_key,
                base_url=settings.deepseek_base_url,
            )

    async def generate(self, prompt: str, system_prompt: Optional[str] = None) -> str:
        if not self.client:
            return "[AI服务未配置，请在.env中设置DEEPSEEK_API_KEY]"

        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        try:
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=0.7,
                max_tokens=2000,
            )
            return clean_ai_text(response.choices[0].message.content or "")
        except Exception as e:
            print(f"AI generate error: {e}")
            return f"[AI服务暂时不可用: {e}]"

    async def chat_stream(
        self,
        message: str,
        system_prompt: str,
        user_id: str,
        history: Optional[list[dict]] = None,
    ) -> AsyncGenerator[dict, None]:
        if not self.client:
            yield {"type": "text", "content": clean_ai_text("[AI服务未配置，请在.env中设置DEEPSEEK_API_KEY]")}
            yield {"type": "end", "content": ""}
            return

        try:
            messages = [{"role": "system", "content": system_prompt}]
            for item in (history or [])[-80:]:
                role = item.get("role")
                content = str(item.get("content") or "").strip()
                if role in {"user", "assistant"} and content:
                    messages.append({"role": role, "content": content[:30000]})
            messages.append({"role": "user", "content": message})
            stream = await self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=0.7,
                max_tokens=1500,
                stream=True,
            )

            full_content = ""
            emitted_content = ""
            async for chunk in stream:
                if chunk.choices[0].delta.content:
                    content = chunk.choices[0].delta.content
                    full_content += content
                    cleaned = clean_ai_text(full_content)
                    # Keep a small tail pending so markers split across SSE
                    # chunks do not flash in the browser.
                    safe_length = max(0, len(cleaned) - 3) if cleaned.endswith(("*", "_", "`", "#")) else len(cleaned)
                    safe = cleaned[:safe_length]
                    if safe.startswith(emitted_content) and len(safe) > len(emitted_content):
                        delta = safe[len(emitted_content):]
                        emitted_content = safe
                        yield {"type": "text", "content": delta}

            cleaned = clean_ai_text(full_content)
            if cleaned.startswith(emitted_content) and len(cleaned) > len(emitted_content):
                yield {"type": "text", "content": cleaned[len(emitted_content):]}
            yield {"type": "end", "content": cleaned}

        except Exception as e:
            print(f"AI stream error: {e}")
            yield {"type": "error", "content": str(e)}


ai_service = AIService()
