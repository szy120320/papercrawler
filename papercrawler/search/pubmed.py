"""
PubMed (NCBI E-utilities) 妫€绱㈤€傞厤鍣?API 鏂囨。: https://www.ncbi.nlm.nih.gov/books/NBK25500/
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
        # 鍒嗛〉鎶撳彇:PubMed 鐢?retstart= 缈婚〉(2026-07 鍔?
        #   1) esearch 涓€娆℃嬁瀹屾暣 PMID 鍒楄〃(鐢?query.page_size 鍐冲畾涓婇檺)
        #   2) efetch 鎸?PMID 鍒囩墖鎵归噺鍙栨憳瑕?retstart + retmax 缈婚〉
        # 闃插崱:max_pages=20 涓婇檺
        # 娉ㄦ剰:esearch 鏈韩浠嶉渶浼?retmax,浣嗛€氬父 鈮?瀹為檯鍛戒腑鏁板嵆鍙嬁鍒版墍鏈?PMID
        # ---------------------------------------------------------------
        term = self._build_term(query)
        esearch_params: dict = {
            "db": "pubmed",
            "term": term,
            # 2026-07-05 v1.3.0: retmax 直接用 query.page_size(每年 PMID 总量)
            # 如果 query.page_size=0 用一个大的默认
            "retmax": str(max(query.page_size, 10000)),
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

        # 2. efetch 鎸?PMID 鍒嗘壒鎷夋憳瑕?retstart + retmax 缈婚〉
        fetch_step = 100  # 鍗曟壒 PMID 涓婇檺
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
                logger.debug(f"[pubmed] efetch 澶辫触: {e}")
                return self._tag_source(results)

            if not xml_text:
                break

            try:
                page_results = self._parse_xml(xml_text)
            except ET.ParseError as e:
                logger.warning(f"[pubmed] XML 瑙ｆ瀽閿欒: {e}")
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

            # 鎽樿
            abstract_parts = art.findall(".//AbstractText")
            abstract = " ".join("".join(p.itertext()) for p in abstract_parts).strip() or None

            # 浣滆€?            authors: list[Author] = []
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

            # 骞翠唤
            pub_date = art.find(".//PubDate")
            year_text = pub_date.findtext("Year") if pub_date is not None else None
            year = int(year_text) if year_text and year_text.isdigit() else None

            # 鏈熷垔
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

            # 鍏抽敭璇?            kws = [kw.text for kw in article.findall(".//Keyword") if kw.text]

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
            # 鍗曠瘒瑙ｆ瀽澶辫触:瀛楁缂哄け鎴栫被鍨嬮敊
            logger.opt(exception=True).debug(f"[pubmed] 鍗曠瘒瑙ｆ瀽澶辫触: {e}")
            return None
