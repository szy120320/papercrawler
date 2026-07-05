"""
CORE 妫€绱㈤€傞厤鍣?
API 鏂囨。: https://core.ac.uk/services/api
"""

from __future__ import annotations

from loguru import logger

from papercrawler.models import Author, PaperMetadata, SearchQuery, AccessStatus
from papercrawler.search.base import BaseSearchAdapter

_BASE = "https://api.core.ac.uk/v3"


class CoreAdapter(BaseSearchAdapter):
    SOURCE_ID = "core"

    async def search(self, query: SearchQuery) -> list[PaperMetadata]:
        if not self.api_key:
            logger.warning("[core] 鏈厤缃?API Key锛岃烦杩?CORE 妫€绱€傝鍦?config.toml 涓缃?api_keys.core")
            return []

        q = query.build_text_query()
        if query.doi:
            q = f"doi:{query.doi}"

        # ---------------------------------------------------------------
        # 鍒嗛〉鎶撳彇:CORE 鐢?offset + limit 缈婚〉(2026-07 鍔?
        #   鍗曢〉 limit 涓婇檺 100
        #   缁堟淇″彿:杩斿洖鏉＄洰 < limit,鎴?total_hits 瀛楁鑰楀敖
        # 闃插崱:max_pages=50 涓婇檺(2026-07-05 鈫?from 20),榛樿 50 脳 100 = 5000 鏉?
        # ---------------------------------------------------------------
        page_size = min(query.page_size, 100)

        headers = {"Authorization": f"Bearer {self.api_key}"}
        results: list[PaperMetadata] = []
        seen: set[str] = set()
        offset = 0
        max_pages = 50
        total_hits: int | None = None

        for _page in range(max_pages):
            params = {
                "q": q,
                "limit": page_size,
                "offset": offset,
            }

            data = await self._get_json(
                f"{_BASE}/search/works", params=params, headers=headers
            )
            if not data:
                break

            items = data.get("results", []) or []
            if total_hits is None:
                try:
                    total_hits = int(data.get("totalHits", 0))
                except (TypeError, ValueError):
                    total_hits = None

            if not items:
                break

            for paper in (self._parse(it) for it in items):
                if not paper:
                    continue
                key = (paper.doi or "").lower() or (paper.url or paper.title or "")
                if key and key in seen:
                    continue
                seen.add(key)
                results.append(paper)

            offset += len(items)
            if len(items) < params["limit"]:
                break
            if total_hits is not None and offset >= total_hits:
                break

        logger.debug(f"[core] 找到 {len(results)} 篇论文")
        return self._tag_source(results)

    def _parse(self, item: dict) -> PaperMetadata | None:
        try:
            authors = [
                Author(name=a.get("name", ""))
                for a in (item.get("authors") or [])
            ]
            doi = item.get("doi")
            pdf_url = item.get("downloadUrl") or item.get("fullTextLink")

            links = item.get("links") or [{}]
            source_urls = item.get("sourceFulltextUrls") or []
            url = source_urls[0] if source_urls else links[0].get("url")

            return PaperMetadata(
                title=item.get("title", "Unknown Title"),
                authors=authors,
                year=item.get("yearPublished"),
                journal=item.get("publisher"),
                doi=doi,
                url=url,
                abstract=item.get("abstract"),
                access_status=AccessStatus.OPEN_ACCESS_PDF if pdf_url else AccessStatus.UNKNOWN,
                oa_url=pdf_url,
                raw_ids={"core": str(item.get("id", ""))},
            )
        except (KeyError, AttributeError, TypeError, ValueError) as e:
            # 鍗曠瘒瑙ｆ瀽澶辫触:瀛楁缂哄け / 绫诲瀷閿?
            logger.opt(exception=True).debug(f"[core] 瑙ｆ瀽澶辫触: {e}")
            return None
