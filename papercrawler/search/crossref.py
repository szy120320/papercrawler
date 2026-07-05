"""
CrossRef 妫€绱㈤€傞厤鍣?
API 鏂囨。: https://api.crossref.org/swagger-ui/index.html
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

        # ---------------------------------------------------------------
        # 分页抓取:CrossRef 用 offset= 翻页(2026-07 加)
        #   单页 rows 上限 100
        #   终止信号:返回条目 < rows,或 total-results 字段耗尽
        # 防卡:max_pages=50 上限(2026-07-05 ↑ from 20),默认 50 × 100 = 5000 条
        # 2026-07-05 v1.3.0: 不再用 query.page_size 截顶
        # ---------------------------------------------------------------
        page_size = min(query.page_size, 100)

        base_params: dict = {
            "rows": page_size,
            "select": "title,author,published,abstract,DOI,URL,container-title,volume,issue,page,is-referenced-by-count,subject",
            "mailto": "user@example.com",
        }

        if query.title:
            base_params["query.title"] = query.title
        if query.author:
            base_params["query.author"] = query.author
        if query.query:
            base_params["query"] = query.query

        if query.year_from or query.year_to:
            frm = query.year_from or 1900
            to = query.year_to or 2100
            base_params["filter"] = f"from-pub-date:{frm},until-pub-date:{to}"

        if query.sort == "citations":
            base_params["sort"] = "is-referenced-by-count"
            base_params["order"] = "desc"
        elif query.sort == "date":
            base_params["sort"] = "published"
            base_params["order"] = "desc"

        results: list[PaperMetadata] = []
        seen: set[str] = set()
        offset = 0
        max_pages = 50
        total_reported: int | None = None

        for _page in range(max_pages):
            params = dict(base_params)
            params["offset"] = offset
            params["rows"] = page_size

            data = await self._get_json(f"{_BASE}/works", params=params)
            if not data:
                break

            msg = data.get("message") or {}
            items = msg.get("items") or []
            if total_reported is None:
                try:
                    total_reported = int(msg.get("total-results", 0))
                except (TypeError, ValueError):
                    total_reported = None

            if not items:
                break

            for item in items:
                paper = self._parse(item)
                if not paper:
                    continue
                key = (paper.doi or "").strip().lower() or (paper.title or "")
                if key and key in seen:
                    continue
                seen.add(key)
                results.append(paper)

            offset += len(items)
            # Crossref 终止信号:返回条目 < rows 或 已达总数
            if len(items) < params["rows"]:
                break
            if total_reported is not None and offset >= total_reported:
                break

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
            # 鍗曠瘒瑙ｆ瀽澶辫触:瀛楁缂哄け / 绫诲瀷閿?
            logger.opt(exception=True).debug(f"[crossref] 瑙ｆ瀽澶辫触: {e}")
            return None
