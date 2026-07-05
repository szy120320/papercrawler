"""
ChemRxiv 妫€绱㈤€傞厤鍣?閫氳繃 Crossref 鍏滃簳)

鍘嗗彶鑳屾櫙(2026-07-05):
  - ChemRxiv 瀹樻柟 OpenEngage API 鑷?2025 骞磋捣琚?Cloudflare Bot Challenge 鎷︽埅
  - 鐩存帴鎵?chemrxiv.org/.../public-api/v1/items 浼氳繑鍥?403 "Just a moment..."
  - 瑙ｅ喅鏂规:鍊熷姪 ChemRxiv 鐨?DOI 鍓嶇紑 10.26434,閫氳繃 Crossref Works API 妫€绱?  - 鍙傝€?paperscraper.get_dumps.utils.chemrxiv.CrossrefChemrxivAPI 鐨勮璁?  - 鏈」鐩笉渚濊禆 paperscraper 鍖?鍙彇鍏舵€濊矾

闄愬埗(2026-07-05):
  - Crossref 涓嶇储寮?ChemRxiv 鐨?abstract銆乧ategories銆乽sage metrics
  - abstract / categories 瀛楁姘歌繙鏄┖
  - 濡傛灉璁烘枃鏍囬涓嶅惈 SSE 鍏抽敭璇?浼氳绮楃瓫鍓旈櫎(鍙兘闈?reverse_keywords 婕忕綉)
  - PDF 涓嬭浇:Crossref item 鐨?`resource.primary.URL` 瀛楁,浼氭寚鍚?chemrxiv.org 涓婄殑璇︽儏椤?    (涓嬭浇 PDF 浠嶅彲鑳借 Cloudflare 鎷?鈥?璧?OA 鍏滃簳鎴?Sci-Hub)
"""

from __future__ import annotations

import re
from typing import Optional

from loguru import logger

from papercrawler.models import Author, PaperMetadata, SearchQuery, AccessStatus
from papercrawler.search.base import BaseSearchAdapter

_BASE = "https://api.crossref.org/works"
_CHEMRXIV_DOI_PREFIX = "10.26434"   # ChemRxiv 涓撳睘 DOI 鍓嶇紑
_PAGE_SIZE = 100                     # Crossref 单页最大
_MAX_PAGES = 50                       # 上限:50 × 100 = 5,000 条(2026-07-05 提升)


