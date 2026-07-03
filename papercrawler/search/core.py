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

        params = {
            "q": q,
            "limit": min(query.max_results, 100),
            "offset": 0,
        }

        headers = {"Authorization": f"Bearer {self.api_key}"}
        data = await self._get_json(f"{_BASE}/search/works", params=params, headers=headers)
        if not data:
            return []

        results = []
        for item in data.get("results", []):
            paper = self._parse(item)
            if paper:
                results.append(paper)

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
            # 单篇解析失败:字段缺失 / 类型错
            logger.opt(exception=True).debug(f"[core] 解析失败: {e}")
            return None
