"""
CORE 检索适配器
API 文档: https://core.ac.uk/services/api
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
            logger.warning("[core] 未配置 API Key，跳过 CORE 检索。请在 config.toml 中设置 api_keys.core")
            return []

        q = query.build_text_query()
        if query.doi:
            q = f"doi:{query.doi}"

        # ---------------------------------------------------------------
        # 分页抓取:CORE 用 offset + limit 翻页(2026-07 加)
        #   单页 limit 上限 100
        #   终止信号:返回条目 < limit,或 total_hits 字段耗尽
        # 防卡:max_pages=50 上限(2026-07-05 ↑ from 20),默认 50 × 100 = 5000 条
        # ---------------------------------------------------------------
        page_size = min(query.max_results, 100)
        if query.max_results > 100:
            page_size = 100

        headers = {"Authorization": f"Bearer {self.api_key}"}
        results: list[PaperMetadata] = []
        seen: set[str] = set()
        offset = 0
        max_pages = 50
        total_hits: int | None = None

        for _page in range(max_pages):
            params = {
                "q": q,
                "limit": min(page_size, query.max_results - len(results)),
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
            if len(results) >= query.max_results:
                break

        logger.debug(f"[core] 找到 {len(results)} 篇论文")
        return self._tag_source(results[: query.max_results])

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
            # 单篇解析失败:字段缺失 / 类型错
            logger.opt(exception=True).debug(f"[core] 解析失败: {e}")
            return None
