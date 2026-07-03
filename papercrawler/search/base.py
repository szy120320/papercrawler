"""
检索适配器基类
"""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod

import httpx
from loguru import logger

from papercrawler.models import PaperMetadata, SearchQuery
from papercrawler.utils.rate_limiter import get_rate_limiter


class BaseSearchAdapter(ABC):
    """所有检索数据源适配器的抽象基类"""

    #: 数据源标识符（子类必须定义）
    SOURCE_ID: str = "base"

    #: 最大 429 重试次数（子类可覆盖）
    _MAX_RATE_LIMIT_RETRIES: int = 2

    def __init__(self, api_key: str = "", timeout: int = 30, request_delay: float = 1.0):
        self.api_key = api_key
        self.timeout = timeout
        self._limiter = get_rate_limiter(request_delay)

    # ------------------------------------------------------------------
    # 公共接口
    # ------------------------------------------------------------------

    @abstractmethod
    async def search(self, query: SearchQuery) -> list[PaperMetadata]:
        """
        执行检索，返回论文元数据列表。
        子类需实现此方法。
        """
        ...

    # ------------------------------------------------------------------
    # 工具方法
    # ------------------------------------------------------------------

    async def _get(self, url: str, params: dict | None = None,
                   headers: dict | None = None) -> dict | None:
        """
        执行带速率限制和指数退避重试的 HTTP GET 请求，返回 JSON 字典。

        429 处理策略：
        - 优先读取响应头 Retry-After（单位秒）
        - 若无该头则按指数退避（10s, 30s）
        - 超过 _MAX_RATE_LIMIT_RETRIES 次后放弃，返回 None
        """
        await self._limiter.wait(url)
        _headers = {"User-Agent": "PaperDownloader/1.0 (mailto:user@example.com)"}
        if headers:
            _headers.update(headers)

        try:
            async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=True) as client:
                resp = await client.get(url, params=params, headers=_headers)

                # 指数退避处理 429
                backoff_base = [10, 30]
                for attempt in range(self._MAX_RATE_LIMIT_RETRIES):
                    if resp.status_code != 429:
                        break
                    retry_after = resp.headers.get("Retry-After")
                    if retry_after:
                        wait_sec = int(retry_after)
                    else:
                        wait_sec = backoff_base[attempt] if attempt < len(backoff_base) else 60

                    self._log_rate_limit(wait_sec, attempt + 1)
                    await asyncio.sleep(wait_sec)
                    resp = await client.get(url, params=params, headers=_headers)

                if resp.status_code == 429:
                    self._log_rate_limit_give_up()
                    return None

                resp.raise_for_status()
                return resp.json()

        except httpx.HTTPStatusError as e:
            if e.response.status_code in (401, 403):
                if self.api_key:
                    logger.error(
                        f"[{self.SOURCE_ID}] 认证错误 ({e.response.status_code}): 请检查 API Key"
                    )
                else:
                    logger.warning(
                        f"[{self.SOURCE_ID}] 请求被拒绝 ({e.response.status_code}): {url}"
                    )
            elif e.response.status_code == 404:
                logger.debug(f"[{self.SOURCE_ID}] 资源不存在 (404): {url}")
            else:
                logger.warning(
                    f"[{self.SOURCE_ID}] HTTP 错误 {e.response.status_code}: {url}"
                )
            return None
        except Exception as e:
            logger.warning(f"[{self.SOURCE_ID}] 请求失败: {e}")
            return None

    def _log_rate_limit(self, wait_sec: int, attempt: int) -> None:
        """记录限速等待日志；无 API Key 的来源降级为 DEBUG"""
        msg = f"[{self.SOURCE_ID}] 速率限制，等待 {wait_sec}s (第 {attempt} 次重试)"
        if self.api_key:
            logger.warning(msg)
        else:
            logger.debug(msg)

    def _log_rate_limit_give_up(self) -> None:
        """超过重试次数后的日志"""
        msg = f"[{self.SOURCE_ID}] 速率限制持续，跳过本次请求"
        if self.api_key:
            logger.warning(msg)
        else:
            logger.debug(msg)

    def _tag_source(self, papers: list[PaperMetadata]) -> list[PaperMetadata]:
        """为所有结果打上本数据源标签"""
        for p in papers:
            if self.SOURCE_ID not in p.sources:
                p.sources.append(self.SOURCE_ID)
        return papers
