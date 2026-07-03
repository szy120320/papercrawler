"""
recategorize 命令 — 对已下载论文重新跑领域分类

papercrawler recategorize [OPTIONS]
"""

from __future__ import annotations

import json as _json
from pathlib import Path
from typing import Optional

import typer

from loguru import logger

from papercrawler.cli._helpers import (
    _run_combined_csv_export, _setup, console,
)


def cmd_recategorize(
    output_dir: str = typer.Option(
        "./results", "--output-dir", "-o",
        help="要重新分类的论文所在目录(默认递归扫描 ./results/ 下的所有子目录)",
    ),
    interest_threshold: float = typer.Option(
        0.0, "--interest-threshold",
        help="最低领域相关性分数(0.0=只标注,>0=同时过滤)",
    ),
    config_path: Optional[str] = typer.Option(None, "--config", help="配置文件路径"),
    verbose:     bool          = typer.Option(False, "--verbose", help="详细日志"),
    dry_run:     bool          = typer.Option(False, "--dry-run", help="只预览不写回"),
):
    """
    对已下载论文重新跑 [interest] 评分与分类,写回 metadata.json。

    默认扫描 ./results/ 下的所有子目录,找出含 metadata.json 的论文目录。
    用法:
        papercrawler recategorize
        papercrawler recategorize -o ./results/2026-07-03_xxx
        papercrawler recategorize --interest-threshold 0.5
    """
    from papercrawler.classify import Categorizer, DomainFilter
    from papercrawler.models import PaperMetadata

    cfg = _setup(config_path, verbose)

    if not cfg.interest.must_have and not cfg.interest.should_have and not cfg.interest.exclude and not cfg.interest.categories:
        console.print("[red]错误: [interest] 节未配置任何关键词或分类,无法重新分类[/red]")
        raise typer.Exit(1)

    base = Path(output_dir)
    if not base.exists():
        console.print(f"[red]目录不存在: {base}[/red]")
        raise typer.Exit(1)

    # 找出所有含 metadata.json 的子目录
    meta_files = list(base.rglob("metadata.json"))
    if not meta_files:
        console.print(f"[yellow]在 {base} 下未找到任何 metadata.json[/yellow]")
        return

    console.print(f"[cyan]找到 {len(meta_files)} 篇已下载论文,开始重新分类...[/cyan]")

    df = DomainFilter(cfg.interest)
    cat = Categorizer(cfg.interest)

    stats = {
        "total": len(meta_files),
        "categorized": 0,
        "filtered_out": 0,
        "errors": 0,
    }

    filtered_papers: list[PaperMetadata] = []

    for meta_path in meta_files:
        try:
            data = _json.loads(meta_path.read_text(encoding="utf-8"))
            paper = PaperMetadata(**data)

            # 重新打分 + 分类
            paper.interest_score = df.score(paper)
            paper.categories = cat.categorize(paper)

            if paper.categories:
                stats["categorized"] += 1

            if interest_threshold > 0.0 and (paper.interest_score or 0.0) < interest_threshold:
                stats["filtered_out"] += 1
            else:
                filtered_papers.append(paper)

            # 写回 metadata.json
            if not dry_run:
                meta_path.write_text(
                    _json.dumps(paper.model_dump(mode="json"), ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )

        except (ValueError, KeyError, TypeError, OSError) as e:
            # ValueError: JSON / Pydantic 验证;KeyError / TypeError: 字段错;OSError: 读/写文件错
            stats["errors"] += 1
            console.print(f"[red]处理 {meta_path} 失败: {e}[/red]")
            logger.opt(exception=True).debug(f"[recategorize] {meta_path}")
        except Exception as e:
            # 兜底:未预期的异常,带 traceback 便于排查
            stats["errors"] += 1
            console.print(f"[red]处理 {meta_path} 时发生未预期异常: {e}[/red]")
            logger.opt(exception=True).error(f"[recategorize] {meta_path}")

    # 输出统计
    console.print(f"\n[bold]重新分类完成:[/bold]")
    console.print(f"  总数:     {stats['total']}")
    console.print(f"  分类:     [green]{stats['categorized']}[/green] 篇命中至少一个分类")
    if interest_threshold > 0:
        console.print(f"  阈值过滤: [yellow]{stats['filtered_out']}[/yellow] 篇低于 {interest_threshold} 被剔除")
        console.print(f"  保留:     [cyan]{len(filtered_papers)}[/cyan] 篇")
    if stats["errors"]:
        console.print(f"  错误:     [red]{stats['errors']}[/red]")
    if dry_run:
        console.print(f"\n[yellow]--dry-run 模式,未写回任何文件[/yellow]")

    # 同步全局 DB(把扫描到的论文记录进 ~/.papercrawler/_download_log.db)
    if not dry_run:
        from papercrawler.download.database import DownloadDatabase
        from papercrawler.paths import GLOBAL_DB_PATH
        global_db = DownloadDatabase(str(GLOBAL_DB_PATH))
        new_to_global = 0
        for meta_path in meta_files:
            try:
                data = _json.loads(meta_path.read_text(encoding="utf-8"))
                paper = PaperMetadata(**data)
                # 用 metadata.json 所在目录名作为 run name
                run_name = meta_path.parent.name
                global_db.upsert(
                    doi=paper.doi,
                    hash_id=paper.unique_id,
                    title=paper.title,
                    authors=[a.name for a in paper.authors],
                    year=paper.year,
                    journal=paper.journal,
                    access_status=paper.access_status.value,
                    download_status="success",  # 假设迁移的都是已下载的
                    output_dir=str(meta_path.parent),
                    error_msg=None,
                    download_run=run_name,
                )
                new_to_global += 1
            except (ValueError, OSError):
                # JSON / Pydantic / 文件读错 — 都跳过,不阻塞整体流程
                logger.opt(exception=True).debug(
                    f"[recategorize] 同步全局 DB 跳过 {meta_path}"
                )
        console.print(f"\n[cyan]全局 DB 已同步: {new_to_global} 条(写入 {GLOBAL_DB_PATH})[/cyan]")

    # 如果有阈值过滤,导出筛选后的 CSV 到 output_dir
    if interest_threshold > 0.0 and filtered_papers and not dry_run:
        from papercrawler.export.csv_writer import CSVWriter
        csv_out = base / f"_recategorized_above_{interest_threshold:.2f}.csv"
        CSVWriter().write(filtered_papers, csv_out)
        console.print(f"\n[green]已导出筛选结果: {csv_out}[/green]")

    # 不管有没有阈值过滤,都同步生成合并 CSV
    if not dry_run:
        _run_combined_csv_export(results_root="results", csv_path=Path("results") / "_all_filtered.csv")