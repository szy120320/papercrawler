"""
速率限制器

针对每个域名维护最小请求间隔，避免触发反爬或超出 API 配额。
"""

from __future__ import annotations

import asyncio
import time
from collections import defaultdict
from urllib.parse import urlparse


class RateLimiter:
    """
    基于域名的异步速率限制器。

    使用方式::

        limiter = RateLimiter(default_delay=1.0)
        await limiter.wait("https://api.semanticscholar.org/...")
        response = await client.get(url)
    """

    def __init__(self, default_delay: float = 1.0):
        self._default_delay = default_delay
        self._domain_delays: dict[str, float] = {}       # 域名 → 自定义延迟
        self._last_request: dict[str, float] = defaultdict(float)
        self._locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    def _domain(self, url: str) -> str:
        return urlparse(url).netloc or url

    def set_delay(self, domain: str, delay: float) -> None:
        """为特定域名设置自定义延迟（不影响其他域名）"""
        self._domain_delays[domain] = delay

    async def wait(self, url: str, delay: float | None = None) -> None:
        """
        等待直到距上次请求经过了足够的间隔时间。
        优先级: 参数 delay > 域名自定义 delay > 全局默认 delay
        """
        domain = self._domain(url)
        if delay is None:
            delay = self._domain_delays.get(domain, self._default_delay)

        async with self._locks[domain]:
            now = time.monotonic()
            elapsed = now - self._last_request[domain]
            if elapsed < delay:
                await asyncio.sleep(delay - elapsed)
            self._last_request[domain] = time.monotonic()


# 全局速率限制器实例（各模块共享）
_global_limiter: RateLimiter | None = None


def get_rate_limiter(delay: float = 1.0) -> RateLimiter:
    global _global_limiter
    if _global_limiter is None:
        _global_limiter = RateLimiter(default_delay=delay)
    return _global_limiter
