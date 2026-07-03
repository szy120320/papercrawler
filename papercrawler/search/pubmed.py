"""
PubMed (NCBI E-utilities) 检索适配器
API 文档: https://www.ncbi.nlm.nih.gov/books/NBK25500/
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

import httpx
from loguru import logger

from papercrawler.models import Author, PaperMetadata, SearchQuery, AccessStatus
from papercrawler.search.base import BaseSearchAdapter

_ESEARCH = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
_EFETCH  = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"


class PubMedAdapter(BaseSearchAdapter):
    SOURCE_ID = "pubmed"

    async def search(self, query: SearchQuery) -> list[PaperMetadata]:
        # 1. esearch 获取 PMID 列表
        term = self._build_term(query)
        params: dict = {
            "db": "pubmed",
            "term": term,
            "retmax": min(query.max_results, 100),
            "retmode": "json",
        }
        if self.api_key:
            params["api_key"] = self.api_key
        if query.year_from or query.year_to:
            frm = query.year_from or 1800
            to = query.year_to or 2100
            params["datetype"] = "pdat"
            params["mindate"] = str(frm)
            params["maxdate"] = str(to)

        data = await self._get(_ESEARCH, params=params)
        if not data:
            return []

        pmids = data.get("esearchresult", {}).get("idlist", [])
        if not pmids:
            return []

        # 2. efetch 批量获取元数据 XML（同样走 _get 保证限速与重试）
        fetch_params: dict = {
            "db": "pubmed",
            "id": ",".join(pmids),
            "retmode": "xml",
            "rettype": "abstract",
        }
        if self.api_key:
            fetch_params["api_key"] = self.api_key

        # efetch 返回 XML 而非 JSON，不能直接用 _get()（它调用 .json()）
        # 手动复用 limiter + 重试逻辑，返回原始 XML 文本
        xml_text = await self._get_xml(_EFETCH, params=fetch_params)
        if not xml_text:
            return []

        results = self._parse_xml(xml_text)
        logger.debug(f"[pubmed] 找到 {len(results)} 篇论文")
        return self._tag_source(results)

    async def _get_xml(self, url: str, params: dict | None = None) -> str | None:
        """
        执行带速率限制的 HTTP GET，返回响应文本（用于 XML 端点）。
        与 _get() 共享 limiter 和重试逻辑，但返回 text 而非 json。
        """
        await self._limiter.wait(url)
        _headers = {"User-Agent": "PaperDownloader/1.0 (mailto:user@example.com)"}
        try:
            import asyncio as _asyncio
            async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=True) as client:
                resp = await client.get(url, params=params, headers=_headers)

                # 重试 429（最多 2 次，指数退避）
                backoff_base = [10, 30]
                for attempt in range(self._MAX_RATE_LIMIT_RETRIES):
                    if resp.status_code != 429:
                        break
                    wait_sec = int(resp.headers.get("Retry-After", backoff_base[attempt]))
                    logger.debug(f"[pubmed] 速率限制，等待 {wait_sec}s (第 {attempt+1} 次重试)")
                    await _asyncio.sleep(wait_sec)
                    resp = await client.get(url, params=params, headers=_headers)

                if resp.status_code == 429:
                    logger.debug("[pubmed] 速率限制持续，跳过 efetch")
                    return None

                resp.raise_for_status()
                return resp.text
        except Exception as e:
            logger.warning(f"[pubmed] efetch 失败: {e}")
            return None

    def _build_term(self, query: SearchQuery) -> str:
        parts = []
        if query.doi:
            return query.doi + "[DOI]"
        if query.title:
            parts.append(f"{query.title}[Title]")
        if query.author:
            parts.append(f"{query.author}[Author]")
        if query.query:
            parts.append(query.query)
        return " AND ".join(parts) if parts else query.build_text_query()

    def _parse_xml(self, xml_text: str) -> list[PaperMetadata]:
        papers = []
        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError as e:
            logger.warning(f"[pubmed] XML 解析错误: {e}")
            return []

        for article in root.findall(".//PubmedArticle"):
            paper = self._parse_article(article)
            if paper:
                papers.append(paper)
        return papers

    def _parse_article(self, article: ET.Element) -> PaperMetadata | None:
        try:
            medline = article.find("MedlineCitation")
            if medline is None:
                return None
            art = medline.find("Article")
            if art is None:
                return None

            title_el = art.find("ArticleTitle")
            title = "".join(title_el.itertext()).strip() if title_el is not None else "Unknown"

            # 摘要
            abstract_parts = art.findall(".//AbstractText")
            abstract = " ".join("".join(p.itertext()) for p in abstract_parts).strip() or None

            # 作者
            authors = []
            for a in art.findall(".//Author"):
                last = a.findtext("LastName", "")
                fore = a.findtext("ForeName", "") or a.findtext("Initials", "")
                name = f"{last}, {fore}".strip(", ") if last else fore
                if name:
                    affil_el = a.find(".//AffiliationInfo/Affiliation")
                    affil = affil_el.text if affil_el is not None else None
                    orcid_el = a.find(".//Identifier[@Source='ORCID']")
                    orcid = orcid_el.text if orcid_el is not None else None
                    authors.append(Author(name=name, affiliation=affil, orcid=orcid))

            # 年份
            pub_date = art.find(".//PubDate")
            year_text = pub_date.findtext("Year") if pub_date is not None else None
            year = int(year_text) if year_text and year_text.isdigit() else None

            # 期刊
            journal_el = art.find("Journal")
            journal = journal_el.findtext("Title") if journal_el is not None else None

            # PMID & DOI
            pmid_el = medline.find("PMID")
            pmid = pmid_el.text if pmid_el is not None else None

            doi = None
            for id_el in article.findall(".//ArticleId"):
                if id_el.get("IdType") == "doi":
                    doi = id_el.text
                    break

            # 关键词
            kws = [kw.text for kw in article.findall(".//Keyword") if kw.text]

            return PaperMetadata(
                title=title,
                authors=authors,
                year=year,
                journal=journal,
                doi=doi,
                abstract=abstract,
                keywords=kws,
                access_status=AccessStatus.UNKNOWN,
                raw_ids={"pubmed": pmid},
            )
        except Exception as e:
            logger.debug(f"[pubmed] 单篇解析失败: {e}")
            return None
