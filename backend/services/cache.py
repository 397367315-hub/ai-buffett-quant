import json
import time
import asyncio
from datetime import datetime, timedelta
from typing import Optional, Any


class DataCache:
    """带过期时间的数据缓存层，减少API调用频率"""

    def __init__(self, ttl_seconds: int = 300):
        self._cache: dict[str, dict] = {}
        self._ttl = ttl_seconds

    def get(self, key: str) -> Optional[Any]:
        if key in self._cache:
            entry = self._cache[key]
            if time.time() - entry["ts"] < self._ttl:
                return entry["data"]
        return None

    def set(self, key: str, data: Any):
        self._cache[key] = {"data": data, "ts": time.time()}

    def clear_expired(self):
        now = time.time()
        expired = [k for k, v in self._cache.items() if now - v["ts"] > self._ttl]
        for k in expired:
            del self._cache[k]

    @property
    def stats(self) -> dict:
        return {
            "cached_keys": len(self._cache),
            "keys": list(self._cache.keys()),
            "ttl_seconds": self._ttl,
        }


# 全局缓存实例
api_cache = DataCache(ttl_seconds=300)


async def api_call_with_retry(func, *args, max_retries: int = 3, cache_key: str = None, **kwargs):
    """带缓存+重试的API调用包装器"""
    # 先检查缓存
    if cache_key:
        cached = api_cache.get(cache_key)
        if cached is not None:
            return cached

    # 重试调用
    last_error = None
    for attempt in range(max_retries):
        try:
            result = await func(*args, **kwargs)
            # 缓存成功结果
            if cache_key and result:
                api_cache.set(cache_key, result)
            return result
        except Exception as e:
            last_error = e
            if attempt < max_retries - 1:
                wait = (attempt + 1) * 3  # 3s, 6s, 9s 退避
                print(f"[API Retry] {func.__name__} attempt {attempt+1} failed, retrying in {wait}s: {e}")
                await asyncio.sleep(wait)

    print(f"[API Error] {func.__name__} failed after {max_retries} retries: {last_error}")
    return None
