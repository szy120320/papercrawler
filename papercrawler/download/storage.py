"""
文件存储与目录管理
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import aiofiles
from loguru import logger

from papercrawler.models import PaperMetadata
from papercrawler.utils.naming import make_paper_dirname


class PaperStorage:
    """负责为每篇论文创建目录并写入各类文件"""

    def __init__(self, base_output_dir: str):
        self.base = Path(base_output_dir)
        self.base.mkdir(parents=True, exist_ok=True)

    def get_paper_dir(self, paper: PaperMetadata) -> Path:
        """返回论文专属子目录路径（不创建）"""
        dirname = make_paper_dirname(
            year=paper.year,
            first_author_lastname=paper.first_author_lastname,
            title=paper.title,
            doi=paper.doi,
            hash_id=paper.unique_id,
        )
        return self.base / dirname

    def ensure_paper_dir(self, paper: PaperMetadata) -> Path:
        """创建并返回论文专属子目录"""
        paper_dir = self.get_paper_dir(paper)
        paper_dir.mkdir(parents=True, exist_ok=True)
        return paper_dir

    async def save_metadata(self, paper: PaperMetadata, paper_dir: Path) -> Path:
        """将元数据序列化为 JSON 并写入 metadata.json"""
        meta_path = paper_dir / "metadata.json"
        data = paper.model_dump(mode="json")
        async with aiofiles.open(meta_path, "w", encoding="utf-8") as f:
            await f.write(json.dumps(data, ensure_ascii=False, indent=2))
        logger.debug(f"元数据已保存: {meta_path}")
        return meta_path

    async def save_binary(self, content: bytes, paper_dir: Path, filename: str) -> Path:
        """保存二进制文件（PDF 等）"""
        file_path = paper_dir / filename
        async with aiofiles.open(file_path, "wb") as f:
            await f.write(content)
        logger.debug(f"文件已保存: {file_path} ({len(content)/1024:.1f} KB)")
        return file_path

    async def save_text(self, content: str, paper_dir: Path, filename: str) -> Path:
        """保存文本文件（HTML、Markdown 等）"""
        file_path = paper_dir / filename
        async with aiofiles.open(file_path, "w", encoding="utf-8") as f:
            await f.write(content)
        logger.debug(f"文本已保存: {file_path}")
        return file_path

    async def write_index(
        self,
        papers: list[PaperMetadata],
        session_name: str = "",
    ) -> None:
        """在 base 目录下生成 _index.md 和 _index.json"""
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        header = f"# 论文下载索引\n\n**生成时间：** {now}  \n**论文数量：** {len(papers)}\n\n"
        if session_name:
            header += f"**检索词：** {session_name}\n\n"

        lines = [
            header,
            "| # | 标题 | 作者 | 年份 | 期刊 | DOI | 获取状态 |\n",
            "|---|------|------|------|------|-----|----------|\n",
        ]
        json_list = []
        for i, p in enumerate(papers, 1):
            authors_str = "; ".join(a.name for a in p.authors[:3])
            if len(p.authors) > 3:
                authors_str += " et al."
            doi_link = f"[{p.doi}](https://doi.org/{p.doi})" if p.doi else "—"
            title_trunc = p.title[:60] + "…" if len(p.title) > 60 else p.title
            paper_dir = self.get_paper_dir(p)
            rel_path = paper_dir.relative_to(self.base)
            title_link = f"[{title_trunc}](./{rel_path}/paper.md)"
            lines.append(
                f"| {i} | {title_link} | {authors_str} | "
                f"{p.year or '—'} | {p.journal or '—'} | "
                f"{doi_link} | {p.access_status_display()} |\n"
            )
            json_list.append({
                "index": i,
                "title": p.title,
                "doi": p.doi,
                "year": p.year,
                "access_status": p.access_status.value,
                "dir": str(rel_path),
            })

        async with aiofiles.open(self.base / "_index.md", "w", encoding="utf-8") as f:
            await f.writelines(lines)

        async with aiofiles.open(self.base / "_index.json", "w", encoding="utf-8") as f:
            await f.write(json.dumps(json_list, ensure_ascii=False, indent=2))

        logger.info(f"索引文件已生成: {self.base / '_index.md'}")
