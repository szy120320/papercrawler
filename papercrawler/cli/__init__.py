"""
papercrawler.cli — CLI 入口包

命令结构:
  papercrawler search      检索论文
  papercrawler download    通过 DOI/URL 直接下载
  papercrawler batch       批量任务
  papercrawler convert     将已有文件转换为 Markdown
  papercrawler history     查询下载历史
  papercrawler config      配置管理

子模块:
  _helpers.py  共享工具函数
  search.py    search 命令
  download.py  download + batch 命令
  convert.py   convert 命令
  history.py   history 子 typer
  config.py    config 子 typer
"""

from __future__ import annotations

import typer

from papercrawler.cli._helpers import console  # 复用 console / logger 配置

app = typer.Typer(
    name="paper-dl",
    help="学术论文检索与下载工具。支持 OA 全文下载、Sci-Hub 兜底、MarkItDown 格式转换、领域相关性打分。",
    no_args_is_help=True,
    pretty_exceptions_enable=False,
)


def _register_subcommands() -> None:
    """集中注册所有子命令(避免循环导入,延迟到调用时)"""
    from papercrawler.cli import search, download, convert, history, config  # noqa: F401

    # search / download / batch / convert — 直接挂到 app
    app.command("search")(search.cmd_search)
    app.command("download")(download.cmd_download)
    app.command("batch")(download.cmd_batch)
    app.command("convert")(convert.cmd_convert)

    # history / config — 子 typer
    app.add_typer(history.app, name="history")
    app.add_typer(config.app, name="config")


_register_subcommands()


if __name__ == "__main__":
    app()