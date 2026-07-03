"""
CrossRef 检索适配器
API 文档: https://api.crossref.org/swagger-ui/index.html
"""

from __future__ import annotations

import re

from loguru import logger

from papercrawler.models import Author, PaperMetadata, SearchQuery, AccessStatus
from papercrawler.search.base import BaseSearchAdapter

_BASE = "https://api.crossref.org"


class CrossRefAdapter(BaseSearchAdapter):
    SOURCE_ID = "crossref"

    async def search(self, query: SearchQuery) -> list[PaperMetadata]:
        if query.doi:
            return await self._by_doi(query.doi)

        params: dict = {
            "rows": min(query.max_results, 100),
            "select": "title,author,published,abstract,DOI,URL,container-title,volume,issue,page,is-referenced-by-count,subject",
            "mailto": "user@example.com",
        }

        if query.title:
            params["query.title"] = query.title
        if query.author:
            params["query.author"] = query.author
        if query.query:
            params["query"] = query.query

        if query.year_from or query.year_to:
            frm = query.year_from or 1900
            to = query.year_to or 2100
            params["filter"] = f"from-pub-date:{frm},until-pub-date:{to}"

        if query.sort == "citations":
            params["sort"] = "is-referenced-by-count"
            params["order"] = "desc"
        elif query.sort == "date":
            params["sort"] = "published"
            params["order"] = "desc"

        data = await self._get_json(f"{_BASE}/works", params=params)
        if not data:
            return []

        results = []
        for item in (data.get("message", {}) or {}).get("items", []):
            paper = self._parse(item)
            if paper:
                results.append(paper)

        logger.debug(f"[crossref] 找到 {len(results)} 篇论文")
        return self._tag_source(results)

    async def _by_doi(self, doi: str) -> list[PaperMetadata]:
        data = await self._get_json(
            f"{_BASE}/works/{doi}",
            params={"mailto": "user@example.com"},
        )
        if not data:
            return []
        item = (data.get("message") or {})
        paper = self._parse(item)
        return self._tag_source([paper]) if paper else []

    def _parse(self, item: dict) -> PaperMetadata | None:
        try:
            titles = item.get("title", [])
            title = titles[0] if titles else "Unknown Title"

            authors = []
            for a in item.get("author", []):
                given = a.get("given", "")
                family = a.get("family", "")
                name = f"{family}, {given}".strip(", ") if family else given
                affils = a.get("affiliation", [])
                affil = affils[0].get("name") if affils else None
                orcid = a.get("ORCID", "").replace("http://orcid.org/", "").replace("https://orcid.org/", "") or None
                authors.append(Author(name=name, affiliation=affil, orcid=orcid))

            pub = item.get("published", {}) or item.get("published-print", {}) or {}
            dp = pub.get("date-parts", [[]])
            year = dp[0][0] if dp and dp[0] else None

            containers = item.get("container-title", [])
            journal = containers[0] if containers else None

            doi = item.get("DOI")
            abstract_raw = item.get("abstract", "") or ""
            abstract = re.sub(r"<[^>]+>", "", abstract_raw).strip() or None

            return PaperMetadata(
                title=title,
                authors=authors,
                year=year,
                journal=journal,
                volume=item.get("volume"),
                issue=item.get("issue"),
                pages=item.get("page"),
                doi=doi,
                url=item.get("URL"),
                abstract=abstract,
                citations_count=item.get("is-referenced-by-count"),
                keywords=item.get("subject", []),
                access_status=AccessStatus.UNKNOWN,
                raw_ids={"crossref": doi},
            )
        except (KeyError, AttributeError, TypeError, ValueError) as e:
            # 单篇解析失败:字段缺失 / 类型错
            logger.opt(exception=True).debug(f"[crossref] 解析失败: {e}")
            return None
