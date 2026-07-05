"""
arXiv 妫€绱㈤€傞厤鍣?API 鏂囨。: https://info.arxiv.org/help/api/index.html
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET

from loguru import logger

from papercrawler.models import Author, PaperMetadata, SearchQuery, AccessStatus
from papercrawler.search.base import BaseSearchAdapter, SourceError

_BASE = "https://export.arxiv.org/api/query"
_NS = {
    "atom":  "http://www.w3.org/2005/Atom",
    "arxiv": "http://arxiv.org/schemas/atom",
}


class ArXivAdapter(BaseSearchAdapter):
    SOURCE_ID = "arxiv"

    async def search(self, query: SearchQuery) -> list[PaperMetadata]:
        search_query = self._build_query(query)
        sort_by = self._sort_by(query.sort)

        # ---------------------------------------------------------------
        # 鍒嗛〉鎶撳彇:ArXiv 浣跨敤 start= 鍋忕Щ缈婚〉(2026-07 鍔?
        #   鍗曢〉 page_size 涓婇檺 2000(2026-07-05 鈫?from 500),瀹炴祴 1000 绋冲畾
        #   缁堟淇″彿:杩斿洖鏉＄洰 < 璇锋眰鏁?鎴?opensearch:totalResults 鑰楀敖
        # 闃插崱:max_pages=20 涓婇檺(2026-07-05 鈫?from 10),榛樿 20 脳 1000 = 20000 鏉?        # ---------------------------------------------------------------
        page_size = min(query.page_size, 1000)   # 2026-07-05: ArXiv API 上限 1000(避开 2000 易超时)
        # 2026-07-05: 不再用 query.page_size 截顶,翻页到 max_pages 上限或 API 终止信号

        results: list[PaperMetadata] = []
        seen: set[str] = set()
        start = 0
        max_pages = 20

        for _page in range(max_pages):
            params = {
                "search_query": search_query,
                "start": start,
                "page_size": page_size,
                "sortBy": sort_by,
                "sortOrder": "descending",
            }

            try:
                xml_text = await self._get_text(_BASE, params=params)
            except SourceError as e:
                logger.debug(f"[arxiv] 检索失败: {e}")
                return self._tag_source(results)

            if not xml_text:
                break

            try:
                page_results = self._parse(xml_text, query)
            except ET.ParseError as e:
                logger.warning(f"[arxiv] XML 解析错误: {e}")
                break

            if not page_results:
                break

            for paper in page_results:
                # ArXiv id 跨页去重
                key = paper.url or (paper.title or "")
                if key and key in seen:
                    continue
                seen.add(key)
                results.append(paper)

            start += len(page_results)
            # ArXiv 终止信号:返回条目 < 请求条目 → 末尾
            if len(page_results) < params["page_size"]:
                break

        logger.debug(f"[arxiv] 找到 {len(results)} 篇论文")
        return self._tag_source(results)

    def _build_query(self, query: SearchQuery) -> str:
        if query.doi:
            arxiv_id = self._doi_to_arxiv_id(query.doi)
            if arxiv_id:
                return f"id:{arxiv_id}"

        parts = []
        if query.title:
            parts.append(f"ti:{query.title}")
        if query.author:
            parts.append(f"au:{query.author}")
        if query.query:
            parts.append(f"all:{query.query}")
        return " AND ".join(parts) if parts else f"all:{query.build_text_query()}"

    def _doi_to_arxiv_id(self, doi: str) -> str | None:
        m = re.search(r"arxiv[./:](\d{4}\.\d{4,5})", doi, re.IGNORECASE)
        return m.group(1) if m else None

    def _sort_by(self, sort: str) -> str:
        return {"date": "submittedDate", "relevance": "relevance"}.get(sort, "relevance")

    def _parse(self, xml_text: str, query: SearchQuery) -> list[PaperMetadata]:
        root = ET.fromstring(xml_text)
        papers: list[PaperMetadata] = []
        for entry in root.findall("atom:entry", _NS):
            paper = self._parse_entry(entry, query)
            if paper:
                papers.append(paper)
        return papers

    def _parse_entry(self, entry: ET.Element, query: SearchQuery) -> PaperMetadata | None:
        try:
            title_el = entry.find("atom:title", _NS)
            title = (title_el.text or "").strip().replace("\n", " ") if title_el is not None else "Unknown"

            abstract_el = entry.find("atom:summary", _NS)
            abstract = (abstract_el.text or "").strip() if abstract_el is not None else None

            authors: list[Author] = []
            for a in entry.findall("atom:author", _NS):
                name_el = a.find("atom:name", _NS)
                if name_el is not None and name_el.text:
                    affil_el = a.find("arxiv:affiliation", _NS)
                    affil = affil_el.text if affil_el is not None else None
                    authors.append(Author(name=name_el.text.strip(), affiliation=affil))

            # arxiv ID & PDF URL
            arxiv_id = None
            pdf_url = None
            for link in entry.findall("atom:link", _NS):
                href = link.get("href", "")
                rel = link.get("rel", "")
                title_attr = link.get("title", "")
                if title_attr == "pdf":
                    pdf_url = href
                elif rel == "alternate":
                    m = re.search(r"arxiv\.org/abs/(.+)", href)
                    if m:
                        arxiv_id = m.group(1)

            if arxiv_id and not pdf_url:
                pdf_url = f"https://arxiv.org/pdf/{arxiv_id}.pdf"

            # 鍙戣〃鏃堕棿
            published_el = entry.find("atom:published", _NS)
            year = None
            if published_el is not None and published_el.text:
                m = re.match(r"(\d{4})", published_el.text)
                year = int(m.group(1)) if m else None

            # 骞翠唤杩囨护
            if year and query.year_from and year < query.year_from:
                return None
            if year and query.year_to and year > query.year_to:
                return None

            # DOI(閮ㄥ垎 arXiv 璁烘枃鏈?
            doi_el = entry.find("arxiv:doi", _NS)
            doi = doi_el.text.strip() if doi_el is not None and doi_el.text else None

            # 鍒嗙被 = 鍏抽敭璇?
            categories = [
                c.get("term", "")
                for c in entry.findall("atom:category", _NS)
                if c.get("term")
            ]

            return PaperMetadata(
                title=title,
                authors=authors,
                year=year,
                journal="arXiv preprint",
                doi=doi,
                url=f"https://arxiv.org/abs/{arxiv_id}" if arxiv_id else None,
                abstract=abstract,
                keywords=categories,
                access_status=AccessStatus.OPEN_ACCESS_PREPRINT,
                oa_url=pdf_url,
                preprint_url=f"https://arxiv.org/abs/{arxiv_id}" if arxiv_id else None,
                raw_ids={"arxiv": arxiv_id},
            )
        except (KeyError, AttributeError, TypeError, ValueError) as e:
            # 鍗曠瘒瑙ｆ瀽澶辫触:瀛楁缂哄け鎴栫被鍨嬮敊
            logger.opt(exception=True).debug(f"[arxiv] 鍗曠瘒瑙ｆ瀽澶辫触: {e}")
            return None
