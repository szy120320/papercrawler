"""
CLI 共享工具函数

被 search / download / batch / recategorize / convert / history / config 共用:
  - console: 单例 rich Console
  - logger:  loguru 全局 logger
  - _setup: 加载配置 + 初始化 logger
  - _run: 异步运行入口
  - _default_run_name: 生成默认 run 名
  - _display_source_stats: 显示各数据源检索数
  - _display_results: 显示论文检索结果表格
  - _run_interest_pipeline: 两阶段领域打分流程
  - _run_csv_export: 导出 CSV
  - _run_combined_csv_export: 合并 results/ 下所有 metadata.json
  - _do_download: 并发下载主入口
  - _parse_task_file: 解析 batch 任务文件
"""

from __future__ import annotations

import asyncio
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

from loguru import logger
from rich.console import Console
from rich.table import Table

from papercrawler.config import AppConfig

console = Console()


# ---------------------------------------------------------------------------
# 日志 + 配置初始化
# ---------------------------------------------------------------------------

def _setup(config_path: Optional[str] = None, verbose: bool = False) -> AppConfig:
    """加载配置并配置 loguru logger"""
    from papercrawler.config import load_config, set_config

    cfg = load_config(config_path)
    set_config(cfg)
    level = "DEBUG" if verbose else "INFO"
    logger.remove()
    logger.add(sys.stderr, level=level, format="{time:HH:mm:ss} | {level:<8} | {message}")
    return cfg


def _run(coro):
    """统一异步运行入口"""
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Run 命名 / 目录
# ---------------------------------------------------------------------------

def _default_run_name(query: Optional[str], author: Optional[str], doi: Optional[str]) -> str:
    """
    根据检索内容生成默认的运行名,用于 results/ 子目录。

    优先级: DOI 精确(短) > 作者 > 关键词(取前几个词) > 时间戳
    """
    timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")

    if doi:
        slug = re.sub(r"[^\w]", "_", doi)[:30].strip("_")
        return f"{timestamp}__{slug}"

    if author:
        slug = re.sub(r"[^\w]", "_", author)[:25].strip("_")
        return f"{timestamp}__{slug}"

    if query:
        # 取前 4 个有意义的词
        words = re.findall(r"\w+", query)[:4]
        slug = "_".join(words) if words else "search"
        return f"{timestamp}__{slug}"

    return timestamp


# ---------------------------------------------------------------------------
# 显示辅助
# ---------------------------------------------------------------------------

def _display_source_stats(source_stats) -> None:
    """以紧凑表格展示各数据源的检索结果数量与失败原因。

    接受两种类型(向后兼容):
      - SourceStats (papercrawler.search.manager.SourceStats):新接口
      - dict[str, int]:旧接口,负数 = 失败
    """
    from papercrawler.search.manager import SourceStats

    table = Table(title="各数据源检索结果", box=None, show_header=True, header_style="bold cyan")
    table.add_column("数据源",     style="cyan",  min_width=18)
    table.add_column("结果数",     justify="right", min_width=6)
    table.add_column("状态",       justify="center", min_width=14)

    total = 0
    for source, item in source_stats.items():
        # 兼容旧接口 dict[str, int]
        if isinstance(item, int):
            failure_kind = "失败" if item < 0 else None
            ok_count = max(0, item)
        else:
            # 新接口 SourceStats
            failure_kind = item.failure_kind
            ok_count = item.ok_count

        if failure_kind:
            label = {
                "http_error":  "[red]HTTP 错误[/red]",
                "parse_error": "[red]解析失败[/red]",
                "timeout":     "[red]超时[/red]",
                "rate_limit":  "[yellow]限速持续[/yellow]",
                "other":       "[red]异常[/red]",
            }.get(failure_kind, f"[red]{failure_kind}[/red]")
            table.add_row(source, "—", label)
        else:
            total += ok_count
            count_str = f"[green]{ok_count}[/green]" if ok_count > 0 else "[dim]0[/dim]"
            status = "[green]✓[/green]" if ok_count > 0 else "[dim]— 无命中[/dim]"
            table.add_row(source, count_str, status)

    console.print(table)
    console.print(f"[dim]合并去重前共 {total} 条原始结果[/dim]\n")


