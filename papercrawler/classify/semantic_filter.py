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
    _reverse_keyword_hit,
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

    反向关键词:细筛阶段也再查一次(用 title+abstract+keywords 更全的文本),
    命中任一即把 paper.is_reversed=True 并强制 semantic_score=0,
    确保两阶段都做硬剔除,不会被任何下游逻辑放行。
    """

    def __init__(self, interest: "InterestConfig"):
        self.interest = interest
        self.keywords = list(interest.semantic_keywords or [])
        self.reverse_keywords = list(interest.reverse_keywords or [])

    def score(self, paper: "PaperMetadata") -> int:
        """
        返回 0~len(keywords) 之间的命中数(整数)。

        副作用:若 paper 命中 reverse_keywords,会把 is_reversed=True / 强制返回 0。
        """
        # 先做反向关键词硬剔除(细筛阶段也要查 — 用更全的文本)
        # 注意:与粗筛一致,reverse 走严格 substring,不用 fuzzy(见 _reverse_keyword_hit)。
        if self.reverse_keywords:
            text = self._extract_text(paper)
            if text.strip():
                text_lower = _normalize(text)
                reversed_hits = [
                    kw for kw in self.reverse_keywords
                    if _reverse_keyword_hit(kw, text_lower)
                ]
                if reversed_hits:
                    paper.is_reversed = True
                    # 合并(粗筛可能已标记过)
                    existing = set(paper.reversed_keywords or [])
                    paper.reversed_keywords = sorted(existing | set(reversed_hits))
                    return 0

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