"""
domain_filter.py — 领域相关性打分

根据用户在 [interest] 配置的关键词集合,对论文 title+abstract 做整体相关性打分。

打分规则(可解释、纯规则):
    score = 0.0
      + 0.6  if any must_have 命中
      + 0.1  per should_have 命中 (cap at 0.3)
      - 0.5  if any exclude 命中
    范围 [0.0, 1.0]

模糊匹配:
    使用 difflib.SequenceMatcher 计算关键词与文本窗口的字符相似度,
    >= fuzzy_threshold 即视为命中。这样 "reaxff" / "ReaxFF" / "Reax FF" 都能匹配。

注意:本模块不依赖任何外部 NLP 库,纯字符串处理,速度极快。
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from loguru import logger

if TYPE_CHECKING:
    from papercrawler.config import InterestConfig
    from papercrawler.models import PaperMetadata


# ---------------------------------------------------------------------------
# 文本预处理
# ---------------------------------------------------------------------------

_word_re = re.compile(r"\b\w+\b", re.UNICODE)


def _normalize(text: str) -> str:
    """
    标准化字符串:小写、合并空白。

    注意:不去除标点和变音符 —— 因为关键词可能是缩写 (e.g. "MD", "DFT")
    """
    if not text:
        return ""
    return " ".join(text.lower().split())


def _tokenize(text: str) -> list[str]:
    """提取所有 word token(用于模糊匹配窗口)"""
    return _word_re.findall(_normalize(text))


# ---------------------------------------------------------------------------
# 关键词匹配
# ---------------------------------------------------------------------------

def _reverse_keyword_hit(
    keyword: str,
    text_lower: str,
) -> bool:
    """
    反向关键词命中:仅做大小写不敏感的子串匹配,不用 fuzzy。

    设计依据:
      反向词是黑名单 — 误杀一篇相关论文的代价比放过一篇不相关论文高。
    fuzzy 在多 token 关键词上会把 "aqueous electrolyte" 的 longest token
    "electrolyte" 与 "solid-state electrolyte" 的 "electrolyte" 视为命中,
    导致几乎所有电解质方向论文被误杀,这是 2026-07 实测发现的 bug。
    所以 reverse 走严格 substring,完全跳过 fuzzy。
    """
    kw = keyword.lower().strip()
    return bool(kw) and (kw in text_lower)


def _exact_or_fuzzy_hit(
    keyword: str,
    text_lower: str,
    text_tokens: list[str],
    fuzzy_threshold: float,
) -> bool:
    """
    判断 keyword 是否在 text 中命中。

    匹配规则:
      1. 完全包含(子串匹配,大小写不敏感)
      2. 模糊匹配:对每个 token 计算字符相似度,>= threshold 即视为命中
         (用 token 级别而非全字符串,避免长摘要误判)
    """
    kw = keyword.lower().strip()
    if not kw:
        return False

    # 1. 子串匹配(快路径)
    if kw in text_lower:
        return True

    # 2. 模糊匹配(token 级别)
    if fuzzy_threshold <= 0.0:
        return False

    # 关键词本身可能含空格(如 "solid electrolyte"),
    # 这种情况取最长 token 参与模糊匹配即可
    kw_main_token = max(kw.split(), key=len) if " " in kw else kw
    if len(kw_main_token) < 4:
        # 太短的词不参与模糊匹配(避免误判)
        return False

    from difflib import SequenceMatcher
    for tok in text_tokens:
        if abs(len(tok) - len(kw_main_token)) > max(2, len(kw_main_token) // 3):
            continue
        sim = SequenceMatcher(None, kw_main_token, tok).ratio()
        if sim >= fuzzy_threshold:
            return True
    return False


def _count_hits(
    keywords: list[str],
    text_lower: str,
    text_tokens: list[str],
    fuzzy_threshold: float,
) -> int:
    """统计 keywords 中命中 text 的数量"""
    if not keywords:
        return 0
    n = 0
    for kw in keywords:
        if _exact_or_fuzzy_hit(kw, text_lower, text_tokens, fuzzy_threshold):
            n += 1
    return n


# ---------------------------------------------------------------------------
# 打分
# ---------------------------------------------------------------------------

# 各部分权重(可在 InterestConfig 中扩展,这里给默认值)
_BASE_SCORE = 0.6       # 命中 must_have → 0.6
_SHOULD_BONUS = 0.1     # 每个 should_have 命中 → +0.1
_SHOULD_CAP = 0.3       # should_have 总加分封顶 0.3
_EXCLUDE_PENALTY = 0.5  # 命中 exclude → -0.5
_FLOOR = 0.0            # 分数下限
_CEIL = 1.0             # 分数上限


def _extract_text(paper: "PaperMetadata") -> str:
    """拼接 title + abstract + keywords,作为打分文本"""
    parts = [paper.title or ""]
    if paper.abstract:
        parts.append(paper.abstract)
    if paper.keywords:
        parts.append(" ".join(paper.keywords))
    return " ".join(parts)


def score_paper(
    paper: "PaperMetadata",
    interest: "InterestConfig",
) -> float:
    """
    计算单篇论文对用户兴趣的相关性分数(0.0 ~ 1.0)。

    Args:
        paper: 论文元数据
        interest: [interest] 配置

    Returns:
        相关性分数,0.0 表示不相关,1.0 表示强相关

    副作用:
        若命中 reverse_keywords,会同时把 paper.is_reversed=True 并记录
        paper.reversed_keywords(命中的反向关键词列表,供审计)。
    """
    text = _extract_text(paper)
    if not text.strip():
        return 0.0

    text_lower = _normalize(text)
    text_tokens = _tokenize(text)
    threshold = interest.fuzzy_threshold

    # 0. reverse_keywords:命中即直接 0 分(并标记 is_reversed)
    #    在 must_have 检查之前,确保硬剔除优先级最高。
    #    注意:反向词只走 substring 严格匹配(见 _reverse_keyword_hit docstring),
    #    不用 interest.fuzzy_threshold — 否则会因 longest-token fuzzy 把
    #    "aqueous electrolyte" / "zinc ion battery" 等几乎所有相关论文误杀。
    if interest.reverse_keywords:
        reversed_hits = [
            kw for kw in interest.reverse_keywords
            if _reverse_keyword_hit(kw, text_lower)
        ]
        if reversed_hits:
            paper.is_reversed = True
            paper.reversed_keywords = reversed_hits
            return 0.0
        # 命中后直接返回 — 不参与后面的粗筛打分
        paper.is_reversed = False
        paper.reversed_keywords = []
    else:
        paper.is_reversed = False
        paper.reversed_keywords = []

    # 1. must_have:命中 → 基础分
    score = 0.0
    if _count_hits(interest.must_have, text_lower, text_tokens, threshold) > 0:
        score += _BASE_SCORE

    # 2. should_have:每个 +0.1,封顶 0.3
    if interest.should_have:
        hits = _count_hits(interest.should_have, text_lower, text_tokens, threshold)
        score += min(hits * _SHOULD_BONUS, _SHOULD_CAP)

    # 3. exclude:命中 → 直接 -0.5(可降至 0 以下,但最终截到 0)
    if _count_hits(interest.exclude, text_lower, text_tokens, threshold) > 0:
        score -= _EXCLUDE_PENALTY

    return round(max(_FLOOR, min(_CEIL, score)), 3)


# ---------------------------------------------------------------------------
# 批量接口
# ---------------------------------------------------------------------------

class DomainFilter:
    """
    领域相关性过滤器 — 封装批量打分与阈值过滤逻辑。
    """

    def __init__(self, interest: "InterestConfig"):
        self.interest = interest

    def score(self, paper: "PaperMetadata") -> float:
        return score_paper(paper, self.interest)

    def annotate(
        self,
        papers: list["PaperMetadata"],
    ) -> list["PaperMetadata"]:
        """
        为每篇论文计算粗筛分数,写入 paper.coarse_score(原地修改)。

        两阶段打分设计:DomainFilter 是第一阶段粗筛,分数写到 coarse_score,
        第二阶段 SemanticFilter 把分数写到 semantic_score,
        最终由 CLI / caller 合并成 interest_score = 0.6*coarse + 0.4*semantic。
        """
        for p in papers:
            p.coarse_score = self.score(p)
        return papers

    def filter(
        self,
        papers: list["PaperMetadata"],
        threshold: float = 0.0,
    ) -> list["PaperMetadata"]:
        """
        标注分数后,按阈值过滤;按分数降序排列。

        threshold=0.0 表示不过滤,仅标注和排序。
        """
        self.annotate(papers)

        if threshold > 0.0:
            before = len(papers)
            papers = [p for p in papers if (p.interest_score or 0.0) >= threshold]
            filtered_out = before - len(papers)
            if filtered_out:
                logger.info(
                    f"[interest] 阈值={threshold:.2f},过滤 {filtered_out} 篇低相关论文,"
                    f"保留 {len(papers)} 篇"
                )

        papers.sort(key=lambda p: p.interest_score or 0.0, reverse=True)
        return papers


# 函数式 API(向后兼容 author_filter 的风格)
def annotate_interest_scores(
    papers: list["PaperMetadata"],
    interest: "InterestConfig",
) -> list["PaperMetadata"]:
    """为每篇论文计算粗筛分数,写入 paper.coarse_score(不覆盖 interest_score)

    历史兼容:旧版本 annotate_interest_scores 把粗筛分数写到 interest_score。
    新版两阶段打分:粗筛写 coarse_score,细筛写 semantic_score,合并到 interest_score。
    """
    df = DomainFilter(interest)
    for p in papers:
        p.coarse_score = df.score(p)
    return papers


def filter_by_interest(
    papers: list["PaperMetadata"],
    interest: "InterestConfig",
    threshold: float = 0.0,
) -> list["PaperMetadata"]:
    return DomainFilter(interest).filter(papers, threshold=threshold)
