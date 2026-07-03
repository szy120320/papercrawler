"""
convert 命令 — 将 PDF/HTML/DOCX 等转为 Markdown

papercrawler convert FILE_PATH [OPTIONS]
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer

from papercrawler.cli._helpers import _setup, console


def cmd_convert(
    file_path:  str           = typer.Argument(..., help="要转换的文件路径（PDF/HTML/DOCX）"),
    output:     Optional[str] = typer.Option(None, "--output", "-o", help="输出 .md 文件路径"),
    config_path: Optional[str] = typer.Option(None, "--config",      help="配置文件路径"),
    verbose:    bool           = typer.Option(False, "--verbose",     help="详细日志"),
):
    """将已有 PDF/HTML/DOCX 文件转换为 Markdown。"""
    from papercrawler.convert.markitdown_converter import MarkItDownConverter

    _setup(config_path, verbose)
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