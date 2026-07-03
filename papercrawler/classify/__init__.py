"""
papercrawler.classify — 领域相关性判定与自动分类

三个子模块:
  - domain_filter   : 第一阶段粗筛 — 只看 title,用 must_have/should_have/exclude
  - semantic_filter : 第二阶段细筛 — 命中关键词计数,title+abstract+keywords 中命中 ≥ 阈值
  - categorizer     : 按用户自定义 categories 给论文打多标签

两阶段打分流程(CLI 中):
  search → DomainFilter.annotate(papers) → 粗筛 → enrich(补 abstract) →
  SemanticFilter.annotate(papers) → 双门限过滤 → Categorizer.annotate → CSV → download
"""
from papercrawler.classify.domain_filter import (
    DomainFilter,
    score_paper,
    annotate_interest_scores,
    filter_by_interest,
)
from papercrawler.classify.semantic_filter import SemanticFilter
from papercrawler.classify.categorizer import Categorizer, categorize_paper

__all__ = [
    # 第一阶段粗筛
    "DomainFilter",
    "score_paper",
    "annotate_interest_scores",
    "filter_by_interest",
    # 第二阶段细筛
    "SemanticFilter",
    # 自动分类
    "Categorizer",
    "categorize_paper",
]