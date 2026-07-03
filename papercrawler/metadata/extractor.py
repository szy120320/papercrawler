"""
元数据提取器

负责在论文检索后补充和完善元数据：
- 通过 CrossRef 精化 DOI 对应的完整元数据
- 通过 Unpaywall 补充 OA 状态
- 合并各来源元数据（调用 utils.dedup.merge_papers）
"""

from __future__ import annotations

from typing import Optional

from loguru import logger

from papercrawler.access.checker import AccessChecker
from papercrawler.config import AppConfig, get_config
from papercrawler.models import PaperMetadata
from papercrawler.search.crossref import CrossRefAdapter
from papercrawler.search.semantic_scholar import SemanticScholarAdapter
from papercrawler.utils.dedup import merge_papers


class MetadataExtractor:
    """
    对检索结果进行元数据补充与标准化。

    主要职责：
    1. 确保每篇论文有 DOI（若无则尝试通过标题在 CrossRef 查询）
    2. 通过 AccessChecker 确定 OA 状态
    3. 补充缺失的摘要（若有 DOI，尝试 S2 补充）
    """

    def __init__(self, config: Optional[AppConfig] = None):
        self.config = config or get_config()
        self._crossref = CrossRefAdapter(
            timeout=self.config.download.read_timeout,
            request_delay=self.config.download.request_delay,
        )
        self._s2 = SemanticScholarAdapter(
            api_key=self.config.api_keys.semantic_scholar,
            timeout=self.config.download.read_timeout,
            request_delay=self.config.download.request_delay,
        )
        self._checker = AccessChecker(config=self.config)

    async def enrich(self, paper: PaperMetadata) -> PaperMetadata:
        """
        对单篇论文补充元数据：
        1. 若无摘要且有 DOI，从 CrossRef/S2 获取
        2. 检查 OA 状态
        """
        # 补充摘要
        if not paper.abstract:
            paper = await self._fetch_abstract(paper)

        # 检查 OA 状态
        paper = await self._checker.check(paper)

        return paper

    async def enrich_batch(self, papers: list[PaperMetadata]) -> list[PaperMetadata]:
        """批量补充元数据（先 OA 检查，后摘要补充）"""
        import asyncio
        from papercrawler.search.base import SourceError
        from httpx import HTTPError

        semaphore = asyncio.Semaphore(5)
        failure_counts = {"http_error": 0, "parse_error": 0, "timeout": 0, "other": 0}

        async def _enrich_one(p: PaperMetadata) -> PaperMetadata:
            async with semaphore:
                try:
                    return await self.enrich(p)
                except SourceError as e:
                    failure_counts[e.kind] = failure_counts.get(e.kind, 0) + 1
                    logger.debug(f"[enrich] {e.kind}: {p.title[:60]} — {e}")
                    return p
                except HTTPError as e:
                    failure_counts["http_error"] += 1
                    logger.opt(exception=True).debug(f"[enrich] HTTP 错误: {p.title[:60]}")
                    return p
                except Exception as e:
                    failure_counts["other"] += 1
                    logger.opt(exception=True).warning(f"[enrich] 未预期异常: {p.title[:60]} — {e}")
                    return p

        results = list(await asyncio.gather(*[_enrich_one(p) for p in papers]))
        if any(v > 0 for v in failure_counts.values()):
            logger.info(
                f"[enrich 失败统计] " + ", ".join(f"{k}={v}" for k, v in failure_counts.items() if v > 0)
            )
        return results

    async def _fetch_abstract(self, paper: PaperMetadata) -> PaperMetadata:
        """尝试通过 S2 或 CrossRef 补充摘要"""
        if not paper.doi:
            return paper

        # 先尝试 Semantic Scholar
        try:
            results = await self._s2._by_doi(paper.doi)
            if results and results[0].abstract:
                paper.abstract = results[0].abstract
                if "semantic_scholar" not in paper.sources:
                    paper.sources.append("semantic_scholar")
                return paper
        except Exception as e:
            logger.opt(exception=True).debug(f"[enrich-abstract] S2 失败 DOI={paper.doi}: {e}")

        # 再尝试 CrossRef
        try:
            results = await self._crossref._by_doi(paper.doi)
            if results and results[0].abstract:
                paper.abstract = results[0].abstract
        except Exception as e:
            logger.opt(exception=True).debug(f"[enrich-abstract] CrossRef 失败 DOI={paper.doi}: {e}")

        return paper
