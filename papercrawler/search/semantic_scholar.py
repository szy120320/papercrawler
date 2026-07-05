"""
Semantic Scholar 妫€绱㈤€傞厤鍣?
API 鏂囨。: https://api.semanticscholar.org/api-docs/
"""

from __future__ import annotations

from loguru import logger

from papercrawler.models import Author, PaperMetadata, SearchQuery, AccessStatus
from papercrawler.search.base import BaseSearchAdapter

_BASE = "https://api.semanticscholar.org/graph/v1"
_FIELDS = (
    "title,authors,year,abstract,openAccessPdf,externalIds,"
    "citationCount,referenceCount,journal,publicationVenue,"
    "publicationTypes,fieldsOfStudy,url"
)


class SemanticScholarAdapter(BaseSearchAdapter):
    SOURCE_ID = "semantic_scholar"

    async def search(self, query: SearchQuery) -> list[PaperMetadata]:
        if query.doi:
            return await self._by_doi(query.doi)

        # ---------------------------------------------------------------
        # 分页抓取:Semantic Scholar 用 offset= 翻页 + token
        #   单页 limit 上限 100
        #   终止信号:返回条目 < limit,或 next 字段为 null/空
        # 2026-07-05 v1.3.0: 不再用 query.page_size 截顶
        # ---------------------------------------------------------------
        page_size = min(query.page_size, 100)

        base_params: dict = {
            "query": query.build_text_query(),
            "limit": page_size,
            "fields": _FIELDS,
        }
        if query.year_from and query.year_to:
            base_params["year"] = f"{query.year_from}-{query.year_to}"
        elif query.year_from:
            base_params["year"] = f"{query.year_from}-"
        elif query.year_to:
            base_params["year"] = f"-{query.year_to}"

        if query.sort == "citations":
            base_params["sort"] = "citationCount:desc"
        elif query.sort == "date":
            base_params["sort"] = "publicationDate:desc"

        headers = {}
        if self.api_key:
            headers["x-api-key"] = self.api_key

        results: list[PaperMetadata] = []
        seen: set[str] = set()
        offset = 0
        max_pages = 40
        # 鏃?API key 鏃?S2 闄愰€熸洿涓?缈婚〉娆℃暟鍑忓崐
        if not self.api_key:
            max_pages = 5

        for _page in range(max_pages):
            params = dict(base_params)
            params["offset"] = offset
            params["limit"] = page_size

            data = await self._get_json(
                f"{_BASE}/paper/search", params=params, headers=headers
            )
            if not data:
                break

            items = data.get("data", [])
            if not items:
                break

            for item in items:
                paper = self._parse(item)
                if not paper:
                    continue
                ext = (paper.raw_ids or {}).get("semantic_scholar") or ""
                if not ext:
                    ext = (paper.doi or "").lower() or (paper.title or "")
                if ext and ext in seen:
                    continue
                seen.add(ext)
                results.append(paper)

            # S2 终止信号:返回条目 < limit,或 next 字段缺失
            if len(items) < params["limit"]:
                break
            if not data.get("next"):
                break
            offset += params["limit"]

        logger.debug(f"[semantic_scholar] 找到 {len(results)} 篇论文")
        return self._tag_source(results)

    async def _by_doi(self, doi: str) -> list[PaperMetadata]:
        headers = {}
        if self.api_key:
            headers["x-api-key"] = self.api_key
        data = await self._get_json(
            f"{_BASE}/paper/DOI:{doi}",
            params={"fields": _FIELDS},
            headers=headers,
        )
        if not data:
            return []
        paper = self._parse(data)
        return self._tag_source([paper]) if paper else []

    def _parse(self, item: dict) -> PaperMetadata | None:
        try:
            authors = [
                Author(name=a.get("name", ""))
                for a in item.get("authors", [])
            ]
            ext_ids = item.get("externalIds", {}) or {}
            doi = ext_ids.get("DOI")
            arxiv_id = ext_ids.get("ArXiv")

            oa_pdf = item.get("openAccessPdf") or {}
            oa_url = oa_pdf.get("url") if oa_pdf else None

            access = AccessStatus.UNKNOWN
            if oa_url:
                if arxiv_id:
                    access = AccessStatus.OPEN_ACCESS_PREPRINT
                else:
                    access = AccessStatus.OPEN_ACCESS_PDF

            journal_info = item.get("journal") or item.get("publicationVenue") or {}
            journal_name = (
                journal_info.get("name") if isinstance(journal_info, dict) else None
            )

            return PaperMetadata(
                title=item.get("title", "Unknown Title"),
                authors=authors,
                year=item.get("year"),
                journal=journal_name,
                doi=doi,
                url=item.get("url"),
                abstract=item.get("abstract"),
                citations_count=item.get("citationCount"),
                references_count=item.get("referenceCount"),
                access_status=access,
                oa_url=oa_url,
                preprint_url=f"https://arxiv.org/abs/{arxiv_id}" if arxiv_id else None,
                raw_ids={
                    "semantic_scholar": item.get("paperId"),
                    "arxiv": arxiv_id,
                    "pubmed": ext_ids.get("PubMed"),
                },
            )
        except (KeyError, AttributeError, TypeError, ValueError) as e:
            # 鍗曠瘒瑙ｆ瀽澶辫触:瀛楁缂哄け / 绫诲瀷閿?
            logger.opt(exception=True).debug(f"[semantic_scholar] 瑙ｆ瀽澶辫触: {e}")
            return None
