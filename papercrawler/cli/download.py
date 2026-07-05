"""
download + batch 命令

papercrawler download [OPTIONS]  — 单篇通过 DOI/URL 下载
papercrawler batch   [OPTIONS]  — 从文件批量导入任务
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

import typer

from papercrawler.cli._helpers import (
    _default_run_name, _do_download, _parse_task_file, _run, _setup, console,
)


def cmd_download(
    doi:        Optional[str] = typer.Option(None, "-d", "--doi",       help="DOI"),
    url:        Optional[str] = typer.Option(None, "-u", "--url",       help="直接 URL"),
    from_csv:   Optional[str] = typer.Option(None, "--from-csv",        help="从 CSV 文件加载论文列表并下载(由 `search` 命令生成的 CSV)"),
    skip_already_downloaded: bool = typer.Option(False, "--skip-already", help="(配合 --from-csv) 跳过 CSV 中 downloaded=true 的行"),
    name:       Optional[str] = typer.Option(None, "--name",           help="本次运行名称(留空=时间戳)"),
    output_dir: Optional[str] = typer.Option(None, "--output-dir",      help="输出目录(留空=results/<name 或时间戳>/)"),
    force:      bool          = typer.Option(False, "--force",           help="强制重新下载(覆盖本 run DB)"),
    force_global: bool        = typer.Option(False, "--force-global",    help="强制重新下载(覆盖全局 DB)"),
    dry_run:    bool          = typer.Option(False, "--dry-run",         help="模拟运行"),
    config_path: Optional[str] = typer.Option(None, "--config",         help="配置文件路径"),
    verbose:    bool          = typer.Option(False, "--verbose",         help="详细日志"),
):
    """
    通过 DOI/URL 下载单篇,或通过 --from-csv 批量下载 CSV 中的论文列表。

    典型工作流(支持断点续下):
      1. 检索: papercrawler search -q "..." --year-from 2020
         → 输出 results/<时间戳>__<关键词>/<日期>_<关键词>_<数量>.csv
      2. 首次下载: papercrawler download --from-csv results/.../...csv
         → 中途 ctrl+c 也安全:全局 DB 记录已下载的论文
      3. 续下(同样命令): papercrawler download --from-csv results/.../...csv
         → 自动跳过已下载的论文,只下未完成的部分
      4. 重下全部: papercrawler download --from-csv results/.../...csv --force-global
    """
    from papercrawler.models import AccessStatus, PaperMetadata, SearchQuery
    from papercrawler.metadata.extractor import MetadataExtractor
    from papercrawler.search.manager import SearchManager

    cfg = _setup(config_path, verbose)

    # 参数冲突检查
    provided = sum(bool(x) for x in (doi, url, from_csv))
    if provided == 0:
        console.print("[red]错误：请提供 --doi、--url 或 --from-csv[/red]")
        raise typer.Exit(1)
    if provided > 1:
        console.print("[red]错误：--doi、--url、--from-csv 互斥,只能选一个[/red]")
        raise typer.Exit(1)

    if output_dir is None:
        slug = None
        if doi:
            slug = re.sub(r"[^\w]", "_", doi)[:30].strip("_")
        elif from_csv:
            slug = Path(from_csv).stem[:30]
        elif url:
            slug = re.sub(r"[^\w]", "_", url)[:30].strip("_")
        run_name = name or _default_run_name(slug, None, None)
        output_dir = f"results/{run_name}"
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    console.print(f"[dim]本次运行目录: {Path(output_dir).resolve()}[/dim]")

    # 1. 加载论文列表
    papers: list = []

    async def _resolve_papers_from_csv() -> list:
        from papercrawler.export.csv_writer import CSVReader
        reader = CSVReader()
        try:
            loaded = reader.read(from_csv, filter_downloaded=skip_already_downloaded)
        except FileNotFoundError as e:
            console.print(f"[red]{e}[/red]")
            return []
        except Exception as e:
            console.print(f"[red]CSV 读取失败: {e}[/red]")
            return []
        if not loaded:
            console.print("[yellow]CSV 中无有效论文(可能全部已 downloaded=true)[/yellow]")
            return []

        # 在内存里直接复用:不再次 enrich(避免重复网络请求)
        # 但仍走 downloader 的全局 DB 检查 → 自动跳过已下载
        return loaded

    async def _resolve_papers_from_doi() -> list:
        q = SearchQuery(doi=doi, max_results=1)
        manager = SearchManager(config=cfg)
        return await manager.search(q)

    async def _resolve_papers_from_url() -> list:
        return [PaperMetadata(
            title="Direct URL Download",
            oa_url=url,
            access_status=AccessStatus.OPEN_ACCESS_PDF,
        )]

    if from_csv:
        papers = _run(_resolve_papers_from_csv())
    elif doi:
        papers = _run(_resolve_papers_from_doi())
    else:  # url
        papers = _run(_resolve_papers_from_url())

    if not papers:
        console.print("[red]未找到任何论文可下载[/red]")
        raise typer.Exit(1)

    console.print(f"[cyan]从 CSV 加载 {len(papers)} 篇待下载[/cyan]"
                  if from_csv else
                  f"[cyan]待下载: {len(papers)} 篇[/cyan]")

    # 2. 走完整下载流程(自动断点续下:全局 DB 默认跳过已下载)
    _do_download(
        papers, output_dir, cfg,
        force=force, dry_run=dry_run, force_global=force_global,
    )


def cmd_batch(
    input_file:  str           = typer.Option(..., "--input",      "-i", help="批量任务文件路径"),
    name:        Optional[str] = typer.Option(None, "--name",          help="本次运行名称(留空=时间戳)"),
    output_dir:  Optional[str] = typer.Option(None, "--output-dir",     help="输出目录(留空=results/<name 或时间戳>/)"),
    force:       bool          = typer.Option(False, "--force",           help="强制重新下载"),
    dry_run:     bool          = typer.Option(False, "--dry-run",         help="模拟运行"),
    config_path: Optional[str] = typer.Option(None, "--config",          help="配置文件路径"),
    verbose:     bool          = typer.Option(False, "--verbose",         help="详细日志"),
):
    """
    从文件批量导入检索任务并执行。

    任务文件格式（每行一个任务）：

    \\b
    query: QM/MM electrochemistry
    author: Michael Levitt
    doi: 10.1038/253694a0
    title: Computer simulation of protein folding
    """
    from papercrawler.metadata.extractor import MetadataExtractor
    from papercrawler.search.manager import SearchManager

    cfg = _setup(config_path, verbose)
    task_file = Path(input_file)
    if not task_file.exists():
        console.print(f"[red]任务文件不存在: {input_file}[/red]")
        raise typer.Exit(1)

    if output_dir is None:
        run_name = name or _default_run_name(None, None, None)
        output_dir = f"results/{run_name}"
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    console.print(f"[dim]本次运行目录: {Path(output_dir).resolve()}[/dim]")

    queries = _parse_task_file(task_file)
    console.print(f"[cyan]共解析 {len(queries)} 个检索任务[/cyan]")

    all_papers = []

    async def _run_all():
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