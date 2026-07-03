"""
去重工具

基于 DOI 或 标题+作者+年份 哈希值对论文列表进行去重，
并合并来自多个数据源的元数据字段。
"""

from __future__ import annotations

from papercrawler.models import PaperMetadata


# 各字段的数据源优先级（越靠前优先级越高）
_FIELD_PRIORITY: dict[str, list[str]] = {
    "title":           ["crossref", "semantic_scholar", "openalex", "arxiv", "pubmed", "core"],
    "abstract":        ["semantic_scholar", "pubmed", "openalex", "crossref", "arxiv", "core"],
    "authors":         ["crossref", "openalex", "semantic_scholar", "pubmed", "arxiv", "core"],
    "year":            ["crossref", "openalex", "semantic_scholar", "pubmed", "arxiv", "core"],
    "journal":         ["crossref", "openalex", "semantic_scholar", "pubmed"],
    "citations_count": ["semantic_scholar", "openalex"],
    "oa_url":          ["unpaywall", "semantic_scholar", "openalex", "core"],
    "keywords":        ["semantic_scholar", "pubmed", "openalex", "crossref"],
}


def _source_rank(source: str, field: str) -> int:
    priority = _FIELD_PRIORITY.get(field, [])
    try:
        return priority.index(source)
    except ValueError:
        return len(priority)  # 未知来源放最后


def merge_papers(papers: list[PaperMetadata]) -> PaperMetadata:
    """
    将同一论文来自多个数据源的记录合并为一条，
    按各字段的优先级选取最优值。
    """
    if len(papers) == 1:
        return papers[0]

    # 以第一条为基础
    base = papers[0].model_copy(deep=True)
    base.sources = []

    for paper in papers:
        for src in paper.sources:
            if src not in base.sources:
                base.sources.append(src)

    # 合并各字段
    for field in ["title", "abstract", "authors", "year", "journal",
                  "citations_count", "oa_url", "keywords", "volume",
                  "issue", "pages", "preprint_url"]:
        best_value = None
        best_rank = 999

        for paper in papers:
            value = getattr(paper, field, None)
            # 跳过空值
            if value is None or value == [] or value == "":
                continue
            # 用最高优先级的来源
            rank = min(
                (_source_rank(s, field) for s in paper.sources),
                default=999,
            )
            if rank < best_rank:
                best_rank = rank
                best_value = value

        if best_value is not None:
            setattr(base, field, best_value)

    # 合并 raw_ids
    for paper in papers:
        base.raw_ids.update(paper.raw_ids)

    return base


def deduplicate(papers: list[PaperMetadata]) -> list[PaperMetadata]:
    """
    对论文列表去重并合并元数据。

    主键：DOI（若存在）；否则使用 unique_id（基于标题+作者+年份哈希）。
    """
    groups: dict[str, list[PaperMetadata]] = {}

    for paper in papers:
        key = paper.unique_id
        if key not in groups:
            groups[key] = []
        groups[key].append(paper)

    return [merge_papers(group) for group in groups.values()]
