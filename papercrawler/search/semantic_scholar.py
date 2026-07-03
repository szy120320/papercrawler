"""
Semantic Scholar 检索适配器
API 文档: https://api.semanticscholar.org/api-docs/
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

        params: dict = {
            "query": query.build_text_query(),
            "limit": min(query.max_results, 100),
            "fields": _FIELDS,
        }
        if query.year_from and query.year_to:
            params["year"] = f"{query.year_from}-{query.year_to}"
        elif query.year_from:
            params["year"] = f"{query.year_from}-"
        elif query.year_to:
            params["year"] = f"-{query.year_to}"

        if query.sort == "citations":
            params["sort"] = "citationCount:desc"
        elif query.sort == "date":
            params["sort"] = "publicationDate:desc"

        headers = {}
        if self.api_key:
            headers["x-api-key"] = self.api_key

        data = await self._get(f"{_BASE}/paper/search", params=params, headers=headers)
        if not data:
            return []

        results = []
        for item in data.get("data", []):
            paper = self._parse(item)
            if paper:
                results.append(paper)

        logger.debug(f"[semantic_scholar] 找到 {len(results)} 篇论文")
        return self._tag_source(results)

    async def _by_doi(self, doi: str) -> list[PaperMetadata]:
        headers = {}
        if self.api_key:
            headers["x-api-key"] = self.api_key
        data = await self._get(
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
        except Exception as e:
            logger.debug(f"[semantic_scholar] 解析失败: {e}")
            return None
