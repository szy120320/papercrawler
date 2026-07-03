"""
arXiv 检索适配器
API 文档: https://info.arxiv.org/help/api/index.html
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET

import httpx
from loguru import logger

from papercrawler.models import Author, PaperMetadata, SearchQuery, AccessStatus
from papercrawler.search.base import BaseSearchAdapter

_BASE = "https://export.arxiv.org/api/query"
_NS = {
    "atom":  "http://www.w3.org/2005/Atom",
    "arxiv": "http://arxiv.org/schemas/atom",
}


class ArXivAdapter(BaseSearchAdapter):
    SOURCE_ID = "arxiv"

    async def search(self, query: SearchQuery) -> list[PaperMetadata]:
        search_query = self._build_query(query)
        params = {
            "search_query": search_query,
            "max_results": min(query.max_results, 200),
            "sortBy": self._sort_by(query.sort),
            "sortOrder": "descending",
        }

        try:
            await self._limiter.wait(_BASE)
            async with httpx.AsyncClient(
                timeout=self.timeout, follow_redirects=True
            ) as client:
                resp = await client.get(
                    _BASE,
                    params=params,
                    headers={"User-Agent": "PaperDownloader/1.0 (mailto:user@example.com)"},
                )
                resp.raise_for_status()
                xml_text = resp.text
        except Exception as e:
            logger.warning(f"[arxiv] 请求失败: {e}")
            return []

        results = self._parse(xml_text, query)
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
        papers = []
        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError as e:
            logger.warning(f"[arxiv] XML 解析错误: {e}")
            return []

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

            authors = []
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

            # 发表时间
            published_el = entry.find("atom:published", _NS)
            year = None
            if published_el is not None and published_el.text:
                m = re.match(r"(\d{4})", published_el.text)
                year = int(m.group(1)) if m else None

            # 年份过滤
            if year and query.year_from and year < query.year_from:
                return None
            if year and query.year_to and year > query.year_to:
                return None

            # DOI（部分 arXiv 论文有）
            doi_el = entry.find("arxiv:doi", _NS)
            doi = doi_el.text.strip() if doi_el is not None and doi_el.text else None

            # 分类 = 关键词
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
        except Exception as e:
            logger.debug(f"[arxiv] 单篇解析失败: {e}")
            return None
