"""
检索适配器基类 + SourceError 异常

公共能力(由 base 提供,各 adapter 共用):
  - _get_json() — 带速率限制 + 指数退避 + jitter 的 HTTP GET,返回 JSON dict
  - _get_text() — 同上,但返回原始 text(用于 XML / 纯文本端点)
  - _tag_source() — 给所有结果打上本数据源标签
  - 429 重试 / 401/403/404 区分 / API Key 缺失降级

各 adapter 只需实现:
  - SOURCE_ID: str
  - async search(query) -> list[PaperMetadata]
  - _parse(item) -> PaperMetadata | None  (子类各自实现)
"""

from __future__ import annotations

import asyncio
import json
import random
from abc import ABC, abstractmethod
from typing import Optional

import httpx
from loguru import logger

from papercrawler.models import PaperMetadata, SearchQuery
from papercrawler.utils.rate_limiter import get_rate_limiter


# ============================================================================
# 自定义异常
# ============================================================================

class SourceError(Exception):
    """检索数据源执行失败(网络 / 解析 / 限速等)。

    Attributes:
        source_id: 数据源标识(类 SOURCE_ID)
        kind: 失败类型,用于区分 HTTP / parse / timeout / other
        cause: 底层异常(若有)
    """

    def __init__(self, source_id: str, kind: str, message: str, cause: Optional[BaseException] = None):
        super().__init__(message)
        self.source_id = source_id
        self.kind = kind          # "http_error" | "parse_error" | "rate_limit" | "timeout" | "other"
        self.cause = cause


# ============================================================================
# HTTP 头部常量
# ============================================================================

DEFAULT_USER_AGENT = "PaperDownloader/1.0 (mailto:user@example.com)"


# ============================================================================
# 抽象基类
# ============================================================================

class BaseSearchAdapter(ABC):
    """所有检索数据源适配器的抽象基类"""

    #: 数据源标识符(子类必须定义)
    SOURCE_ID: str = "base"

    #: 最大 429 重试次数(子类可覆盖)
    _MAX_RATE_LIMIT_RETRIES: int = 2

    def __init__(self, api_key: str = "", timeout: int = 30, request_delay: float = 1.0):
        self.api_key = api_key
        self.timeout = timeout
        self._limiter = get_rate_limiter(request_delay)

    # ------------------------------------------------------------------
    # 公共接口 — 必须实现
    # ------------------------------------------------------------------

    @abstractmethod
    async def search(self, query: SearchQuery) -> list[PaperMetadata]:
        """
        执行检索,返回论文元数据列表。
        子类需实现此方法。
        """
        ...

    # ------------------------------------------------------------------
    # HTTP 工具 — JSON
    # ------------------------------------------------------------------

    async def _get_json(self, url: str, params: dict | None = None,
                        headers: dict | None = None) -> dict | None:
        """
        执行带速率限制 + 指数退避 + jitter 的 HTTP GET,返回 JSON dict。

        429 处理策略:
          - 优先读取响应头 Retry-After(单位秒)
          - 否则按指数退避(10s, 30s)+ jitter
          - 超过 _MAX_RATE_LIMIT_RETRIES 次后放弃,返回 None

        Returns:
            dict: 成功返回解析后的 JSON
            None: 失败(404 / 限速持续 / 网络异常 / JSON 解析失败)

        Raises:
            SourceError: 当失败需要被 SearchManager 计入失败计数时
                         (None 返回表示可静默处理的失败)
        """
        raw = await self._get_raw(url, params, headers, expect_json=True)
        if raw is None:
            return None
        status, text = raw
        # _get_raw 已 raise_for_status,此处只剩解析
        try:
            return json.loads(text)
        except (ValueError, TypeError) as e:
            # JSON 解析失败 — 抛 SourceError 让 manager 计入 parse_error
            raise SourceError(self.SOURCE_ID, "parse_error", f"JSON 解析失败: {e}", cause=e)

    # ------------------------------------------------------------------
    # HTTP 工具 — 文本 (XML / 纯文本)
    # ------------------------------------------------------------------

    async def _get_text(self, url: str, params: dict | None = None,
                        headers: dict | None = None) -> str | None:
        """
        与 _get_json 共享限速 + 重试逻辑,但返回原始文本(用于 XML / 纯文本端点)。

        Returns:
            str: 成功返回响应文本
            None: 失败
        """
        raw = await self._get_raw(url, params, headers, expect_json=False)
        if raw is None:
            return None
        return raw[1]

    # ------------------------------------------------------------------
    # HTTP 核心(私有,被 _get_json / _get_text 共用)
    # ------------------------------------------------------------------

    async def _get_raw(self, url: str, params: dict | None = None,
                       headers: dict | None = None,
                       expect_json: bool = True) -> Optional[tuple[int, str]]:
        """
        底层 HTTP GET,带限速 + 429 重试 + jitter,返回 (status, text)。

        不会 raise;所有 HTTP 异常都被转换为 None 返回或 SourceError。
        """
        await self._limiter.wait(url)
        _headers = {"User-Agent": DEFAULT_USER_AGENT}
        if headers:
            _headers.update(headers)

        try:
            async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=True) as client:
                resp = await client.get(url, params=params, headers=_headers)

                # 指数退避 + jitter 处理 429
                backoff_base = [10, 30]
                for attempt in range(self._MAX_RATE_LIMIT_RETRIES):
                    if resp.status_code != 429:
                        break
                    retry_after = resp.headers.get("Retry-After")
                    if retry_after:
                        wait_sec = int(retry_after)
                    else:
                        base = backoff_base[attempt] if attempt < len(backoff_base) else 60
                        # jitter: ±20% 随机抖动,避免 thundering herd
                        wait_sec = base + random.uniform(-base * 0.2, base * 0.2)

                    self._log_rate_limit(wait_sec, attempt + 1)
                    await asyncio.sleep(wait_sec)
                    resp = await client.get(url, params=params, headers=_headers)

                if resp.status_code == 429:
                    self._log_rate_limit_give_up()
                    return None

                # 404: 资源不存在,常见且不致命,直接返回 None
                if resp.status_code == 404:
                    logger.debug(f"[{self.SOURCE_ID}] 资源不存在 (404): {url}")
                    return None

                resp.raise_for_status()
                return resp.status_code, resp.text

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
            else:
                logger.warning(
                    f"[{self.SOURCE_ID}] HTTP 错误 {e.response.status_code}: {url}"
                )
            return None
        except httpx.TimeoutException as e:
            logger.warning(f"[{self.SOURCE_ID}] 请求超时: {url} — {e}")
            return None
        except httpx.HTTPError as e:
            # 网络层错误 (ConnectError / ReadError 等)
            logger.warning(f"[{self.SOURCE_ID}] 网络错误: {url} — {e}")
            return None

    # ------------------------------------------------------------------
    # 通用工具
    # ------------------------------------------------------------------

    def _log_rate_limit(self, wait_sec: int, attempt: int) -> None:
        """记录限速等待日志;无 API Key 的来源降级为 DEBUG"""
        msg = f"[{self.SOURCE_ID}] 速率限制,等待 {wait_sec:.1f}s (第 {attempt} 次重试)"
        if self.api_key:
            logger.warning(msg)
        else:
            logger.debug(msg)

    def _log_rate_limit_give_up(self) -> None:
        """超过重试次数后的日志"""
        msg = f"[{self.SOURCE_ID}] 速率限制持续,跳过本次请求"
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