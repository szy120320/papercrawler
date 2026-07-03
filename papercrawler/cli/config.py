"""
config 子命令 — 配置管理

papercrawler config init  生成默认配置文件
papercrawler config show  显示当前配置
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer
from rich import print as rprint

from papercrawler.cli._helpers import console

app = typer.Typer(help="配置管理")


@app.command("init")
def config_init():
    """在 ./config/ 目录生成默认配置文件 papercrawler.toml。"""
    # 优先用 ./config/papercrawler.toml.example(项目本地),fallback 到旧的根目录位置
    example_candidates = [
        Path(__file__).parent.parent.parent / "config" / "papercrawler.toml.example",
        Path(__file__).parent.parent.parent / "papercrawler.toml.example",  # 向后兼容
    ]
    example = next((p for p in example_candidates if p.exists()), None)

    config_dir = Path("config")
    config_dir.mkdir(parents=True, exist_ok=True)
    dest = config_dir / "papercrawler.toml"

    if dest.exists():
        overwrite = typer.confirm(f"{dest} 已存在,是否覆盖?", default=False)
        if not overwrite:
            raise typer.Exit(0)

    if example:
        import shutil
        shutil.copy(example, dest)
    else:
        # 内嵌最小配置
        dest.write_text(
            "[api_keys]\nunpaywall_email = \"your@email.com\"\n",
            encoding="utf-8",
        )
    console.print(f"[green]配置文件已生成: {dest.resolve()}[/green]")
    console.print("[yellow]请编辑该文件,至少填写 unpaywall_email[/yellow]")


@app.command("show")
def config_show(
    config_path: Optional[str] = typer.Option(None, "--config"),
):
    """显示当前配置。"""
    from papercrawler.config import load_config
    cfg = load_config(config_path)
    rprint(cfg.model_dump())