def _display_results(
    papers,
    fmt: str,
    query_author: Optional[str] = None,
    show_interest: bool = False,
    show_categories: bool = False,
) -> None:
    """在终端显示论文检索结果表格 / JSON / Markdown"""
    if fmt == "json":
        data = [p.model_dump(mode="json") for p in papers]
        console.print_json(json.dumps(data, ensure_ascii=False))
        return

    show_score = bool(query_author)  # 仅在按作者检索时显示匹配度列

    table = Table(title=f"检索结果（共 {len(papers)} 篇）", show_lines=False)
    table.add_column("#",      style="dim", width=4)
    table.add_column("标题",   max_width=36)
    table.add_column("作者",   max_width=16)
    table.add_column("年",     width=5, justify="center")
    table.add_column("期刊",   max_width=18)
    table.add_column("引用",   width=6, justify="right")
    table.add_column("OA",     width=12, justify="center")
    if show_score:
        table.add_column("作者分", width=8, justify="center")
    if show_interest:
        # 两阶段打分:只显示粗筛和细筛两个独立分数(不合并)
        table.add_column("粗筛",  width=7, justify="center")
        table.add_column("细筛",  width=7, justify="center")
    if show_categories:
        table.add_column("分类",   max_width=20)
    table.add_column("DOI",    max_width=22)

    oa_color = {
        "oa_pdf":       "[green]OA-PDF[/green]",
        "oa_html":      "[green]OA-HTML[/green]",
        "oa_preprint":  "[cyan]预印本[/cyan]",
        "metadata_only":"[yellow]仅元数据[/yellow]",
        "unknown":      "[dim]未知[/dim]",
    }

    def _colorize_score(score) -> str:
        if score is None:
            return "[dim]—[/dim]"
        if score >= 0.80:
            return f"[green]{score:.2f}[/green]"
        if score >= 0.50:
            return f"[yellow]{score:.2f}[/yellow]"
        return f"[red]{score:.2f}[/red]"

    for i, p in enumerate(papers, 1):
        first_author = p.authors[0].display_name() if p.authors else "—"

        row = [
            str(i),
            p.title[:36],
            first_author,
            str(p.year or "—"),
            (p.journal or "—")[:18],
            str(p.citations_count or "—"),
            oa_color.get(p.access_status.value, "—"),
        ]

        if show_score:
            row.append(_colorize_score(p.author_match_score))
        if show_interest:
            row.append(_colorize_score(p.coarse_score))
            row.append(_colorize_score(p.semantic_score))
        if show_categories:
            cats = "; ".join(p.categories) if p.categories else "[dim]—[/dim]"
            row.append(cats[:20])

        row.append((p.doi or "—")[:22])
        table.add_row(*row)

    console.print(table)

    notes = []
    if show_score:
        notes.append(
            "[dim]作者分: [green]≥0.80 高[/green]  "
            "[yellow]0.50–0.79 中[/yellow]  "
            "[red]<0.50 低[/red][/dim]"
        )
    if show_interest:
        notes.append(
            "[dim]领域分: 同上规则,基于 title+abstract 与 [interest] 关键词匹配[/dim]"
        )
    if notes:
        console.print("  ".join(notes))


# ---------------------------------------------------------------------------
# 领域打分流水线
# ---------------------------------------------------------------------------

def _run_interest_pipeline(
    papers,
    cfg: AppConfig,
    interest: bool,
    interest_threshold: float,
    semantic_min_matches: int,
    categorize: bool,
) -> None:
    """
    两阶段打分流程(阶段1 粗筛已在 search 后立刻执行)。

      阶段2 细筛(SemanticFilter,纯关键词命中计数):
        - 在 title + abstract + keywords 中数 semantic_keywords 命中几个
        - 命中数写到 paper.semantic_score(整数,0~N)

    双门限硬过滤:
        粗筛分数 >= interest_threshold AND 命中关键词数 >= semantic_min_matches
        任一不达标都剔除。语义分数是整数(命中数),不做加权。

    阈值过滤是 in-place 修改 papers 列表(顺序不变,剔除低分论文)。
    """
    from papercrawler.classify import Categorizer, DomainFilter, SemanticFilter

    if (
        not cfg.interest.must_have
        and not cfg.interest.should_have
        and not cfg.interest.exclude
        and not cfg.interest.categories
        and not cfg.interest.semantic_keywords
    ):
        console.print(
            "[yellow]⚠️  --interest/--categorize 已启用,但 [interest] 配置为空。"
            "请在 papercrawler.toml 中配置关键词与分类,或直接不传这两个参数。[/yellow]"
        )
        return

    if interest:
        # 阶段2 细筛 — 写到 paper.semantic_score(整数,命中数)
        sf = SemanticFilter(cfg.interest)
        sf.annotate(papers)

        # 双门限硬过滤
        before = len(papers)
        papers[:] = [
            p for p in papers
            if (p.coarse_score or 0.0) >= interest_threshold
            and (p.semantic_score or 0.0) >= semantic_min_matches
        ]
        kept = len(papers)
        console.print(
            f"[cyan]阶段2 细筛完成 + 双门限过滤: "
            f"粗筛≥{interest_threshold:.2f} AND 命中关键词≥{semantic_min_matches}, "
            f"保留 {kept}/{before} 篇[/cyan]"
        )

        # 按精筛命中数降序
        papers.sort(key=lambda p: p.semantic_score or 0, reverse=True)

    if categorize:
        cat = Categorizer(cfg.interest)
        cat.annotate(papers)
        n_categorized = sum(1 for p in papers if p.categories)
        console.print(
            f"[cyan]分类完成: {n_categorized}/{len(papers)} 篇被分类,"
            f"分类数={len(cfg.interest.categories)}[/cyan]"
        )


