"""
CLI 入口 — Typer

命令结构:
  paper-dl search    检索论文
  paper-dl download  通过 DOI/URL 直接下载
  paper-dl batch     批量任务
  paper-dl convert   将已有文件转换为 Markdown
  paper-dl history   查询下载历史
  paper-dl config    配置管理
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Optional

import typer
from loguru import logger
from rich import print as rprint
from rich.console import Console
from rich.table import Table

from papercrawler.config import AppConfig, load_config, set_config
from papercrawler.models import SearchQuery

app = typer.Typer(
    name="paper-dl",
    help="学术论文检索与下载工具。支持 OA 全文下载和 MarkItDown 格式转换。",
    no_args_is_help=True,
    pretty_exceptions_enable=False,
)
console = Console()

# ---------------------------------------------------------------------------
# 公共选项
# ---------------------------------------------------------------------------

def _setup(config_path: Optional[str] = None, verbose: bool = False) -> AppConfig:
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
# search 命令
# ---------------------------------------------------------------------------

@app.command("search")
def cmd_search(
    query:            Optional[str]  = typer.Option(None, "-q", "--query",          help="关键词检索"),
    author:           Optional[str]  = typer.Option(None, "-a", "--author",         help="作者名检索"),
    title:            Optional[str]  = typer.Option(None, "-t", "--title",          help="题目检索"),
    doi:              Optional[str]  = typer.Option(None, "-d", "--doi",            help="DOI 直接检索"),
    max_results:      int             = typer.Option(20,   "-n", "--max-results",    help="最大结果数"),
    year_from:        Optional[int]  = typer.Option(None,  "--year-from",           help="发表年份下限"),
    year_to:          Optional[int]  = typer.Option(None,  "--year-to",             help="发表年份上限"),
    source:           Optional[str]  = typer.Option(None,  "--source",              help="指定数据源（逗号分隔）"),
    sort:             str             = typer.Option("relevance", "--sort",          help="relevance|date|citations"),
    oa_only:          bool            = typer.Option(False, "--oa-only",             help="仅返回 OA 论文"),
    min_author_score: float           = typer.Option(0.0,   "--min-author-score",    help="最低作者匹配分数（0.0~1.0，0.0=不过滤）"),
    # 领域感知 ✨
    interest:         bool            = typer.Option(False, "--interest",            help="启用 [interest] 配置进行领域相关性过滤"),
    interest_threshold: float         = typer.Option(0.0,   "--interest-threshold", help="最低领域相关性分数（0.0~1.0，0.0=仅标注）"),
    categorize:       bool            = typer.Option(False, "--categorize",          help="按 [interest.categories] 自动给论文打分类标签"),
    csv_path:         Optional[str]  = typer.Option(None,  "--csv",                 help="导出符合条件的结果到 CSV 文件路径"),
    # 下载
    download:         bool            = typer.Option(False, "--download",            help="检索后自动下载"),
    output_dir:       str             = typer.Option("./papers", "--output-dir",     help="输出目录"),
    fmt:              str             = typer.Option("table", "--format",            help="table|json|md"),
    force:            bool            = typer.Option(False, "--force",               help="强制重新下载"),
    dry_run:          bool            = typer.Option(False, "--dry-run",             help="模拟运行（不实际下载）"),
    config_path:      Optional[str]  = typer.Option(None,  "--config",              help="配置文件路径"),
    verbose:          bool            = typer.Option(False, "--verbose",             help="详细日志"),
):
    """检索论文。可通过关键词、作者名、题目或 DOI 检索。

    启用 --interest 后,会根据 [interest] 配置对论文打分,可用 --interest-threshold 过滤。
    启用 --categorize 后,会按 [interest.categories] 给论文打多标签。
    启用 --csv PATH 后,会导出符合条件的结果到 CSV(在阈值过滤之后)。
    """
    cfg = _setup(config_path, verbose)

    if not any([query, author, title, doi]):
        console.print("[red]错误：至少提供 --query、--author、--title 或 --doi 之一[/red]")
        raise typer.Exit(1)

    # --min-author-score 命令行参数覆盖配置文件中的阈值
    if min_author_score > 0.0:
        cfg.filters.author_match_threshold = min_author_score

    sources = [s.strip() for s in source.split(",")] if source else []
    q = SearchQuery(
        query=query,
        author=author,
        title=title,
        doi=doi,
        max_results=max_results,
        year_from=year_from,
        year_to=year_to,
        sources=sources,
        sort=sort,
        oa_only=oa_only,
    )

    async def _run_search():
        from papercrawler.search.manager import SearchManager
        from papercrawler.metadata.extractor import MetadataExtractor

        manager = SearchManager(config=cfg)
        papers, source_counts = await manager.search_with_stats(q)

        # 补充元数据和 OA 状态
        if papers:
            extractor = MetadataExtractor(config=cfg)
            console.print(f"[cyan]正在检查 OA 状态（共 {len(papers)} 篇）...[/cyan]")
            papers = await extractor.enrich_batch(papers)

        return papers, source_counts

    papers, source_counts = _run(_run_search())

    # 显示各数据源检索结果统计
    if source_counts:
        _display_source_stats(source_counts)

    if not papers:
        console.print("[yellow]未找到符合条件的论文[/yellow]")
        raise typer.Exit(0)

    # ✨ 领域相关性判定 + 自动分类
    if interest or categorize:
        _run_interest_pipeline(papers, cfg, interest, interest_threshold, categorize)

    # ✨ CSV 导出(在阈值过滤之后)
    if csv_path:
        _run_csv_export(papers, csv_path)

    # 如果启用 --interest 但没有 --interest-threshold,默认全部保留(只标注)
    # 如果 --interest-threshold > 0,过滤已在 pipeline 内完成

    # 展示结果（传入 author 用于显示匹配度列,以及是否显示 interest/category 列）
    _display_results(
        papers,
        fmt,
        query_author=author,
        show_interest=interest,
        show_categories=categorize,
    )

    if download:
        _do_download(papers, output_dir, cfg, force=force, dry_run=dry_run)


# ---------------------------------------------------------------------------
# download 命令
# ---------------------------------------------------------------------------

@app.command("download")
def cmd_download(
    doi:        Optional[str] = typer.Option(None, "-d", "--doi",       help="DOI"),
    url:        Optional[str] = typer.Option(None, "-u", "--url",       help="直接 URL"),
    output_dir: str           = typer.Option("./papers", "--output-dir", help="输出目录"),
    force:      bool          = typer.Option(False, "--force",           help="强制重新下载"),
    dry_run:    bool          = typer.Option(False, "--dry-run",         help="模拟运行"),
    config_path: Optional[str] = typer.Option(None, "--config",         help="配置文件路径"),
    verbose:    bool          = typer.Option(False, "--verbose",         help="详细日志"),
):
    """通过 DOI 或 URL 直接下载单篇论文。"""
    cfg = _setup(config_path, verbose)

    if not doi and not url:
        console.print("[red]错误：请提供 --doi 或 --url[/red]")
        raise typer.Exit(1)

    async def _run_download():
        from papercrawler.search.manager import SearchManager
        from papercrawler.metadata.extractor import MetadataExtractor

        if doi:
            q = SearchQuery(doi=doi, max_results=1)
            manager = SearchManager(config=cfg)
            papers = await manager.search(q)
        else:
            from papercrawler.models import PaperMetadata, AccessStatus
            papers = [PaperMetadata(title="Direct URL Download", oa_url=url,
                                    access_status=AccessStatus.OPEN_ACCESS_PDF)]

        if not papers:
            console.print(f"[red]未找到论文: {doi or url}[/red]")
            return

        extractor = MetadataExtractor(config=cfg)
        papers = await extractor.enrich_batch(papers)
        return papers

    papers = _run(_run_download())
    if papers:
        _do_download(papers, output_dir, cfg, force=force, dry_run=dry_run)


# ---------------------------------------------------------------------------
# batch 命令
# ---------------------------------------------------------------------------

@app.command("batch")
def cmd_batch(
    input_file:  str           = typer.Option(..., "--input",      "-i", help="批量任务文件路径"),
    output_dir:  str           = typer.Option("./papers", "--output-dir", help="输出目录"),
    force:       bool          = typer.Option(False, "--force",           help="强制重新下载"),
    dry_run:     bool          = typer.Option(False, "--dry-run",         help="模拟运行"),
    config_path: Optional[str] = typer.Option(None, "--config",          help="配置文件路径"),
    verbose:     bool          = typer.Option(False, "--verbose",         help="详细日志"),
):
    """
    从文件批量导入检索任务并执行。

    任务文件格式（每行一个任务）：

    \b
    query: QM/MM electrochemistry
    author: Michael Levitt
    doi: 10.1038/253694a0
    title: Computer simulation of protein folding
    """
    cfg = _setup(config_path, verbose)
    task_file = Path(input_file)
    if not task_file.exists():
        console.print(f"[red]任务文件不存在: {input_file}[/red]")
        raise typer.Exit(1)

    queries = _parse_task_file(task_file)
    console.print(f"[cyan]共解析 {len(queries)} 个检索任务[/cyan]")

    all_papers = []

    async def _run_all():
        from papercrawler.search.manager import SearchManager
        from papercrawler.metadata.extractor import MetadataExtractor

        manager = SearchManager(config=cfg)
        extractor = MetadataExtractor(config=cfg)
        for i, q in enumerate(queries, 1):
            console.print(f"[bold]任务 {i}/{len(queries)}: {q.build_text_query()[:60]}[/bold]")
            papers = await manager.search(q)
            papers = await extractor.enrich_batch(papers)
            all_papers.extend(papers)

    _run(_run_all())
    if all_papers:
        _do_download(all_papers, output_dir, cfg, force=force, dry_run=dry_run)


# ---------------------------------------------------------------------------
# convert 命令
# ---------------------------------------------------------------------------

@app.command("convert")
def cmd_convert(
    file_path:  str           = typer.Argument(..., help="要转换的文件路径（PDF/HTML/DOCX）"),
    output:     Optional[str] = typer.Option(None, "--output", "-o", help="输出 .md 文件路径"),
    config_path: Optional[str] = typer.Option(None, "--config",      help="配置文件路径"),
    verbose:    bool           = typer.Option(False, "--verbose",     help="详细日志"),
):
    """将已有 PDF/HTML/DOCX 文件转换为 Markdown。"""
    _setup(config_path, verbose)
    from papercrawler.convert.markitdown_converter import MarkItDownConverter

    converter = MarkItDownConverter(enabled=True)
    if not converter.is_available():
        console.print("[red]MarkItDown 不可用，请安装: pip install markitdown[all][/red]")
        raise typer.Exit(1)

    result = converter.convert_file(file_path)
    if result is None:
        console.print(f"[red]转换失败: {file_path}[/red]")
        raise typer.Exit(1)

    out_path = output or str(Path(file_path).with_suffix(".md"))
    Path(out_path).write_text(result, encoding="utf-8")
    console.print(f"[green]转换成功 → {out_path}[/green]")


# ---------------------------------------------------------------------------
# history 命令
# ---------------------------------------------------------------------------

history_app = typer.Typer(help="查询下载历史记录")
app.add_typer(history_app, name="history")


@history_app.command("list")
def history_list(
    status:     Optional[str] = typer.Option(None, "--status", help="过滤状态: success|failed|skipped"),
    limit:      int           = typer.Option(50,   "--limit",  help="显示条数"),
    output_dir: str           = typer.Option("./papers", "--output-dir", help="论文输出目录（含数据库）"),
):
    """列出下载记录。"""
    db_path = str(Path(output_dir) / "_download_log.db")
    if not Path(db_path).exists():
        console.print("[yellow]暂无下载记录（数据库文件不存在）[/yellow]")
        return

    from papercrawler.download.database import DownloadDatabase
    db = DownloadDatabase(db_path)
    records = db.list_records(status=status, limit=limit)

    if not records:
        console.print("[yellow]未找到符合条件的记录[/yellow]")
        return

    table = Table(title=f"下载记录（{status or '全部'}，前{limit}条）")
    table.add_column("#",      style="dim")
    table.add_column("标题",   max_width=40)
    table.add_column("年份",   justify="center")
    table.add_column("状态",   justify="center")
    table.add_column("OA 状态", justify="center")
    table.add_column("DOI")

    status_color = {"success": "green", "failed": "red", "skipped": "yellow", "pending": "cyan"}
    for r in records:
        color = status_color.get(r["download_status"], "white")
        table.add_row(
            str(r["id"]),
            r["title"][:40],
            str(r["year"] or "—"),
            f"[{color}]{r['download_status']}[/{color}]",
            r["access_status"] or "—",
            r["doi"] or "—",
        )
    console.print(table)


@history_app.command("stats")
def history_stats(
    output_dir: str = typer.Option("./papers", "--output-dir", help="论文输出目录（含数据库）"),
):
    """显示下载统计摘要。"""
    db_path = str(Path(output_dir) / "_download_log.db")
    if not Path(db_path).exists():
        console.print("[yellow]暂无下载记录[/yellow]")
        return

    from papercrawler.download.database import DownloadDatabase
    db = DownloadDatabase(db_path)
    stats = db.stats()

    table = Table(title="下载统计")
    table.add_column("状态")
    table.add_column("数量", justify="right")
    total = sum(stats.values())
    for status, count in stats.items():
        pct = f"{count/total*100:.1f}%" if total else "0%"
        table.add_row(status, f"{count}  ({pct})")
    table.add_row("[bold]合计[/bold]", f"[bold]{total}[/bold]")
    console.print(table)


# ---------------------------------------------------------------------------
# config 命令
# ---------------------------------------------------------------------------

config_app = typer.Typer(help="配置管理")
app.add_typer(config_app, name="config")


@config_app.command("init")
def config_init():
    """在当前目录生成默认配置文件 papercrawler.toml。"""
    example = Path(__file__).parent.parent / "papercrawler.toml.example"
    dest = Path("papercrawler.toml")
    if dest.exists():
        overwrite = typer.confirm("papercrawler.toml 已存在，是否覆盖？", default=False)
        if not overwrite:
            raise typer.Exit(0)

    if example.exists():
        import shutil
        shutil.copy(example, dest)
    else:
        # 内嵌最小配置
        dest.write_text(
            "[api_keys]\nunpaywall_email = \"your@email.com\"\n",
            encoding="utf-8",
        )
    console.print(f"[green]配置文件已生成: {dest.resolve()}[/green]")
    console.print("[yellow]请编辑 papercrawler.toml，至少填写 unpaywall_email[/yellow]")


@config_app.command("show")
def config_show(
    config_path: Optional[str] = typer.Option(None, "--config"),
):
    """显示当前配置。"""
    cfg = load_config(config_path)
    rprint(cfg.model_dump())


# ---------------------------------------------------------------------------
# 内部工具函数
# ---------------------------------------------------------------------------

def _display_source_stats(source_counts: dict) -> None:
    """以紧凑表格展示各数据源的检索结果数量。"""
    table = Table(title="各数据源检索结果", box=None, show_header=True, header_style="bold cyan")
    table.add_column("数据源",     style="cyan",  min_width=18)
    table.add_column("结果数",     justify="right", min_width=6)
    table.add_column("状态",       justify="center", min_width=6)

    total = 0
    for source, count in source_counts.items():
        if count < 0:
            table.add_row(source, "—", "[red]失败[/red]")
        else:
            total += count
            count_str = f"[green]{count}[/green]" if count > 0 else "[dim]0[/dim]"
            table.add_row(source, count_str, "[green]✓[/green]" if count > 0 else "[dim]—[/dim]")

    console.print(table)
    console.print(f"[dim]合并去重前共 {total} 条原始结果[/dim]\n")


def _display_results(
    papers,
    fmt: str,
    query_author: Optional[str] = None,
    show_interest: bool = False,
    show_categories: bool = False,
) -> None:
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
        table.add_column("领域分", width=8, justify="center")
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
            row.append(_colorize_score(p.interest_score))
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


def _run_interest_pipeline(
    papers,
    cfg: AppConfig,
    interest: bool,
    interest_threshold: float,
    categorize: bool,
) -> None:
    """
    在主流程内运行:领域打分 → 分类 → 阈值过滤。

    阈值过滤是 in-place 修改 papers 列表(顺序不变,剔除低分论文)。
    """
    from papercrawler.classify import DomainFilter, Categorizer

    if not cfg.interest.must_have and not cfg.interest.should_have and not cfg.interest.exclude and not cfg.interest.categories:
        console.print(
            "[yellow]⚠️  --interest/--categorize 已启用,但 [interest] 配置为空。"
            "请在 papercrawler.toml 中配置关键词与分类,或直接不传这两个参数。[/yellow]"
        )
        return

    if interest:
        df = DomainFilter(cfg.interest)
        df.annotate(papers)
        if interest_threshold > 0.0:
            before = len(papers)
            papers[:] = [p for p in papers if (p.interest_score or 0.0) >= interest_threshold]
            kept = len(papers)
            console.print(
                f"[cyan]领域过滤: 阈值={interest_threshold:.2f},"
                f"保留 {kept}/{before} 篇,按领域分降序[/cyan]"
            )
        else:
            console.print("[cyan]领域打分完成(未启用阈值过滤)[/cyan]")
        # 按分数排序
        papers.sort(key=lambda p: p.interest_score or 0.0, reverse=True)

    if categorize:
        cat = Categorizer(cfg.interest)
        cat.annotate(papers)
        n_categorized = sum(1 for p in papers if p.categories)
        console.print(
            f"[cyan]分类完成: {n_categorized}/{len(papers)} 篇被分类,"
            f"分类数={len(cfg.interest.categories)}[/cyan]"
        )


def _run_csv_export(papers, csv_path: str) -> None:
    """导出论文列表到 CSV"""
    from papercrawler.export.csv_writer import CSVWriter

    writer = CSVWriter()
    n = writer.write(papers, csv_path)
    console.print(f"[green]CSV 已导出: {n} 行 → {csv_path}[/green]")


def _do_download(papers, output_dir: str, cfg: AppConfig,
                 force: bool = False, dry_run: bool = False) -> None:
    console.print(f"\n[bold]开始下载 {len(papers)} 篇论文 → {output_dir}[/bold]")

    async def _run_dl():
        from papercrawler.download.downloader import PaperDownloader
        from papercrawler.download.storage import PaperStorage

        downloader = PaperDownloader(output_dir=output_dir, config=cfg)
        tasks = await downloader.download_all(papers, force=force, dry_run=dry_run)

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

    _run(_run_dl())


def _parse_task_file(path: Path) -> list[SearchQuery]:
    """解析批量任务文件，每行格式：type: value"""
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


if __name__ == "__main__":
    app()
