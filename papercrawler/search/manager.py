"""
SearchManager — 并发调度多数据源检索 + 去重

失败分类:
  - ok:        成功返回
  - empty:     成功返回 0 篇
  - http_error: HTTP 4xx/5xx (401/403/404/429 持续)
  - parse_error: JSON/XML 解析失败
  - timeout:   请求超时
  - other:     其他未预期异常(带 traceback)

失败计数写入 SourceStats 结构,SearchManager 聚合后给 CLI 显示。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Optional

from loguru import logger

from papercrawler.config import AppConfig, get_config
from papercrawler.models import PaperMetadata, SearchQuery
from papercrawler.search.arxiv import ArXivAdapter
from papercrawler.search.base import BaseSearchAdapter, SourceError
from papercrawler.search.chemrxiv_via_crossref import ChemRxivViaCrossrefAdapter
from papercrawler.search.core import CoreAdapter
from papercrawler.search.crossref import CrossRefAdapter
from papercrawler.search.openalex import OpenAlexAdapter
from papercrawler.search.pubmed import PubMedAdapter
from papercrawler.search.semantic_scholar import SemanticScholarAdapter
from papercrawler.utils.dedup import deduplicate


# ============================================================================
# 失败计数结构
# ============================================================================

@dataclass
class SourceStats:
    """单个数据源的检索结果统计"""
    ok_count:        int = 0   # 成功返回的论文数(可能为 0,表示该源没命中)
    failure_kind:    Optional[str] = None  # None / "http_error" / "parse_error" / "timeout" / "other"
    failure_message: Optional[str] = None  # 失败详细信息(给日志用)


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
        "chemrxiv_via_crossref": ChemRxivViaCrossrefAdapter(
            timeout=dl.read_timeout,
            request_delay=dl.request_delay,
        ),
        "core": CoreAdapter(
            api_key=keys.core,
            timeout=dl.read_timeout,
            request_delay=dl.request_delay,
        ),
        # 2026-07-05 改用 chemrxiv_via_crossref(通过 Crossref 检索,
        # 绕开 ChemRxiv 官方 API 的 Cloudflare 拦截)
    }

    # 为各数据源设置域名级专属延迟,避免触发速率限制
    from papercrawler.utils.rate_limiter import get_rate_limiter
    limiter = get_rate_limiter()

    # Semantic Scholar 免费层:~100 req/5min ≈ 1 req/3s,无 key 时设 3s 间隔
    if not keys.semantic_scholar:
        limiter.set_delay("api.semanticscholar.org", 3.0)

    # arXiv API 官方文档规定:相邻请求至少间隔 3 秒
    limiter.set_delay("export.arxiv.org", 3.5)

    return adapters


class SearchManager:
    """
    并发调用多个检索适配器,合并并去重结果。
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
        并发执行所有激活的数据源检索,返回去重、作者过滤后的结果列表。
        """
        papers, _stats = await self.search_with_stats(query, show_progress=show_progress)
        return papers

    async def search_with_stats(
        self,
        query: SearchQuery,
        show_progress: bool = True,
    ) -> tuple[list[PaperMetadata], dict[str, SourceStats]]:
        """
        并发执行所有激活的数据源检索,同时返回每个数据源的统计。

        2026-07-05 v1.3.0 行为变更:
          - 年份范围(year_from..year_to)**拆成逐年调用**,
            避免一次查询触发数据源返回数量上限被截顶
          - 不再按 max_results 截顶(语义改为 page_size,只在 adapter 内部使用)

        Returns:
            (papers, source_stats)
            - papers: 去重、过滤后的结果列表
            - source_stats: {source_id: SourceStats}
              - ok_count=0 且 failure_kind=None → 该源没命中任何论文(正常)
              - failure_kind 不为空 → 该源检索失败
        """
        adapters = self._active_adapters(query.sources)
        if not adapters:
            logger.warning("没有可用的检索数据源")
            return [], {}

        logger.info(f"开始检索,使用数据源: {[a.SOURCE_ID for a in adapters]}")

        # 2026-07-05: 逐年查询。
        # 旧逻辑:一次传 year_from..year_to 给各 adapter,触发 API 截顶
        # 新逻辑:manager 在外层拆成单年,每个年份一轮并发调用各 adapter
        year_queries = self._split_year_range(query)

        if len(year_queries) > 1:
            logger.info(
                f"年份范围 {query.year_from}-{query.year_to} 拆成 {len(year_queries)} 个单年查询"
            )

        all_papers: list[PaperMetadata] = []
        # 用累加:同一源跨年累计
        source_stats: dict[str, SourceStats] = {
            a.SOURCE_ID: SourceStats() for a in adapters
        }

        for yq in year_queries:
            if len(year_queries) > 1:
                logger.info(f"  检索 {yq.year_from} 年 ...")
            tasks = [adapter.search(yq) for adapter in adapters]
            results_per_source = await asyncio.gather(*tasks, return_exceptions=True)

            for adapter, result in zip(adapters, results_per_source):
                sid = adapter.SOURCE_ID
                stats = source_stats[sid]

                if isinstance(result, SourceError):
                    if stats.failure_kind is None:
                        stats.failure_kind = result.kind
                        stats.failure_message = str(result)
                    logger.warning(f"[{sid}] {result.kind}: {result}")
                elif isinstance(result, Exception):
                    if stats.failure_kind is None:
                        stats.failure_kind = "other"
                        stats.failure_message = f"{type(result).__name__}: {result}"
                    logger.opt(exception=True).warning(f"[{sid}] 检索异常: {result}")
                elif isinstance(result, list):
                    stats.ok_count += len(result)   # 累加,不是覆盖
                    all_papers.extend(result)
                else:
                    if stats.failure_kind is None:
                        stats.failure_kind = "other"
                        stats.failure_message = f"unexpected return type: {type(result).__name__}"

        # 统计失败摘要日志
        failures = {sid: s for sid, s in source_stats.items() if s.failure_kind}
        if failures:
            logger.warning(
                f"检索失败的数据源({len(failures)}): "
                + ", ".join(f"{sid}={s.failure_kind}" for sid, s in failures.items())
            )

        logger.info(f"各数据源共返回 {len(all_papers)} 条结果,开始去重...")
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

        # 2026-07-05: 删除 max_results 截顶
        # 旧:deduped = deduped[: query.max_results]
        # 新:不截顶,翻页终止由各 adapter 的 cursor=null / total 耗尽 决定

        logger.info(f"去重后共 {len(deduped)} 篇论文")
        return deduped, source_stats

    def _split_year_range(self, query: SearchQuery) -> list[SearchQuery]:
        """
        把年份范围拆成多个单年 SearchQuery(2026-07-05 v1.3.0 新增)。

        拆分规则:
          - 无 year_from / year_to → 单个原 query(不限年份)
          - 有 year_from 且 year_to 缺失 → 从 year_from 到当前年
          - 有 year_to 且 year_from 缺失 → 从 1900 到 year_to
          - 都有 → 从 year_from 到 year_to 的所有单年
          - 只有一个(==)→ 单个原 query
        """
        if query.year_from is None and query.year_to is None:
            return [query]
        if query.year_from is not None and query.year_to is None:
            year_to = query.year_from  # 只有一年,直接返回
            return [query.model_copy(update={"year_from": year_to, "year_to": year_to})]
        if query.year_from is None and query.year_to is not None:
            year_from = query.year_to  # 只有一年
            return [query.model_copy(update={"year_from": year_from, "year_to": year_from})]

        # 两个都有
        frm, to = query.year_from, query.year_to
        if frm == to:
            return [query]
        if frm > to:
            frm, to = to, frm

        from copy import copy
        result = []
        for y in range(frm, to + 1):
            new_q = query.model_copy(update={"year_from": y, "year_to": y})
            result.append(new_q)
        return result


# ============================================================================
# 向后兼容 shim — 老代码 / 测试可能用 source_counts: dict[str, int]
# ============================================================================

def source_stats_to_int_map(source_stats: dict[str, SourceStats]) -> dict[str, int]:
    """
    把 SourceStats 映射回老的 {source_id: int} 接口:
      - 失败 → -1(与老行为一致)
      - 成功 → ok_count

    注:CLI _display_source_stats 现在用 SourceStats 直读,这个 shim 留作兼容。
    """
    return {
        sid: (-1 if s.failure_kind else s.ok_count)
        for sid, s in source_stats.items()
    }