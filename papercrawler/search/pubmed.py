"""
PubMed (NCBI E-utilities) 检索适配器
API 文档: https://www.ncbi.nlm.nih.gov/books/NBK25500/
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

from loguru import logger

from papercrawler.models import Author, PaperMetadata, SearchQuery, AccessStatus
from papercrawler.search.base import BaseSearchAdapter, SourceError

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

        data = await self._get_json(_ESEARCH, params=params)
        if not data:
            return []

        pmids = data.get("esearchresult", {}).get("idlist", [])
        if not pmids:
            return []

        # 2. efetch 批量获取元数据 XML(走 _get_text,共享限速 + 重试)
        fetch_params: dict = {
            "db": "pubmed",
            "id": ",".join(pmids),
            "retmode": "xml",
            "rettype": "abstract",
        }
        if self.api_key:
            fetch_params["api_key"] = self.api_key

        try:
            xml_text = await self._get_text(_EFETCH, params=fetch_params)
        except SourceError as e:
            logger.debug(f"[pubmed] efetch 失败: {e}")
            return []

        if not xml_text:
            return []

        try:
            results = self._parse_xml(xml_text)
        except ET.ParseError as e:
            logger.warning(f"[pubmed] XML 解析错误: {e}")
            return []

        logger.debug(f"[pubmed] 找到 {len(results)} 篇论文")
        return self._tag_source(results)

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
        root = ET.fromstring(xml_text)
        papers: list[PaperMetadata] = []
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
            authors: list[Author] = []
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
        except (KeyError, AttributeError, TypeError, ValueError) as e:
            # 单篇解析失败:字段缺失或类型错
            logger.opt(exception=True).debug(f"[pubmed] 单篇解析失败: {e}")
            return None