class ChemRxivViaCrossrefAdapter(BaseSearchAdapter):
    """閫氳繃 Crossref Works API 妫€绱?ChemRxiv 棰勫嵃鏈?
    Crossref filter 缁勫悎:
      - prefix:10.26434         # ChemRxiv DOI 鍓嶇紑
      - type:posted-content      # 棰勫嵃鏈被鍨?      - from-posted-date / until-posted-date  # 骞翠唤鑼冨洿

    鍏抽敭璇?`query.bibliographic=`(鍏ㄦ枃 + 鏍囬 + 浣滆€?鐢ㄧ┖鏍煎垎闅?    """

    SOURCE_ID = "chemrxiv_via_crossref"

    async def search(self, query: SearchQuery) -> list[PaperMetadata]:
        if query.doi:
            return await self._by_doi(query.doi)

        if not query.query:
            logger.warning("[chemrxiv_via_crossref] 娌℃湁 query 鍏抽敭璇?璺宠繃")
            return []

        # 鏋勯€?filter
        filters = [
            f"prefix:{_CHEMRXIV_DOI_PREFIX}",
            "type:posted-content",
        ]
        if query.year_from or query.year_to:
            frm = query.year_from or 1900
            to = query.year_to or 2100
            filters.append(f"from-posted-date:{frm}")
            filters.append(f"until-posted-date:{to}")

        # Crossref 参数
        params: dict = {
            "query.bibliographic": query.query,
            "rows": _PAGE_SIZE,
            "select": (
                "DOI,title,author,posted,issued,container-title,"
                "link,relation,license,URL,resource"
            ),
            "filter": ",".join(filters),
        }
        # 绀艰矊鎬?mailto
        params["mailto"] = "user@example.com"

        results: list[PaperMetadata] = []
        seen: set[str] = set()
        offset = 0
        max_pages = _MAX_PAGES
        total_reported: int | None = None

        for _page in range(max_pages):
            page_params = dict(params)
            page_params["offset"] = offset
            page_params["rows"] = _PAGE_SIZE

            data = await self._get_json(f"{_BASE}", params=page_params)
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
                # 用 DOI 去重(同源数据,DOI 唯一)
                key = (paper.doi or "").strip().lower() or (paper.title or "")
                if key and key in seen:
                    continue
                seen.add(key)
                results.append(paper)

            offset += len(items)
            # Crossref 终止信号
            if len(items) < page_params["rows"]:
                break
            if total_reported is not None and offset >= total_reported:
                break

        logger.debug(f"[chemrxiv_via_crossref] 找到 {len(results)} 篇论文")
        return self._tag_source(results)

    async def _by_doi(self, doi: str) -> list[PaperMetadata]:
        """閫氳繃 DOI 绮剧‘鏌ヤ竴绡?鍙敮鎸?10.26434 鍓嶇紑鐨?ChemRxiv DOI)"""
        if not doi.lower().startswith(_CHEMRXIV_DOI_PREFIX):
            logger.debug(f"[chemrxiv_via_crossref] DOI {doi} 涓嶅睘浜?ChemRxiv 鍓嶇紑 {_CHEMRXIV_DOI_PREFIX}")
            return []
        data = await self._get_json(f"{_BASE}/{doi}", params={"mailto": "user@example.com"})
        if not data:
            return []
        item = (data.get("message") or {})
        paper = self._parse(item)
        return self._tag_source([paper]) if paper else []

    def _parse(self, item: dict) -> Optional[PaperMetadata]:
        try:
            # 鏍囬
            titles = item.get("title", [])
            title = titles[0] if titles else "Unknown Title"

            # 浣滆€?            authors = []
            for a in item.get("author", []) or []:
                given = (a.get("given") or "").strip()
                family = (a.get("family") or "").strip()
                name = f"{given} {family}".strip() or a.get("name", "")
                if not name:
                    continue
                affils = a.get("affiliation", [])
                affil = affils[0].get("name") if affils else None
                orcid = (a.get("ORCID") or "").replace("http://orcid.org/", "").replace("https://orcid.org/", "") or None
                authors.append(Author(name=name, affiliation=affil, orcid=orcid))

            # DOI
            doi = item.get("DOI") or None

            # 骞翠唤(浼樺厛 posted,fallback issued)
            year = None
            for date_field in ("posted", "issued"):
                date_parts = (item.get(date_field) or {}).get("date-parts") or []
                if date_parts and date_parts[0]:
                    year = date_parts[0][0]
                    break

            # 鏈熷垔(瀵?chemrxiv 鏉ヨ = "chemRxiv")
            containers = item.get("container-title", [])
            journal = containers[0] if containers else "chemRxiv"

            # abstract 鈥?Crossref 涓嶇储寮?chemrxiv 鎽樿,鍥哄畾涓虹┖
            # 鐢?None 琛ㄧず"鏄庣‘娌℃湁",涓嬫父浼氱敤 0 鍒嗗鐞?            abstract = None

            # 璧勬簮閾炬帴(鎸囧悜 chemrxiv.org 璇︽儏椤垫垨 PDF)
            resource = item.get("resource") or {}
            primary = resource.get("primary") or {}
            oa_url = primary.get("URL") or item.get("URL") or None

            # 鍏宠仈:宸插彂琛ㄧ増鐨?DOI
            rel = item.get("relation") or {}
            published_doi = None
            published_url = None
            is_preprint_of = rel.get("is-preprint-of") or []
            if is_preprint_of:
                candidate = is_preprint_of[0].get("id")
                if candidate:
                    published_doi = candidate
                    published_url = f"https://doi.org/{candidate}"

            # license
            licenses = item.get("license") or []
            license_url = licenses[0].get("URL") if licenses else None

            # 鏋勫缓璇︽儏椤?URL(chemrxiv.org 涔犳儻)
            url = (
                f"https://chemrxiv.org/engage/chemrxiv/article-details/{doi}"
                if doi
                else (item.get("URL") or None)
            )

            return PaperMetadata(
                title=title,
                authors=authors,
                year=year,
                journal=journal,
                doi=doi,
                url=url,
                abstract=abstract,                  # Crossref 涓嶆彁渚?                citations_count=None,               # Crossref 涓嶆彁渚?                keywords=[],                        # Crossref 涓嶆彁渚?categories
                # access_status 鐢变笅娓?AccessChecker 鍐冲畾
                # (chemrxiv 涓婄殑 PDF 澶ф鐜囪 CF 鎷?澶ф鐜?METADATA_ONLY)
                access_status=AccessStatus.UNKNOWN,
                oa_url=oa_url,                      # chemrxiv 璇︽儏椤?URL(鍙兘 CF 鎷?
                preprint_url=url,
                raw_ids={
                    "chemrxiv_via_crossref": doi,
                    "doi": doi,
                    "published_doi": published_doi,
                },
            )
        except (KeyError, AttributeError, TypeError, ValueError) as e:
            logger.opt(exception=True).debug(f"[chemrxiv_via_crossref] 瑙ｆ瀽澶辫触: {e}")
            return None
