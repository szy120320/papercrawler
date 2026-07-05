"""
search 命令

papercrawler search [OPTIONS]
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
    # 领域感知 ✨ (三态:None=自动按配置,True=强制开,False=强制关)
    interest:         Optional[bool]  = typer.Option(None, "--interest/--no-interest", help="启用领域相关性打分 (None=按 [interest] 配置自动决定)"),
    interest_threshold: float         = typer.Option(0.6,   "--interest-threshold", help="第一阶段粗筛最低分数(title-based, 默认 0.6, <此值过滤掉)。单 must_have 命中 = 0.6,所以单关键词场景默认 0.6 即可通过。"),
    semantic_min_matches: int        = typer.Option(3,    "--semantic-min-matches", help="第二阶段细筛最少命中关键词数(title+abstract+keywords 中命中 ≥ 此值才保留, 默认 3)"),
    csv_path:         Optional[str]  = typer.Option(None,  "--csv",                 help="导出符合条件的结果到 CSV 文件路径 (留空=按 [interest] 配置自动决定)"),
    no_csv:           bool            = typer.Option(False, "--no-csv",              help="明确禁用 CSV 导出"),
    # 运行管理
    name:             Optional[str]  = typer.Option(None,  "--name",                help="本次运行的名称(用作 results/ 子目录名,留空=时间戳)"),
    output_dir:       Optional[str]  = typer.Option(None,  "--output-dir",           help="输出目录(留空=results/<name 或时间戳>/)"),
    # 下载
    download:         bool            = typer.Option(False, "--download",            help="检索后自动下载"),
    force:            bool            = typer.Option(False, "--force",               help="强制重新下载(覆盖本 run DB)"),
    force_global:     bool            = typer.Option(False, "--force-global",        help="强制重新下载(覆盖全局 DB,即使其他 run 已下载)"),
    dry_run:          bool            = typer.Option(False, "--dry-run",             help="模拟运行(不实际下载)"),
    config_path:      Optional[str]  = typer.Option(None,  "--config",              help="配置文件路径"),
    verbose:          bool            = typer.Option(False, "--verbose",             help="详细日志"),
    fmt:              str             = typer.Option("table", "--format",            help="table|json|md"),
):
    """检索论文。可通过关键词、作者名、题目或 DOI 检索。

    领域感知默认行为(选项 A):只要 config/papercrawler.toml 的 [interest] 节
    配置了关键词,就会自动启用 --interest / --csv。
    用 --no-interest / --no-csv 可显式关闭。

    每次运行默认在 ./results/<name 或时间戳>/ 下创建独立子目录,papers /
    metadata.json / CSV / 索引都放在那里。

    跨 run 去重:默认会查 ~/.papercrawler/_download_log.db(全局 DB),
    已下载的论文会自动跳过。用 --force-global 强制重下。
    """
    from papercrawler.models import SearchQuery

    cfg = _setup(config_path, verbose)

    if not any([query, author, title, doi]):
        console.print("[red]错误：至少提供 --query、--author、--title 或 --doi 之一[/red]")
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

    # 领域打分:三态 -> 最终布尔
    if interest is None:
        interest = has_keywords

    # CSV:三态 -> 路径
    # 2026-07-05: 自动生成的 CSV 名改用 "<must_have 关键词>_<时间>_<数量>.csv" 格式
    # 因为数量要等粗筛+细筛后才知道,所以这里不立即生成文件名,留到下面补。
    csv_auto_generate = False
    if no_csv:
        csv_path = None
    elif csv_path is None and interest:
        csv_auto_generate = True
        csv_path = None  # 延迟到知道 len(papers) 时再生成

    # 显示本轮配置
    if has_interest_config:
        flags = []
        if interest: flags.append("interest")
        if csv_auto_generate:
            flags.append("csv→<must_have>_<时间>_<数量>.csv(自动)")
        elif csv_path:
            flags.append(f"csv→{Path(csv_path).name}")
        console.print(f"[dim]领域感知: 启用 {', '.join(flags) or '无'}[/dim]")

    # ============= 执行检索 =============
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
        from papercrawler.classify import DomainFilter
        from papercrawler.metadata.extractor import MetadataExtractor
        from papercrawler.search.manager import SearchManager

        manager = SearchManager(config=cfg)
        papers, source_counts = await manager.search_with_stats(q)

        # 第一阶段:粗筛(只基于 title,快,不做 enrich)
        # 等到第二阶段细筛之前才 enrich,节省 API 调用
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
        # 这样能省掉对明显不相关论文的 S2/CrossRef API 调用
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
    # 2026-07-05: 如果 csv_path 是自动生成模式(刚才 csv_auto_generate=True),
    # 在这里根据 must_have 关键词 + 当前时间 + papers 数量生成最终文件名。
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
        # 同步生成合并 CSV(scan results/ 全部 metadata.json)
        # 2026-07-05: 总 CSV 改名 _all_filtered.csv → total_papers.csv
        combined_path = Path("results") / "total_papers.csv"
        _run_combined_csv_export(results_root="results", csv_path=combined_path)

    # 展示结果(传入 author 用于显示匹配度列,以及是否显示 interest 列)
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
        # 下载完成后用实际状态更新 CSV 的 downloaded 列
        if csv_path and not dry_run:
            downloaded_lookup = {
                t.paper.unique_id: t.status.value == "success"
                for t in tasks
            }
            _run_csv_export(papers, csv_path, downloaded_lookup=downloaded_lookup)
            # 同时更新合并 CSV(2026-07-05 改名 total_papers.csv)
            combined_path = Path("results") / "total_papers.csv"
            _run_combined_csv_export(results_root="results", csv_path=combined_path)