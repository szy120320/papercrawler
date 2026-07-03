"""
作者名称标准化、匹配评分与过滤工具

支持多种姓名格式：
  - "Smith, John A."  (Last, First Middle)
  - "John A. Smith"   (First Middle Last)
  - "J. Smith"        (Abbreviated first)
  - "Smith J"         (Last abbreviated)
  - Unicode 姓名（含变音符）

评分规则：
  - 姓氏精确匹配 → 基础分 0.70，再用全名相似度加权最高至 1.00
  - 姓氏相似 → 按字符相似度乘以 0.70
  - 返回所有作者中的最高分
"""

from __future__ import annotations

import difflib
import re
import unicodedata
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from papercrawler.models import Author, PaperMetadata


def normalize_author_name(name: str) -> str:
    """
    将作者姓名标准化为小写、去除标点的空格分隔字符串。

    Examples:
        "Smith, John A."  -> "smith john a"
        "John A. Smith"   -> "john a smith"
        "Müller, Hans"    -> "muller hans"
    """
    # NFKD 分解 Unicode，去除组合字符（如变音符号）
    name = unicodedata.normalize("NFKD", name)
    name = name.encode("ascii", "ignore").decode("ascii")
    name = name.lower()
    # 去除标点（保留字母、数字、空格）
    name = re.sub(r"[^\w\s]", " ", name)
    return " ".join(name.split())


def _extract_last_name(normalized: str) -> str:
    """
    从标准化姓名中提取姓氏 token。

    启发式规则：
      - 如果原始格式是 "Last, First"，逗号已被替换为空格，
        姓氏是 **第一个** token（因为姓在前）。
      - 如果格式是 "First Last"，姓氏是 **最后一个** token。
    由于两种格式均已标准化，无法 100% 判断，取 **最后一个** token
    作为主要姓氏标识（对 "First Last" 格式正确，"Last First" 时
    退化为名字，但全名相似度会补偿）。
    """
    tokens = normalized.split()
    if not tokens:
        return normalized
    # 过滤单字母缩写（如 "j"），选取长度 > 1 的 token 中最后一个
    long_tokens = [t for t in tokens if len(t) > 1]
    return long_tokens[-1] if long_tokens else tokens[-1]


def _extract_all_significant_tokens(normalized: str) -> set[str]:
    """返回长度 > 1 的所有 token 集合（忽略缩写）"""
    return {t for t in normalized.split() if len(t) > 1}


def author_match_score(query_author: str, authors: "list[Author]") -> float:
    """
    计算检索作者名与论文作者列表中任意一位的最高匹配分数。

    参数:
        query_author: 用户输入的作者名（可能含拼写变体）
        authors: 论文作者列表

    返回:
        0.0 ~ 1.0 的匹配分数：
          1.0  = 完美匹配（姓名完全一致）
          ≥0.8 = 高置信度匹配（姓氏精确 + 名字相似）
          ≥0.6 = 中等置信度（姓氏精确，但名字不同或缺失）
          <0.5 = 低置信度或不匹配
    """
    if not query_author or not authors:
        return 0.0

    q_norm = normalize_author_name(query_author)
    q_last = _extract_last_name(q_norm)
    q_tokens = _extract_all_significant_tokens(q_norm)

    best = 0.0

    for a in authors:
        if not a.name:
            continue
        a_norm = normalize_author_name(a.name)
        a_last = _extract_last_name(a_norm)
        a_tokens = _extract_all_significant_tokens(a_norm)

        # 计算姓氏相似度
        last_sim = difflib.SequenceMatcher(None, q_last, a_last).ratio()

        if last_sim >= 0.90:
            # 姓氏高度匹配：用全名相似度作为细粒度评分
            full_sim = difflib.SequenceMatcher(None, q_norm, a_norm).ratio()
            # 姓名 token 集合重叠程度
            token_overlap = (
                len(q_tokens & a_tokens) / max(len(q_tokens), len(a_tokens), 1)
            )
            score = 0.60 + 0.25 * full_sim + 0.15 * token_overlap
        elif last_sim >= 0.75:
            # 姓氏部分匹配（拼写变体、缩写等）
            score = last_sim * 0.70
        else:
            # 尝试直接全名相似度（兜底）
            score = difflib.SequenceMatcher(None, q_norm, a_norm).ratio() * 0.50

        best = max(best, score)

    return round(min(best, 1.0), 3)


def annotate_author_scores(
    papers: "list[PaperMetadata]",
    query_author: str,
) -> "list[PaperMetadata]":
    """
    为论文列表中每篇论文计算并设置 author_match_score，不过滤任何结果。

    参数:
        papers: 论文列表（原地修改 author_match_score 字段）
        query_author: 用户输入的作者名

    返回:
        原列表（已标注分数）
    """
    for paper in papers:
        paper.author_match_score = author_match_score(query_author, paper.authors)
    return papers


def filter_by_author_score(
    papers: "list[PaperMetadata]",
    query_author: str,
    threshold: float = 0.0,
) -> "list[PaperMetadata]":
    """
    先标注所有论文的作者匹配分数，再按阈值过滤。

    参数:
        papers: 论文列表
        query_author: 用户输入的作者名
        threshold: 最低匹配分数阈值（0.0 表示不过滤，仅标注）

    返回:
        过滤后的论文列表（按匹配分数降序排列）
    """
    annotate_author_scores(papers, query_author)

    if threshold > 0.0:
        before = len(papers)
        papers = [p for p in papers if (p.author_match_score or 0.0) >= threshold]
        filtered = before - len(papers)
        if filtered > 0:
            from loguru import logger
            logger.info(
                f"[作者过滤] 阈值={threshold:.2f}，过滤掉 {filtered} 篇低匹配论文，"
                f"保留 {len(papers)} 篇"
            )

    # 按匹配分数降序排列（分数相同时保持原顺序）
    papers.sort(key=lambda p: p.author_match_score or 0.0, reverse=True)
    return papers
