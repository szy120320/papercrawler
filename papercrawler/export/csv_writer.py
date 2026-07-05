"""
csv_writer.py — 论文列表 CSV 导出

输出字段(列顺序固定):
    title            论文标题
    doi              DOI
    year             发表年份
    journal          期刊/来源
    authors          作者列表(; 分隔)
    coarse_score     第一阶段粗筛分数(title + must_have/should_have/exclude)
    semantic_score   第二阶段细筛:命中关键词数(整数,title+abstract+keywords 中
                     semantic_keywords 命中个数;阈值默认 ≥ 3 才保留)
    author_score     作者匹配分数(0~1,可能为空)
    citations        引用数
    access_status    OA 状态字符串
    oa_url           OA 链接
    sources          命中数据源(; 分隔)
    downloaded       是否已下载(true/false)
    paper_dir        已下载时的论文目录名(相对 output_dir)

注意:
    - 2026-07-05 移除 categories 列(分类功能已删除)
    - 不再导出 interest_score(两阶段独立判断,无合并分数)
    - semantic_score 现在是整数(命中关键词数),不是 0~1 的浮点

使用:
    writer = CSVWriter()
    writer.write(papers, "matched_papers.csv")
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Iterable, Optional

from loguru import logger

from papercrawler.models import PaperMetadata


# 固定列顺序(便于下游脚本/Excel 模板)
COLUMNS: list[str] = [
    "title",
    "doi",
    "year",
    "journal",
    "authors",
    "coarse_score",
    "semantic_score",
    "citations",
    "access_status",
    "oa_url",
    "sources",
    "downloaded",
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
        "coarse_score":   paper.coarse_score if paper.coarse_score is not None else "",
        "semantic_score": paper.semantic_score if paper.semantic_score is not None else "",
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


# ---------------------------------------------------------------------------
# CSVReader — 把 search 输出的 CSV 重新加载为 PaperMetadata
# (2026-07-05 新增,用于 `papercrawler download --from-csv`)
# ---------------------------------------------------------------------------

class CSVReadError(ValueError):
    """CSV 格式或字段错误"""


# CSV 列名 → PaperMetadata 字段的映射
_CSV_TO_MODEL = {
    "title":          "title",
    "doi":            "doi",
    "year":           "year",
    "journal":        "journal",
    "authors":        "authors",        # ";" 分隔字符串
    "citations":      "citations_count",
    "access_status":  "access_status",
    "oa_url":         "oa_url",
    "sources":        "sources",        # ";" 分隔字符串
}


def _row_to_paper(row: dict) -> PaperMetadata:
    """将 CSV 一行还原成 PaperMetadata"""
    from papercrawler.models import Author, AccessStatus

    title = (row.get("title") or "").strip()
    if not title:
        raise CSVReadError("缺少 title 字段(必需)")

    # authors: ";" 分隔
    authors_raw = (row.get("authors") or "").strip()
    authors = []
    if authors_raw:
        for name in authors_raw.split(";"):
            name = name.strip()
            if name:
                authors.append(Author(name=name))

    # year: int or None
    year_val: Optional[int] = None
    year_raw = (row.get("year") or "").strip()
    if year_raw:
        try:
            year_val = int(year_raw)
        except ValueError:
            logger.warning(f"[csv-read] 无法解析 year={year_raw!r},置 None")

    # access_status: 枚举字符串
    as_raw = (row.get("access_status") or "").strip()
    access: AccessStatus = AccessStatus.UNKNOWN
    if as_raw:
        try:
            access = AccessStatus(as_raw)
        except ValueError:
            logger.warning(f"[csv-read] 未知 access_status={as_raw!r},置 UNKNOWN")

    # citations: int or None
    cit_val: Optional[int] = None
    cit_raw = (row.get("citations") or "").strip()
    if cit_raw:
        try:
            cit_val = int(cit_raw)
        except ValueError:
            pass

    # sources: ";" 分隔
    sources_raw = (row.get("sources") or "").strip()
    sources = [s.strip() for s in sources_raw.split(";") if s.strip()] if sources_raw else []

    return PaperMetadata(
        title=title,
        authors=authors,
        year=year_val,
        journal=(row.get("journal") or "").strip() or None,
        doi=(row.get("doi") or "").strip() or None,
        oa_url=(row.get("oa_url") or "").strip() or None,
        citations_count=cit_val,
        access_status=access,
        sources=sources,
    )


class CSVReader:
    """CSV 读取器 — 把 search 导出的 CSV 重新加载为 PaperMetadata 列表"""

    def __init__(self) -> None:
        pass

    def read(
        self,
        path: str | Path,
        filter_downloaded: bool = False,
    ) -> list[PaperMetadata]:
        """
        读取 CSV,返回 PaperMetadata 列表。

        Args:
            path: CSV 文件路径(支持 UTF-8 / UTF-8-sig)
            filter_downloaded: 若 True,跳过 downloaded=True 的行(可选过滤)

        Returns:
            PaperMetadata 列表(保留原顺序)

        Raises:
            FileNotFoundError: 文件不存在
            CSVReadError: 文件格式错误
        """
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(f"CSV 文件不存在: {path}")

        papers: list[PaperMetadata] = []
        skipped_already = 0
        errors = 0

        with open(path, "r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            required = {"title"}
            cols = set(reader.fieldnames or [])
            missing = required - cols
            if missing:
                raise CSVReadError(f"CSV 缺少必需列: {missing} (实际列: {reader.fieldnames})")

            for line_no, row in enumerate(reader, start=2):
                # downloaded 列存在且为 true → 跳过(可选)
                if filter_downloaded:
                    flag = (row.get("downloaded") or "").strip().lower()
                    if flag in ("true", "1", "yes"):
                        skipped_already += 1
                        continue
                try:
                    paper = _row_to_paper(row)
                    papers.append(paper)
                except CSVReadError as e:
                    errors += 1
                    logger.warning(f"[csv-read] 第 {line_no} 行解析失败: {e}")

        if skipped_already:
            logger.info(f"[csv-read] 跳过 {skipped_already} 行已下载论文(downloaded=true)")
        if errors:
            logger.warning(f"[csv-read] 解析失败 {errors} 行(已跳过)")

        logger.info(f"[csv-read] 从 {path} 读取 {len(papers)} 篇(跳过 {skipped_already} 已有 / 失败 {errors})")
        return papers
