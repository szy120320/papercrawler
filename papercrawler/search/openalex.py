"""
OpenAlex 妫€绱㈤€傞厤鍣?
API 鏂囨。: https://docs.openalex.org
"""

from __future__ import annotations

from loguru import logger

from papercrawler.models import Author, PaperMetadata, SearchQuery, AccessStatus
from papercrawler.search.base import BaseSearchAdapter

_BASE = "https://api.openalex.org"
_SELECT = (
    "id,title,authorships,publication_year,abstract_inverted_index,"
    "open_access,doi,primary_location,cited_by_count,keywords,"
    "biblio,type"
)


def _decode_abstract(inverted: dict | None) -> str | None:
    """OpenAlex 鐨勬憳瑕佷互鍊掓帓绱㈠紩褰㈠紡瀛樺偍锛岃繕鍘熶负鏂囨湰"""
    if not inverted:
        return None
    try:
        positions: dict[int, str] = {}
        for word, pos_list in inverted.items():
            for pos in pos_list:
                positions[pos] = word
        return " ".join(positions[i] for i in sorted(positions))
    except (KeyError, TypeError, ValueError):
        # 鍊掓帓绱㈠紩缁撴瀯寮傚父(闈?dict / 鍒楄〃绛?
        return None


class OpenAlexAdapter(BaseSearchAdapter):
    SOURCE_ID = "openalex"

    async def search(self, query: SearchQuery) -> list[PaperMetadata]:
        per_page = min(query.page_size, 200)   # OpenAlex API 上限 200

        base_params: dict = {
            "select": _SELECT,
            "per-page": per_page,
        }

        if query.doi:
            base_params["filter"] = f"doi:{query.doi}"
        elif query.title:
            base_params["filter"] = f"title.search:{query.title}"
            if query.author:
                base_params["filter"] += f",author.search:{query.author}"
        elif query.author:
            base_params["filter"] = f"author.search:{query.author}"
        else:
            # 鍏抽敭璇嶆绱細浣跨敤椤跺眰 search 鍙傛暟锛圤penAlex 鎺ㄨ崘鏂瑰紡锛屾悳绱㈡爣棰?鎽樿+鍏ㄦ枃锛?
            # 娉ㄦ剰锛歴earch= 鍙傛暟鍙笌 filter= 鍙傛暟骞跺瓨锛屼絾涓嶈兘鍦?filter 鍐呬娇鐢?default.search:
            base_params["search"] = query.build_text_query()

        if query.year_from or query.year_to:
            if query.year_from and query.year_to:
                year_filter = f"publication_year:{query.year_from}-{query.year_to}"
            elif query.year_from:
                year_filter = f"from_publication_date:{query.year_from}-01-01"
            else:
                year_filter = f"to_publication_date:{query.year_to}-12-31"

            if "filter" in base_params:
                # 宸叉湁 filter锛岀洿鎺ヨ拷鍔犲勾浠芥潯浠讹紙鐢ㄩ€楀彿鍒嗛殧琛ㄧず AND锛?
                base_params["filter"] = f"{base_params['filter']},{year_filter}"
            else:
                # 绾叧閿瘝 search 妯″紡锛歽ear 鍗曠嫭鍔犲叆 filter锛屼袱鑰呭彲浠ュ叡瀛?
                base_params["filter"] = year_filter

        if query.sort == "citations":
            base_params["sort"] = "cited_by_count:desc"
        elif query.sort == "date":
            base_params["sort"] = "publication_year:desc"

        # OpenAlex 鎺ㄨ崘鎻愪緵 mailto
        base_params["mailto"] = "user@example.com"

        # ---------------------------------------------------------------
        # 鍒嗛〉鎶撳彇:OpenAlex cursor-based pagination(2026-07 鍔?
        #   绗竴娆¤姹備笉甯?cursor
        #   鍝嶅簲 meta.next_cursor 鏄?opaque string;涓嬫璇锋眰鍔?cursor=...
        #   next_cursor 涓?null/缂哄け鏃惰〃绀哄凡鍒版湯灏?
        # 闃插崱:max_pages=100 涓婇檺(2026-07-05 鈫?from 50),榛樿 100 椤?脳 200/椤?= 20000 鏉′笂闄?
        # ---------------------------------------------------------------
        results: list[PaperMetadata] = []
        seen: set[str] = set()  # 璺ㄩ〉 DOI 鍘婚噸
        cursor: str | None = "*"   # 棣栨鐗规畩鍊?涓嶅甫 cursor;涔嬪悗鐢?next_cursor
        max_pages = 100

        for _page in range(max_pages):
            params = dict(base_params)
            if cursor and cursor != "*":
                params["cursor"] = cursor

            data = await self._get_json(f"{_BASE}/works", params=params)
            if not data:
                break

            page_items = data.get("results", [])
            if not page_items:
                break

            added = 0
            for item in page_items:
                paper = self._parse(item)
                if not paper:
                    continue
                # 璺ㄩ〉鍘婚噸
                key = (paper.doi or "").strip().lower() or (paper.title or "")
                if key and key in seen:
                    continue
                seen.add(key)
                results.append(paper)
                added += 1

            # OpenAlex 终止信号:next_cursor 消失 / 等于 "*"
            meta = data.get("meta") or {}
            cursor = meta.get("next_cursor")
            if not cursor:
                break

        logger.debug(f"[openalex] 找到 {len(results)} 篇论文")
        return self._tag_source(results)

    def _parse(self, item: dict) -> PaperMetadata | None:
        try:
            authors = []
            for ship in item.get("authorships", []):
                auth = ship.get("author", {})
                insts = ship.get("institutions", [])
                affil = insts[0].get("display_name") if insts else None
                authors.append(Author(name=auth.get("display_name", ""), affiliation=affil))

            oa = item.get("open_access", {}) or {}
            oa_url = oa.get("oa_url") or oa.get("best_oa_url")
            is_oa = oa.get("is_oa", False)

            doi_raw = item.get("doi") or ""
            doi = doi_raw.replace("https://doi.org/", "").strip() or None

            loc = item.get("primary_location", {}) or {}
            source = loc.get("source", {}) or {}
            journal = source.get("display_name")

            biblio = item.get("biblio", {}) or {}
            kws = [k.get("display_name", "") for k in (item.get("keywords") or [])]

            access = AccessStatus.UNKNOWN
            if is_oa and oa_url:
                if oa_url and "arxiv" in oa_url.lower():
                    access = AccessStatus.OPEN_ACCESS_PREPRINT
                else:
                    access = AccessStatus.OPEN_ACCESS_PDF

            first_page = biblio.get("first_page") or ""
            last_page = biblio.get("last_page") or ""
            pages = f"{first_page}-{last_page}".strip("-") or None

            return PaperMetadata(
                title=item.get("title") or item.get("display_name", "Unknown Title"),
                authors=authors,
                year=item.get("publication_year"),
                journal=journal,
                volume=biblio.get("volume"),
                issue=biblio.get("issue"),
                pages=pages,
                doi=doi,
                abstract=_decode_abstract(item.get("abstract_inverted_index")),
                citations_count=item.get("cited_by_count"),
                keywords=kws,
                access_status=access,
                oa_url=oa_url,
                raw_ids={"openalex": item.get("id")},
            )
        except (KeyError, AttributeError, TypeError, ValueError) as e:
            # 鍗曠瘒瑙ｆ瀽澶辫触:瀛楁缂哄け / 绫诲瀷閿?
            logger.opt(exception=True).debug(f"[openalex] 瑙ｆ瀽澶辫触: {e}")
            return None
