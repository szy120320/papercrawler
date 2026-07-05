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
        # ---------------------------------------------------------------
        # 分页抓取:PubMed 用 retstart= 翻页(2026-07 加)
        #   1) esearch 一次拿完整 PMID 列表(用 query.max_results 决定上限)
        #   2) efetch 按 PMID 切片批量取摘要,retstart + retmax 翻页
        # 防卡:max_pages=20 上限
        # 注意:esearch 本身仍需传 retmax,但通常 ≥ 实际命中数即可拿到所有 PMID
        # ---------------------------------------------------------------
        term = self._build_term(query)
        esearch_params: dict = {
            "db": "pubmed",
            "term": term,
            "retmax": str(query.max_results),  # 拿全 PMID,不再受 100 限顶
            "retmode": "json",
        }
        if self.api_key:
            esearch_params["api_key"] = self.api_key
        if query.year_from or query.year_to:
            frm = query.year_from or 1800
            to = query.year_to or 2100
            esearch_params["datetype"] = "pdat"
            esearch_params["mindate"] = str(frm)
            esearch_params["maxdate"] = str(to)

        data = await self._get_json(_ESEARCH, params=esearch_params)
        if not data:
            return []

        pmids = data.get("esearchresult", {}).get("idlist", [])
        if not pmids:
            return []

        # 2. efetch 按 PMID 分批拉摘要,retstart + retmax 翻页
        fetch_step = 100  # 单批 PMID 上限
        results: list[PaperMetadata] = []
        seen: set[str] = set()
        retstart = 0
        max_pages = 20

        for _page in range(max_pages):
            batch = pmids[retstart : retstart + fetch_step]
            if not batch:
                break

            fetch_params: dict = {
                "db": "pubmed",
                "id": ",".join(batch),
                "retmode": "xml",
                "rettype": "abstract",
            }
            if self.api_key:
                fetch_params["api_key"] = self.api_key

            try:
                xml_text = await self._get_text(_EFETCH, params=fetch_params)
            except SourceError as e:
                logger.debug(f"[pubmed] efetch 失败: {e}")
                return self._tag_source(results)

            if not xml_text:
                break

            try:
                page_results = self._parse_xml(xml_text)
            except ET.ParseError as e:
                logger.warning(f"[pubmed] XML 解析错误: {e}")
                break

            for paper in page_results:
                key = ((paper.raw_ids or {}).get("pubmed") or "") or (paper.title or "")
                if key and key in seen:
                    continue
                seen.add(key)
                results.append(paper)

            retstart += len(batch)
            if len(batch) < fetch_step:
                break
            if len(results) >= query.max_results:
                break

        logger.debug(f"[pubmed] 找到 {len(results)} 篇论文")
        return self._tag_source(results[: query.max_results])

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