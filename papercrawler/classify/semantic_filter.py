"""
semantic_filter.py — 第二阶段细筛(纯关键词计数)

打分逻辑:
    semantic_score = 命中关键词数(0~N)
    阈值过滤: semantic_score >= semantic_min_matches(默认 3)

判定:
    - 从 paper.title + paper.abstract + paper.keywords 里数 semantic_keywords 命中几个
    - 模糊匹配:大小写不敏感 + difflib 字符相似度 ≥ fuzzy_threshold
    - 不区分 must_have/should_have — 所有关键词权重相同,数 ≥ 阈值即可

降级处理:
    - 没配 semantic_keywords 时: score=0,过滤会剔除(用户必须配才能用精筛)
    - abstract 缺失时:只用 title + keywords 算(不影响统计逻辑)
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from loguru import logger

# 复用第一阶段的字符串匹配工具
from papercrawler.classify.domain_filter import (
    _exact_or_fuzzy_hit,
    _normalize,
    _tokenize,
)

if TYPE_CHECKING:
    from papercrawler.config import InterestConfig
    from papercrawler.models import PaperMetadata


class SemanticFilter:
    """
    第二阶段细筛 — 纯关键词命中计数。

    命中数 ≥ InterestConfig.semantic_min_matches(默认 3)才保留。
    与第一阶段粗筛(DomainFilter)独立判断,双门限都必须满足。
    """

    def __init__(self, interest: "InterestConfig"):
        self.interest = interest
        self.keywords = list(interest.semantic_keywords or [])

    def score(self, paper: "PaperMetadata") -> int:
        """返回 0~len(keywords) 之间的命中数(整数)"""
        if not self.keywords:
            # 没配任何关键词 — 返回 0(配 threshold=0 可放行,默认 threshold=3 会剔除)
            return 0

        text = self._extract_text(paper)
        if not text.strip():
            return 0

        text_lower = _normalize(text)
        text_tokens = _tokenize(text)
        threshold = self.interest.fuzzy_threshold

        return sum(
            1 for kw in self.keywords
            if _exact_or_fuzzy_hit(kw, text_lower, text_tokens, threshold)
        )

    def annotate(self, papers: list["PaperMetadata"]) -> list["PaperMetadata"]:
        """原地写入 paper.semantic_score(整数,命中数)"""
        for p in papers:
            p.semantic_score = self.score(p)
        return papers

    @staticmethod
    def _extract_text(paper: "PaperMetadata") -> str:
        """拼接 title + abstract + keywords"""
        parts = [paper.title or ""]
        if paper.abstract:
            parts.append(paper.abstract)
        if paper.keywords:
            parts.append(" ".join(paper.keywords))
        return " ".join(parts)