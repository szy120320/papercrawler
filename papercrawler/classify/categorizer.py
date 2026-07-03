"""
categorizer.py — 多标签分类器

根据用户在 [interest.categories] 中定义的分类规则,
为每篇论文打 0~N 个分类标签。

设计要点:
  - 一篇论文可同时属于多个分类(多标签)
  - 每类独立计算"命中关键词数",达到 min_hits 阈值即视为入类
  - 分类标签存储在 paper.categories 字段
  - 与 domain_filter 配合:domain 给总评,categorize 给细分
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from loguru import logger

# 复用 domain_filter 的匹配函数(避免重复实现)
from papercrawler.classify.domain_filter import (
    _count_hits,
    _normalize,
    _tokenize,
)

if TYPE_CHECKING:
    from papercrawler.config import InterestConfig, InterestCategory
    from papercrawler.models import PaperMetadata


def categorize_paper(
    paper: "PaperMetadata",
    interest: "InterestConfig",
) -> list[str]:
    """
    对单篇论文打分类标签。

    Returns:
        命中的分类名列表(可能为空)
    """
    if not interest.categories:
        return []

    text = " ".join(filter(None, [
        paper.title or "",
        paper.abstract or "",
        " ".join(paper.keywords or []),
    ]))
    if not text.strip():
        return []

    text_lower = _normalize(text)
    text_tokens = _tokenize(text)
    threshold = interest.fuzzy_threshold

    matched: list[str] = []
    for cat in interest.categories:
        if not cat.keywords:
            continue
        hits = _count_hits(cat.keywords, text_lower, text_tokens, threshold)
        if hits >= cat.min_hits:
            matched.append(cat.name)

    return matched


class Categorizer:
    """分类器:封装批量打标签逻辑"""

    def __init__(self, interest: "InterestConfig"):
        self.interest = interest

    def categorize(self, paper: "PaperMetadata") -> list[str]:
        return categorize_paper(paper, self.interest)

    def annotate(self, papers: list["PaperMetadata"]) -> list["PaperMetadata"]:
        """
        批量打标签:写入 paper.categories 字段(原地修改)。

        即使配置没有 categories,也不会破坏数据(只是空列表)。
        """
        if not self.interest.categories:
            logger.debug("[categorize] 未配置 categories,跳过分类")
            for p in papers:
                p.categories = []
            return papers

        for p in papers:
            p.categories = self.categorize(p)

        n_categorized = sum(1 for p in papers if p.categories)
        logger.info(
            f"[categorize] 共 {n_categorized}/{len(papers)} 篇被分类,"
            f"分类数={len(self.interest.categories)}"
        )
        return papers
