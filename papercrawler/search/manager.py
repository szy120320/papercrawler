"""
SearchManager — 并发调度多数据源检索 + 去重
"""

from __future__ import annotations

import asyncio
from typing import Optional

from loguru import logger

from papercrawler.config import AppConfig, get_config
from papercrawler.models import PaperMetadata, SearchQuery
from papercrawler.search.arxiv import ArXivAdapter
from papercrawler.search.base import BaseSearchAdapter
from papercrawler.search.chemrxiv import ChemRxivAdapter
from papercrawler.search.core import CoreAdapter
from papercrawler.search.crossref import CrossRefAdapter
from papercrawler.search.openalex import OpenAlexAdapter
from papercrawler.search.pubmed import PubMedAdapter
from papercrawler.search.semantic_scholar import SemanticScholarAdapter
from papercrawler.utils.dedup import deduplicate


def _build_adapters(config: AppConfig) -> dict[str, BaseSearchAdapter]:
    keys = config.api_keys
    dl = config.download
    adapters: dict[str, BaseSearchAdapter] = {
        "semantic_scholar": SemanticScholarAdapter(
            api_key=keys.semantic_scholar,
            timeout=dl.read_timeout,
            request_delay=dl.request_delay,
        ),
        "openalex": OpenAlexAdapter(
            timeout=dl.read_timeout,
            request_delay=dl.request_delay,
        ),
        "crossref": CrossRefAdapter(
            timeout=dl.read_timeout,
            request_delay=dl.request_delay,
        ),
        "pubmed": PubMedAdapter(
            api_key=keys.pubmed,
            timeout=dl.read_timeout,
            request_delay=dl.request_delay,
        ),
        "arxiv": ArXivAdapter(
            timeout=dl.read_timeout,
            request_delay=dl.request_delay,
        ),
        "core": CoreAdapter(
            api_key=keys.core,
            timeout=dl.read_timeout,
            request_delay=dl.request_delay,
        ),
        "chemrxiv": ChemRxivAdapter(
            timeout=dl.read_timeout,
            request_delay=dl.request_delay,
        ),
    }

    # 为各数据源设置域名级专属延迟，避免触发速率限制
    from papercrawler.utils.rate_limiter import get_rate_limiter
    limiter = get_rate_limiter()

    # Semantic Scholar 免费层：~100 req/5min ≈ 1 req/3s，无 key 时设 3s 间隔
    if not keys.semantic_scholar:
        limiter.set_delay("api.semanticscholar.org", 3.0)

    # arXiv API 官方文档规定：相邻请求至少间隔 3 秒
    limiter.set_delay("export.arxiv.org", 3.5)

    return adapters


class SearchManager:
    """
    并发调用多个检索适配器，合并并去重结果。
    """

    def __init__(self, config: Optional[AppConfig] = None):
        self.config = config or get_config()
        self._adapters = _build_adapters(self.config)

    def _active_adapters(self, requested: list[str]) -> list[BaseSearchAdapter]:
        enabled = self.config.sources.enabled
        if requested:
            sources = [s for s in requested if s in enabled]
        else:
            sources = enabled
        return [self._adapters[s] for s in sources if s in self._adapters]

    async def search(
        self,
        query: SearchQuery,
        show_progress: bool = True,
    ) -> list[PaperMetadata]:
        """
        并发执行所有激活的数据源检索，返回去重、作者过滤后的结果列表。
        """
        papers, _ = await self.search_with_stats(query, show_progress=show_progress)
        return papers

    async def search_with_stats(
        self,
        query: SearchQuery,
        show_progress: bool = True,
    ) -> tuple[list[PaperMetadata], dict[str, int]]:
        """
        并发执行所有激活的数据源检索，同时返回每个数据源的原始结果数量。

        Returns:
            (papers, source_counts)
            - papers: 去重、过滤后的结果列表
            - source_counts: {source_id: count}，值为负数表示该源检索失败
        """
        adapters = self._active_adapters(query.sources)
        if not adapters:
            logger.warning("没有可用的检索数据源")
            return [], {}

        logger.info(f"开始检索，使用数据源: {[a.SOURCE_ID for a in adapters]}")

        tasks = [adapter.search(query) for adapter in adapters]
        results_per_source = await asyncio.gather(*tasks, return_exceptions=True)

        all_papers: list[PaperMetadata] = []
        source_counts: dict[str, int] = {}

        for adapter, result in zip(adapters, results_per_source):
            if isinstance(result, Exception):
                logger.warning(f"[{adapter.SOURCE_ID}] 检索异常: {result}")
                source_counts[adapter.SOURCE_ID] = -1  # 用 -1 标记失败
            elif isinstance(result, list):
                source_counts[adapter.SOURCE_ID] = len(result)
                all_papers.extend(result)

        logger.info(f"各数据源共返回 {len(all_papers)} 条结果，开始去重...")
        deduped = deduplicate(all_papers)

        # 作者匹配评分与过滤
        if query.author:
            from papercrawler.utils.author_filter import filter_by_author_score
            threshold = self.config.filters.author_match_threshold
            deduped = filter_by_author_score(deduped, query.author, threshold)

        # 应用 OA 过滤
        if query.oa_only:
            from papercrawler.models import AccessStatus
            deduped = [
                p for p in deduped
                if p.access_status not in (
                    AccessStatus.METADATA_ONLY, AccessStatus.UNKNOWN
                )
            ]

        # 应用结果数量限制
        deduped = deduped[: query.max_results]

        logger.info(f"去重后共 {len(deduped)} 篇论文")
        return deduped, source_counts
