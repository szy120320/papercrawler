"""
history 子命令 — 查询下载历史

papercrawler history list [OPTIONS]
papercrawler history stats [OPTIONS]
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer
from rich.table import Table

from papercrawler.cli._helpers import console

app = typer.Typer(help="查询下载历史记录")


@app.command("list")
def history_list(
    status:     Optional[str] = typer.Option(None, "--status", help="过滤状态: success|failed|skipped"),
    limit:      int           = typer.Option(50,   "--limit",  help="显示条数"),
    output_dir: Optional[str] = typer.Option(None, "--output-dir", help="仅看本 run 的记录(默认看全局 ~/.papercrawler/_download_log.db)"),
):
    """列出下载记录。默认查全局 DB(跨 run)。"""
    from papercrawler.download.database import DownloadDatabase
    from papercrawler.paths import GLOBAL_DB_PATH

    if output_dir is None:
        db_path = str(GLOBAL_DB_PATH)
        scope_hint = "[全局]"
    else:
        db_path = str(Path(output_dir) / "_download_log.db")
        scope_hint = f"[run: {output_dir}]"

    if not Path(db_path).exists():
        console.print(f"[yellow]暂无下载记录(数据库不存在: {db_path})[/yellow]")
        return

    db = DownloadDatabase(db_path)
    records = db.list_records(status=status, limit=limit)

    if not records:
        console.print("[yellow]未找到符合条件的记录[/yellow]")
        return

    table = Table(title=f"下载记录 {scope_hint}（{status or '全部'}，前{limit}条）")
    table.add_column("#",      style="dim")
    table.add_column("标题",   max_width=36)
    table.add_column("年份",   justify="center")
    table.add_column("状态",   justify="center")
    table.add_column("Run",    max_width=18)
    table.add_column("OA",     justify="center")
    table.add_column("DOI",    max_width=22)

    status_color = {"success": "green", "failed": "red", "skipped": "yellow", "pending": "cyan"}
    for r in records:
        color = status_color.get(r["download_status"], "white")
        table.add_row(
            str(r["id"]),
            r["title"][:36],
            str(r["year"] or "—"),
            f"[{color}]{r['download_status']}[/{color}]",
            (r.get("download_run") or "—")[:18],
            r["access_status"] or "—",
            (r["doi"] or "—")[:22],
        )
    console.print(table)


@app.command("stats")
def history_stats(
    output_dir: Optional[str] = typer.Option(None, "--output-dir", help="仅看本 run(默认全局)"),
):
    """显示下载统计摘要。默认查全局 DB。"""
    from papercrawler.download.database import DownloadDatabase
    from papercrawler.paths import GLOBAL_DB_PATH

    if output_dir is None:
        db_path = str(GLOBAL_DB_PATH)
        scope_hint = "[全局]"
    else:
        db_path = str(Path(output_dir) / "_download_log.db")
        scope_hint = f"[run: {output_dir}]"

    if not Path(db_path).exists():
        console.print(f"[yellow]暂无下载记录({db_path} 不存在)[/yellow]")
        return

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