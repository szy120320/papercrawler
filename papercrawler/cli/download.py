"""
download + batch 命令

papercrawler download [OPTIONS]  — 单篇通过 DOI/URL 下载
papercrawler batch   [OPTIONS]  — 从文件批量导入任务
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer

from papercrawler.cli._helpers import (
    _default_run_name, _do_download, _parse_task_file, _run, _setup, console,
)


def cmd_download(
    doi:        Optional[str] = typer.Option(None, "-d", "--doi",       help="DOI"),
    url:        Optional[str] = typer.Option(None, "-u", "--url",       help="直接 URL"),
    name:       Optional[str] = typer.Option(None, "--name",           help="本次运行名称(留空=时间戳)"),
    output_dir: Optional[str] = typer.Option(None, "--output-dir",      help="输出目录(留空=results/<name 或时间戳>/)"),
    force:      bool          = typer.Option(False, "--force",           help="强制重新下载(覆盖本 run DB)"),
    force_global: bool        = typer.Option(False, "--force-global",    help="强制重新下载(覆盖全局 DB)"),
    dry_run:    bool          = typer.Option(False, "--dry-run",         help="模拟运行"),
    config_path: Optional[str] = typer.Option(None, "--config",         help="配置文件路径"),
    verbose:    bool          = typer.Option(False, "--verbose",         help="详细日志"),
):
    """通过 DOI 或 URL 直接下载单篇论文。"""
    from papercrawler.models import AccessStatus, PaperMetadata, SearchQuery
    from papercrawler.metadata.extractor import MetadataExtractor
    from papercrawler.search.manager import SearchManager

    cfg = _setup(config_path, verbose)

    if not doi and not url:
        console.print("[red]错误：请提供 --doi 或 --url[/red]")
        raise typer.Exit(1)

    if output_dir is None:
        run_name = name or _default_run_name(None, None, doi)
        output_dir = f"results/{run_name}"
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    console.print(f"[dim]本次运行目录: {Path(output_dir).resolve()}[/dim]")

    async def _run_download():
        if doi:
            q = SearchQuery(doi=doi, max_results=1)
            manager = SearchManager(config=cfg)
            papers = await manager.search(q)
        else:
            papers = [PaperMetadata(title="Direct URL Download", oa_url=url,
                                    access_status=AccessStatus.OPEN_ACCESS_PDF)]

        if not papers:
            console.print(f"[red]未找到论文: {doi or url}[/red]")
            return None

        extractor = MetadataExtractor(config=cfg)
        papers = await extractor.enrich_batch(papers)
        return papers

    papers = _run(_run_download())
    if papers:
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