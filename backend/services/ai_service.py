import json
from typing import AsyncGenerator, Optional
from config import settings

try:
    from openai import AsyncOpenAI
    _has_openai = True
except ImportError:
    _has_openai = False


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
            return response.choices[0].message.content or ""
        except Exception as e:
            print(f"AI generate error: {e}")
            return f"[AI服务暂时不可用: {e}]"

    async def chat_stream(self, message: str, system_prompt: str, user_id: str) -> AsyncGenerator[dict, None]:
        if not self.client:
            yield {"type": "text", "content": "[AI服务未配置，请在.env中设置DEEPSEEK_API_KEY]"}
            yield {"type": "end", "content": ""}
            return

        try:
            stream = await self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": message},
                ],
                temperature=0.7,
                max_tokens=1500,
                stream=True,
            )

            full_content = ""
            async for chunk in stream:
                if chunk.choices[0].delta.content:
                    content = chunk.choices[0].delta.content
                    full_content += content
                    yield {"type": "text", "content": content}

            yield {"type": "end", "content": full_content}

        except Exception as e:
            print(f"AI stream error: {e}")
            yield {"type": "error", "content": str(e)}


ai_service = AIService()
