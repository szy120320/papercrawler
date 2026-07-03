"""
papercrawler.cli.__main__ — 允许 `python -m papercrawler.cli` 调用

等效于 `papercrawler` console script。
"""

from papercrawler.cli import app

if __name__ == "__main__":
    app()