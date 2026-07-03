"""
ChemRxiv 检索适配器
API 文档: https://chemrxiv.org/engage/chemrxiv/public-api/v1

ChemRxiv 是由美国化学学会（ACS）运营的化学预印本服务器，
所有内容均为开放获取，具有官方公开 REST API，无需认证。
"""

from __future__ import annotations

import re
from typing import Optional
from urllib.parse import quote

from loguru import logger

from papercrawler.models import Author, PaperMetadata, SearchQuery, AccessStatus
from papercrawler.search.base import BaseSearchAdapter

_BASE = "https://chemrxiv.org/engage/chemrxiv/public-api/v1"
_MAX_PER_PAGE = 50  # ChemRxiv API 每页最多 50 条

# ChemRxiv 由 Cambridge Open Engage 平台托管，该平台会拦截常见爬虫 User-Agent。
# 必须使用接近浏览器的 UA，否则返回 403。GET 请求不应设置 Content-Type。
_HEADERS = {
    "Accept": "application/json",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
}


class ChemRxivAdapter(BaseSearchAdapter):
    SOURCE_ID = "chemrxiv"

    async def search(self, query: SearchQuery) -> list[PaperMetadata]:
        if query.doi:
            return await self._by_doi(query.doi)

        # 构建搜索词
        term = self._build_term(query)
        if not term:
            logger.warning("[chemrxiv] 没有有效的检索词，跳过")
            return []

        params: dict = {
            "term": term,
            "limit": min(query.max_results, _MAX_PER_PAGE),
            "skip": 0,
            "sort": self._sort_param(query.sort),
        }

        if query.year_from:
            params["searchDateFrom"] = f"{query.year_from}-01-01"
        if query.year_to:
            params["searchDateTo"] = f"{query.year_to}-12-31"

        results: list[PaperMetadata] = []
        total_needed = query.max_results

        while len(results) < total_needed:
            params["skip"] = len(results)
            params["limit"] = min(total_needed - len(results), _MAX_PER_PAGE)

            data = await self._get_json(f"{_BASE}/items", params=params, headers=_HEADERS)
            if not data:
                break

            hits = data.get("itemHits", [])
            if not hits:
                break

            for item in hits:
                paper = self._parse(item)
                if paper:
                    results.append(paper)

            # 判断是否已获取全部结果
            total_count = data.get("totalCount", 0)
            if len(results) >= total_count or len(hits) < params["limit"]:
                break

        logger.debug(f"[chemrxiv] 找到 {len(results)} 篇论文")
        return self._tag_source(results)

    async def _by_doi(self, doi: str) -> list[PaperMetadata]:
        """通过 DOI 精确检索单篇论文"""
        # DOI 含 / 需要 URL 编码后才能安全嵌入路径
        encoded_doi = quote(doi, safe="")
        data = await self._get_json(f"{_BASE}/items/doi/{encoded_doi}", headers=_HEADERS)
        if not data:
            return []
        paper = self._parse(data)
        return self._tag_source([paper]) if paper else []

    def _build_term(self, query: SearchQuery) -> str:
        """将 SearchQuery 转换为 ChemRxiv 的 term 参数"""
        if query.doi:
            return query.doi
        parts = []
        if query.title:
            parts.append(query.title)
        if query.query:
            parts.append(query.query)
        if query.author:
            parts.append(query.author)
        return " ".join(parts).strip()

    def _sort_param(self, sort: str) -> str:
        return {
            "date":       "published_date",
            "citations":  "citation_count",
            "relevance":  "published_date",
        }.get(sort, "published_date")

    def _parse(self, item: dict) -> Optional[PaperMetadata]:
        try:
            # 作者
            authors = []
            for a in item.get("authors", []):
                first = a.get("firstName", "").strip()
                last = a.get("lastName", "").strip()
                name = f"{first} {last}".strip() if first else last
                if not name:
                    continue
                orcid = a.get("orcid") or None
                insts = a.get("institutions", [])
                affil = insts[0].get("name") if insts else None
                authors.append(Author(name=name, affiliation=affil, orcid=orcid))

            # DOI
            doi = item.get("doi") or None

            # PDF 直链（来自 asset 字段）
            asset = item.get("asset") or {}
            oa_url = asset.get("url") or None

            # 发布年份
            pub_date = item.get("publishedDate") or item.get("approvedDate") or ""
            year: Optional[int] = None
            m = re.match(r"(\d{4})", pub_date)
            if m:
                year = int(m.group(1))

            # 关键词（合并 keywords 和 categories）
            keywords = list(item.get("keywords") or [])
            for cat in item.get("categories") or []:
                cat_name = cat.get("name", "")
                if cat_name and cat_name not in keywords:
                    keywords.append(cat_name)

            # 摘要（可能含 HTML 标签）
            abstract_raw = item.get("abstract") or ""
            abstract = re.sub(r"<[^>]+>", "", abstract_raw).strip() or None

            # ChemRxiv 内部 ID
            item_id = item.get("_id") or item.get("id") or ""

            return PaperMetadata(
                title=item.get("title") or "Unknown Title",
                authors=authors,
                year=year,
                journal="ChemRxiv preprint",
                doi=doi,
                url=f"https://chemrxiv.org/engage/chemrxiv/article-details/{item_id}" if item_id else None,
                abstract=abstract,
                keywords=keywords,
                access_status=AccessStatus.OPEN_ACCESS_PREPRINT,
                oa_url=oa_url,
                preprint_url=f"https://chemrxiv.org/engage/chemrxiv/article-details/{item_id}" if item_id else None,
                raw_ids={"chemrxiv": item_id, "doi": doi},
            )
        except (KeyError, AttributeError, TypeError, ValueError) as e:
            # 单篇解析失败:字段缺失 / 类型错
            logger.opt(exception=True).debug(f"[chemrxiv] 单篇解析失败: {e}")
            return None
