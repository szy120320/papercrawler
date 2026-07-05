"""
OpenAlex 检索适配器
API 文档: https://docs.openalex.org
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
    """OpenAlex 的摘要以倒排索引形式存储，还原为文本"""
    if not inverted:
        return None
    try:
        positions: dict[int, str] = {}
        for word, pos_list in inverted.items():
            for pos in pos_list:
                positions[pos] = word
        return " ".join(positions[i] for i in sorted(positions))
    except (KeyError, TypeError, ValueError):
        # 倒排索引结构异常(非 dict / 列表等)
        return None


class OpenAlexAdapter(BaseSearchAdapter):
    SOURCE_ID = "openalex"

    async def search(self, query: SearchQuery) -> list[PaperMetadata]:
        per_page = min(query.max_results, 200)

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
            # 关键词检索：使用顶层 search 参数（OpenAlex 推荐方式，搜索标题+摘要+全文）
            # 注意：search= 参数可与 filter= 参数并存，但不能在 filter 内使用 default.search:
            base_params["search"] = query.build_text_query()

        if query.year_from or query.year_to:
            if query.year_from and query.year_to:
                year_filter = f"publication_year:{query.year_from}-{query.year_to}"
            elif query.year_from:
                year_filter = f"from_publication_date:{query.year_from}-01-01"
            else:
                year_filter = f"to_publication_date:{query.year_to}-12-31"

            if "filter" in base_params:
                # 已有 filter，直接追加年份条件（用逗号分隔表示 AND）
                base_params["filter"] = f"{base_params['filter']},{year_filter}"
            else:
                # 纯关键词 search 模式：year 单独加入 filter，两者可以共存
                base_params["filter"] = year_filter

        if query.sort == "citations":
            base_params["sort"] = "cited_by_count:desc"
        elif query.sort == "date":
            base_params["sort"] = "publication_year:desc"

        # OpenAlex 推荐提供 mailto
        base_params["mailto"] = "user@example.com"

        # ---------------------------------------------------------------
        # 分页抓取:OpenAlex cursor-based pagination(2026-07 加)
        #   第一次请求不带 cursor
        #   响应 meta.next_cursor 是 opaque string;下次请求加 cursor=...
        #   next_cursor 为 null/缺失时表示已到末尾
        # 防卡:max_pages=100 上限(2026-07-05 ↑ from 50),默认 100 页 × 200/页 = 20000 条上限
        # ---------------------------------------------------------------
        results: list[PaperMetadata] = []
        seen: set[str] = set()  # 跨页 DOI 去重
        cursor: str | None = "*"   # 首次特殊值:不带 cursor;之后用 next_cursor
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
                # 跨页去重
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
            if len(results) >= query.max_results:
                break

        logger.debug(f"[openalex] 找到 {len(results)} 篇论文")
        return self._tag_source(results[: query.max_results])

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
            # 单篇解析失败:字段缺失 / 类型错
            logger.opt(exception=True).debug(f"[openalex] 解析失败: {e}")
            return None