# ---------------------------------------------------------------------------
# CSV 导出
# ---------------------------------------------------------------------------

def _run_csv_export(papers, csv_path: str,
                    downloaded_lookup: Optional[dict] = None,
                    paper_dir_lookup: Optional[dict] = None) -> None:
    """导出论文列表到 CSV"""
    from papercrawler.export.csv_writer import CSVWriter

    writer = CSVWriter()
    n = writer.write(papers, csv_path,
                     downloaded_lookup=downloaded_lookup,
                     paper_dir_lookup=paper_dir_lookup)
    console.print(f"[green]CSV 已导出: {n} 行 → {csv_path}[/green]")


def _run_combined_csv_export(results_root: str = "results", csv_path: Optional[str] = None) -> int:
    """
    扫描 results/ 下所有 metadata.json,合并去重,导出到一个总 CSV。

    行为:
      - 递归扫描 results_root/(跳过 _all_filtered.csv 自身)
      - 用 PaperMetadata.unique_id 去重(DOI 优先,否则 title+author+year 哈希)
      - 按 interest_score 降序,然后 year 降序,然后 title 字典序
      - 写到 csv_path(默认 results/_all_filtered.csv)

    Returns:
        写入的行数
    """
    import json as _json
    from papercrawler.models import PaperMetadata
    from papercrawler.export.csv_writer import CSVWriter

    if csv_path is None:
        csv_path = str(Path(results_root) / "_all_filtered.csv")
    csv_path = Path(csv_path)
    csv_path.parent.mkdir(parents=True, exist_ok=True)

    root = Path(results_root)
    if not root.exists():
        console.print(f"[yellow]results 根目录不存在: {root}[/yellow]")
        return 0

    seen: set[str] = set()
    papers: list[PaperMetadata] = []
    scanned = 0
    errors = 0

    for meta in root.rglob("metadata.json"):
        scanned += 1
        try:
            data = _json.loads(meta.read_text(encoding="utf-8"))
            paper = PaperMetadata(**data)
        except (ValueError, KeyError, TypeError) as e:
            # ValueError: JSON 解析 / Pydantic 验证;KeyError / TypeError: 字段缺失或类型错
            errors += 1
            logger.opt(exception=True).debug(
                f"[combined-csv] 解析 {meta} 失败: {e}"
            )
            continue

        uid = paper.unique_id
        if uid in seen:
            continue
        seen.add(uid)
        papers.append(paper)

    # 排序: interest_score desc > year desc > title asc
    papers.sort(
        key=lambda p: (
            -(p.interest_score if p.interest_score is not None else 0.0),
            -(p.year if p.year is not None else 0),
            (p.title or "").lower(),
        )
    )

    CSVWriter().write(papers, csv_path)
    console.print(
        f"[green]合并 CSV 已导出: {len(papers)} 行(扫 {scanned} 个 metadata,失败 {errors}) → {csv_path}[/green]"
    )
    return len(papers)


# ---------------------------------------------------------------------------
# 下载主入口
# ---------------------------------------------------------------------------

def _do_download(papers, output_dir: str, cfg: AppConfig,
                 force: bool = False, dry_run: bool = False,
                 force_global: bool = False) -> list:
    console.print(f"\n[bold]开始下载 {len(papers)} 篇论文 → {output_dir}[/bold]")
    if force_global:
        console.print("[yellow]⚠️  --force-global:忽略全局 DB 记录,强制重新下载[/yellow]")

    async def _run_dl():
        from papercrawler.download.downloader import PaperDownloader
        from papercrawler.download.storage import PaperStorage

        downloader = PaperDownloader(
            output_dir=output_dir, config=cfg, force_global=force_global,
        )
        tasks = await downloader.download_all(
            papers, force=force, dry_run=dry_run, force_global=force_global,
        )

        success = sum(1 for t in tasks if t.status.value == "success")
        skipped = sum(1 for t in tasks if t.status.value == "skipped")
        failed  = sum(1 for t in tasks if t.status.value == "failed")

        console.print(f"\n[bold]下载完成：[/bold] "
                      f"[green]{success} 成功[/green]  "
                      f"[yellow]{skipped} 跳过[/yellow]  "
                      f"[red]{failed} 失败[/red]")

        # 生成索引
        if not dry_run:
            storage = PaperStorage(output_dir)
            await storage.write_index(papers)

        return tasks

    return _run(_run_dl())


# ---------------------------------------------------------------------------
# Batch 任务文件解析
# ---------------------------------------------------------------------------

def _parse_task_file(path: Path) -> list:
    """解析批量任务文件，每行格式：type: value"""
    from papercrawler.models import SearchQuery

    queries = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" in line:
            key, _, value = line.partition(":")
            key = key.strip().lower()
            value = value.strip()
            if key == "query":
                queries.append(SearchQuery(query=value))
            elif key == "author":
                queries.append(SearchQuery(author=value))
            elif key == "title":
                queries.append(SearchQuery(title=value))
            elif key == "doi":
                queries.append(SearchQuery(doi=value))
    return queries