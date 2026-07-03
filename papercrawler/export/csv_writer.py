"""
csv_writer.py — 论文列表 CSV 导出

输出字段(列顺序固定):
    title            论文标题
    doi              DOI
    year             发表年份
    journal          期刊/来源
    authors          作者列表(; 分隔)
    categories       分类标签(; 分隔,可能为空)
    interest_score   领域相关性分数(0~1,可能为空)
    author_score     作者匹配分数(0~1,可能为空)
    citations        引用数
    access_status    OA 状态字符串
    oa_url           OA 链接
    sources          命中数据源(; 分隔)
    downloaded       是否已下载(true/false)
    paper_dir        已下载时的论文目录名(相对 output_dir)

使用:
    writer = CSVWriter()
    writer.write(papers, "matched_papers.csv")
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Iterable

from loguru import logger

from papercrawler.models import PaperMetadata


# 固定列顺序(便于下游脚本/Excel 模板)
COLUMNS: list[str] = [
    "title",
    "doi",
    "year",
    "journal",
    "authors",
    "categories",
    "interest_score",
    "author_score",
    "citations",
    "access_status",
    "oa_url",
    "sources",
    "downloaded",
    "paper_dir",
]


def _paper_to_row(
    paper: PaperMetadata,
    downloaded: bool = False,
    paper_dir: str = "",
) -> dict:
    """将单篇论文转成 CSV 行字典"""
    return {
        "title":          paper.title or "",
        "doi":            paper.doi or "",
        "year":           paper.year if paper.year is not None else "",
        "journal":        paper.journal or "",
        "authors":        "; ".join(a.name for a in paper.authors),
        "categories":     "; ".join(paper.categories) if paper.categories else "",
        "interest_score": paper.interest_score if paper.interest_score is not None else "",
        "author_score":   paper.author_match_score if paper.author_match_score is not None else "",
        "citations":      paper.citations_count if paper.citations_count is not None else "",
        "access_status":  paper.access_status.value,
        "oa_url":         paper.oa_url or "",
        "sources":        "; ".join(paper.sources),
        "downloaded":     "true" if downloaded else "false",
        "paper_dir":      paper_dir,
    }


class CSVWriter:
    """CSV 写入器"""

    def __init__(self, columns: list[str] | None = None):
        self.columns = columns or COLUMNS

    def write(
        self,
        papers: Iterable[PaperMetadata],
        path: str | Path,
        downloaded_lookup: dict[str, bool] | None = None,
        paper_dir_lookup: dict[str, str] | None = None,
    ) -> int:
        """
        将论文列表写入 CSV。

        Args:
            papers: 论文列表
            path: 输出 CSV 路径
            downloaded_lookup: {unique_id: bool},可选,用于标记已下载
            paper_dir_lookup: {unique_id: dirname},可选,记录下载目录

        Returns:
            实际写入的行数
        """
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)

        downloaded_lookup = downloaded_lookup or {}
        paper_dir_lookup = paper_dir_lookup or {}

        rows = []
        for p in papers:
            uid = p.unique_id
            rows.append(_paper_to_row(
                p,
                downloaded=downloaded_lookup.get(uid, False),
                paper_dir=paper_dir_lookup.get(uid, ""),
            ))

        # 写文件:UTF-8 with BOM(Excel 友好)
        with open(path, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=self.columns, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)

        logger.info(f"[csv] 写入 {len(rows)} 行 → {path}")
        return len(rows)
