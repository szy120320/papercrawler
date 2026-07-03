"""
papercrawler.classify — 领域相关性判定与自动分类

两个子模块:
  - domain_filter  : 论文 vs 用户兴趣的整体相关性打分
  - categorizer    : 按用户自定义 categories 给论文打多标签
"""

from papercrawler.classify.domain_filter import (
    DomainFilter,
    score_paper,
    annotate_interest_scores,
    filter_by_interest,
)
from papercrawler.classify.categorizer import Categorizer, categorize_paper

__all__ = [
    "DomainFilter",
    "score_paper",
    "annotate_interest_scores",
    "filter_by_interest",
    "Categorizer",
    "categorize_paper",
]
