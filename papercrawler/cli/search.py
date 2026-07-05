"""
search 命令

papercrawler search [OPTIONS]

v1.3.0 行为变更(2026-07-05):
  - 删除 `-n / --max-results` 选项(原意为"总结果数上限",实际是学术检索的反模式)
  - 改用 [cli.defaults].page_size 控制单页大小,翻页直到 API 终止信号
  - 所有参数默认值从 [cli.defaults] 读,命令行 flag 可覆盖
  - 不传任何参数 = 完全用 toml 默认值
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer

from papercrawler.cli._helpers import (
    _default_run_name, _display_results, _display_source_stats,
    _do_download, _run, _run_combined_csv_export, _run_csv_export,
    _run_interest_pipeline, _setup, console,
)


def cmd_search(
    # 2026-07-05 v1.3.0: 删除 -n/--max-results,语义改为 [cli.defaults].page_size
    query:            Optional[str]  = typer.Option(None, "-q", "--query",       help="关键词检索(留空用 [cli.defaults].query)"),
    author:           Optional[str]  = typer.Option(None, "-a", "--author",      help="作者名检索"),
    title:            Optional[str]  = typer.Option(None, "-t", "--title",       help="题目检索"),
    doi:              Optional[str]  = typer.Option(None, "-d", "--doi",         help="DOI 直接检索"),
    year_from:        Optional[int]  = typer.Option(None,  "--year-from",        help="发表年份下限(留空用 [cli.defaults].year_from)"),
    year_to:          Optional[int]  = typer.Option(None,  "--year-to",          help="发表年份上限(留空用 [cli.defaults].year_to)"),
    source:           Optional[str]  = typer.Option(None,  "--source",           help="指定数据源(逗号分隔)"),
    sort:             Optional[str]  = typer.Option(None,  "--sort",             help="relevance|date|citations(留空用 [cli.defaults].sort)"),
    oa_only:          Optional[bool] = typer.Option(None,  "--oa-only/--no-oa-only", help="仅返回 OA 论文(留空用 [cli.defaults].oa_only)"),
    min_author_score: float           = typer.Option(0.0,   "--min-author-score", help="最低作者匹配分数(0.0~1.0)"),
    # 领域感知
    interest:         Optional[bool]  = typer.Option(None, "--interest/--no-interest", help="启用领域相关性打分"),
    interest_threshold: float         = typer.Option(0.6,   "--interest-threshold",     help="粗筛分数阈值(默认 0.6)"),
    semantic_min_matches: int        = typer.Option(3,    "--semantic-min-matches",    help="细筛命中关键词数(默认 3)"),
    csv_path:         Optional[str]  = typer.Option(None,  "--csv",                 help="CSV 路径(留空=自动命名)"),
    no_csv:           bool            = typer.Option(False, "--no-csv",              help="禁用 CSV 导出"),
    # 运行管理
    name:             Optional[str]  = typer.Option(None,  "--name",                help="本次运行名"),
    output_dir:       Optional[str]  = typer.Option(None,  "--output-dir",           help="输出目录(留空用 [cli.defaults].output_dir)"),
    # 下载
    download:         Optional[bool] = typer.Option(None,  "--download/--no-download", help="检索后自动下载(留空用 [cli.defaults].auto_download)"),
    force:            bool            = typer.Option(False, "--force",               help="强制重新下载(覆盖本 run DB)"),
    force_global:     bool            = typer.Option(False, "--force-global",        help="强制重新下载(覆盖全局 DB)"),
    dry_run:          bool            = typer.Option(False, "--dry-run",             help="模拟运行(不实际下载)"),
    config_path:      Optional[str]  = typer.Option(None,  "--config",              help="配置文件路径"),
    verbose:          bool            = typer.Option(False, "--verbose",             help="详细日志"),
    fmt:              str             = typer.Option("table", "--format",            help="table|json|md"),
):
    """检索论文。可通过关键词、作者名、题目或 DOI 检索。

    v1.3.0 行为:
      - 检索参数(关键词 / 年份 / sort / oa_only / page_size)可全部在
        config.toml 的 [cli.defaults] 节设置,本命令不传任何 flag 直接用默认值
      - 单页大小通过 [cli.defaults].page_size 控制,翻页到 API 终止信号为止
      - 年份范围(year_from..year_to)会被 manager 自动拆成逐年查询,
        避免单次查询触发数据源返回数量上限

    跨 run 去重:默认会查 ~/.papercrawler/_download_log.db,已下载的论文自动跳过。
    """
    from papercrawler.models import SearchQuery

    cfg = _setup(config_path, verbose)

    # 2026-07-05 v1.3.0: 从 toml [cli.defaults] 填充所有可选参数
    cli_def = cfg.cli

    if query is None:
        query = cli_def.query or None
    if year_from is None:
        year_from = cli_def.year_from
    if year_to is None:
        year_to = cli_def.year_to
    if sort is None:
        sort = cli_def.sort
    if oa_only is None:
        oa_only = cli_def.oa_only
    if download is None:
        download = cli_def.auto_download
    if output_dir is None and cli_def.output_dir:
        output_dir = cli_def.output_dir

    # 至少要有 query / author / title / doi 其一
    if not any([query, author, title, doi]):
        console.print("[red]错误：至少提供 --query、--author、--title、--doi 之一,或设置 [cli.defaults].query[/red]")
        raise typer.Exit(1)

    # --min-author-score 命令行参数覆盖配置文件中的阈值
    if min_author_score > 0.0:
        cfg.filters.author_match_threshold = min_author_score

    # ============= 决定本次运行目录 =============
    if output_dir is None:
        run_name = name or _default_run_name(query, author, doi)
        output_dir = f"results/{run_name}"

    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    console.print(f"[dim]本次运行目录: {output_path.resolve()}[/dim]")

    # ============= 选项 A: auto-enable =============
    has_keywords = bool(cfg.interest.must_have or cfg.interest.should_have or cfg.interest.exclude)
    has_interest_config = has_keywords

    if interest is None:
        interest = has_keywords

    # CSV:三态 -> 路径
    csv_auto_generate = False
    if no_csv:
        csv_path = None
    elif csv_path is None and interest:
        csv_auto_generate = True
        csv_path = None

    # 显示本轮配置
    if has_interest_config:
        flags = []
        if interest: flags.append("interest")
        if csv_auto_generate:
            flags.append("csv→<日期>_<关键词>_<数量>.csv(自动)")
        elif csv_path:
            flags.append(f"csv→{Path(csv_path).name}")
        console.print(f"[dim]领域感知: 启用 {', '.join(flags) or '无'}[/dim]")

    # 显示 CLI 默认值来源(便于用户知道参数从哪来)
    console.print(
        f"[dim]参数: query={query!r} year_from={year_from} year_to={year_to} "
        f"sort={sort} oa_only={oa_only} page_size={cli_def.page_size} download={download}[/dim]"
    )

    # ============= 执行检索 =============
    sources = [s.strip() for s in source.split(",")] if source else []
    q = SearchQuery(
        query=query,
        author=author,
        title=title,
        doi=doi,
        page_size=cli_def.page_size,   # 2026-07-05 v1.3.0: 从 toml 读
        year_from=year_from,
        year_to=year_to,
        sources=sources,
        sort=sort,
        oa_only=oa_only,
    )

    async def _run_search():
        from papercrawler.classify import DomainFilter
        from papercrawler.metadata.extractor import MetadataExtractor
        from papercrawler.search.manager import SearchManager

        manager = SearchManager(config=cfg)
        papers, source_counts = await manager.search_with_stats(q)

        # 第一阶段:粗筛(只基于 title,快,不做 enrich)
        if papers and interest:
            df = DomainFilter(cfg.interest)
            df.annotate(papers)
            before = len(papers)
            papers[:] = [p for p in papers if (p.coarse_score or 0.0) >= (interest_threshold or 0.0)]
            console.print(
                f"[cyan]阶段1 粗筛(title): {before} → {len(papers)} 篇"
                f"(阈值={interest_threshold:.2f})[/cyan]"
            )

        # 第二步:enrich — 只对粗筛通过的论文补 abstract + OA 状态
        if papers:
            extractor = MetadataExtractor(config=cfg)
            console.print(f"[cyan]正在 enrich 补 abstract + OA 状态(共 {len(papers)} 篇)...[/cyan]")
            papers = await extractor.enrich_batch(papers)

        return papers, source_counts

    papers, source_counts = _run(_run_search())

    # 显示各数据源检索结果统计
    if source_counts:
        _display_source_stats(source_counts)

    if not papers:
        console.print("[yellow]未找到符合条件的论文[/yellow]")
        raise typer.Exit(0)

    # 阶段2 细筛(在 enrich 之后,abstract 已就绪)
    if interest:
        _run_interest_pipeline(
            papers, cfg, interest, interest_threshold, semantic_min_matches,
        )

    # CSV 导出(在阈值过滤之后)
    if csv_auto_generate:
        from papercrawler.cli._helpers import _make_run_csv_filename
        csv_path = str(_make_run_csv_filename(
            must_have_keywords=cfg.interest.must_have,
            papers_count=len(papers),
            output_dir=output_path,
            query=query,
        ))
        console.print(f"[dim]CSV 自动命名: {Path(csv_path).name}[/dim]")

    if csv_path:
        _run_csv_export(papers, csv_path)
        combined_path = Path("results") / "total_papers.csv"
        _run_combined_csv_export(results_root="results", csv_path=combined_path)

    # 展示结果
    _display_results(
        papers,
        fmt,
        query_author=author,
        show_interest=interest,
    )

    if download:
        tasks = _do_download(
            papers, str(output_path), cfg,
            force=force, dry_run=dry_run, force_global=force_global,
        )
        if csv_path and not dry_run:
            downloaded_lookup = {
                t.paper.unique_id: t.status.value == "success"
                for t in tasks
            }
            _run_csv_export(papers, csv_path, downloaded_lookup=downloaded_lookup)
            combined_path = Path("results") / "total_papers.csv"
            _run_combined_csv_export(results_root="results", csv_path=combined_path)