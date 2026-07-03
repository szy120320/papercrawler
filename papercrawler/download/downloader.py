"""
并发下载器 — 全文 PDF/HTML 下载 + 元数据保存
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path
from typing import Optional

import httpx
from loguru import logger
from rich.progress import (
    BarColumn, DownloadColumn, Progress, SpinnerColumn,
    TaskProgressColumn, TextColumn, TimeElapsedColumn,
)

from papercrawler.config import AppConfig, get_config
from papercrawler.convert.markitdown_converter import MarkItDownConverter
from papercrawler.download.database import DownloadDatabase
from papercrawler.download.scihub_downloader import SciHubDownloader
from papercrawler.download.storage import PaperStorage
from papercrawler.models import AccessStatus, DownloadTask, PaperMetadata


class PaperDownloader:
    """
    并发下载论文，根据 AccessStatus 决定：
    - OA 论文：下载原始文件 → 保存 → MarkItDown 转换 → 保存 md
    - 仅元数据：保存元数据 JSON → 生成仅含摘要的 md
    """

    def __init__(
        self,
        output_dir: str,
        config: Optional[AppConfig] = None,
        db_path: Optional[str] = None,
    ):
        self.config = config or get_config()
        self.storage = PaperStorage(output_dir)
        db = db_path or str(Path(output_dir) / "_download_log.db")
        self.db = DownloadDatabase(db)
        self.converter = MarkItDownConverter(
            enabled=self.config.markitdown.enabled
        )
        self._semaphore = asyncio.Semaphore(self.config.download.max_concurrent)
        # Sci-Hub 下载器（仅在 config.scihub.enabled=True 时激活）
        self._scihub: Optional[SciHubDownloader] = None
        if self.config.scihub.enabled:
            self._scihub = SciHubDownloader(proxy=self.config.scihub.proxy)

    # ------------------------------------------------------------------
    # 公共接口
    # ------------------------------------------------------------------

    async def download_all(
        self,
        papers: list[PaperMetadata],
        force: bool = False,
        dry_run: bool = False,
    ) -> list[DownloadTask]:
        """
        并发下载论文列表，返回 DownloadTask 结果列表。
        """
        tasks = [DownloadTask(paper=p, output_dir=str(self.storage.base)) for p in papers]

        with Progress(
            SpinnerColumn(),
            TextColumn("[bold blue]{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            TimeElapsedColumn(),
        ) as progress:
            prog_task = progress.add_task("下载论文...", total=len(tasks))

            async def _do(task: DownloadTask) -> DownloadTask:
                result = await self._download_one(task, force=force, dry_run=dry_run)
                progress.advance(prog_task)
                return result

            results = await asyncio.gather(*[_do(t) for t in tasks])

        return list(results)

    async def download_one(
        self,
        paper: PaperMetadata,
        force: bool = False,
        dry_run: bool = False,
    ) -> DownloadTask:
        task = DownloadTask(paper=paper, output_dir=str(self.storage.base))
        return await self._download_one(task, force=force, dry_run=dry_run)

    # ------------------------------------------------------------------
    # 内部实现
    # ------------------------------------------------------------------

    async def _download_one(
        self,
        task: DownloadTask,
        force: bool = False,
        dry_run: bool = False,
    ) -> DownloadTask:
        paper = task.paper

        # 断点续传检查
        if not force and self.db.is_downloaded(paper.doi, paper.unique_id):
            task.mark_skipped("already downloaded")
            logger.info(f"跳过（已下载）: {paper.title[:60]}")
            return task

        if dry_run:
            logger.info(f"[DRY-RUN] 将下载: {paper.title[:60]} [{paper.access_status.value}]")
            task.status_str = "dry_run"  # type: ignore
            return task

        async with self._semaphore:
            try:
                paper_dir = self.storage.ensure_paper_dir(paper)
                paper.downloaded_at = datetime.utcnow()

                if paper.access_status in (
                    AccessStatus.OPEN_ACCESS_PDF,
                    AccessStatus.OPEN_ACCESS_PREPRINT,
                ):
                    await self._download_pdf(paper, paper_dir)
                elif paper.access_status == AccessStatus.OPEN_ACCESS_HTML:
                    await self._download_html(paper, paper_dir)
                else:
                    # 尝试 Sci-Hub fallback（须显式启用且论文有 DOI）
                    scihub_ok = False
                    if self._scihub and paper.doi:
                        scihub_ok = await self._try_scihub(paper, paper_dir)
                    if not scihub_ok:
                        # 仅元数据
                        await self._save_metadata_only(paper, paper_dir)

                task.mark_success()
                logger.success(f"完成: {paper.title[:60]}")

            except Exception as e:
                task.mark_failed(str(e))
                logger.error(f"下载失败 (NET_ERROR): {paper.title[:60]} — {e}")
                # 清理不完整文件
                self._cleanup_incomplete(task)

            finally:
                # 写入数据库
                self.db.upsert(
                    doi=paper.doi,
                    hash_id=paper.unique_id,
                    title=paper.title,
                    authors=[a.name for a in paper.authors],
                    year=paper.year,
                    journal=paper.journal,
                    access_status=paper.access_status.value,
                    download_status=task.status.value,
                    output_dir=str(paper_dir) if task.status.value == "success" else "",
                    error_msg=task.error_msg,
                )

        return task

    async def _download_pdf(self, paper: PaperMetadata, paper_dir: Path) -> None:
        url = paper.oa_url
        if not url:
            raise ValueError("oa_url 为空，无法下载 PDF")

        content = await self._fetch_bytes(url)
        pdf_path = await self.storage.save_binary(content, paper_dir, "paper.pdf")
        paper.files["pdf"] = "paper.pdf"

        if self.config.markitdown.enabled:
            md_text = self.converter.convert_file(str(pdf_path))
            full_md = self._build_markdown(paper, md_text, source_file="paper.pdf")
        else:
            full_md = self._build_markdown(paper, None, source_file="paper.pdf")

        await self.storage.save_text(full_md, paper_dir, "paper.md")
        paper.files["markdown"] = "paper.md"
        await self.storage.save_metadata(paper, paper_dir)

    async def _download_html(self, paper: PaperMetadata, paper_dir: Path) -> None:
        url = paper.oa_url
        if not url:
            raise ValueError("oa_url 为空，无法下载 HTML")

        html_content = await self._fetch_text(url)
        html_path = await self.storage.save_text(html_content, paper_dir, "paper.html")
        paper.files["html"] = "paper.html"

        if self.config.markitdown.enabled:
            md_text = self.converter.convert_file(str(html_path))
            full_md = self._build_markdown(paper, md_text, source_file="paper.html")
        else:
            full_md = self._build_markdown(paper, None, source_file="paper.html")

        await self.storage.save_text(full_md, paper_dir, "paper.md")
        paper.files["markdown"] = "paper.md"
        await self.storage.save_metadata(paper, paper_dir)

    async def _save_metadata_only(self, paper: PaperMetadata, paper_dir: Path) -> None:
        full_md = self._build_markdown(paper, None, metadata_only=True)
        await self.storage.save_text(full_md, paper_dir, "paper.md")
        paper.files["markdown"] = "paper.md"
        await self.storage.save_metadata(paper, paper_dir)

    async def _try_scihub(self, paper: PaperMetadata, paper_dir: Path) -> bool:
        """
        尝试通过 Sci-Hub 下载论文 PDF。
        成功时更新 access_status 并运行 MarkItDown 转换。
        返回 True 表示成功，False 表示失败（调用方应回退到仅元数据）。
        """
        assert self._scihub is not None
        success = await self._scihub.download(
            doi=paper.doi,  # type: ignore[arg-type]
            dest_dir=paper_dir,
            filename="paper.pdf",
        )
        if not success:
            logger.debug(f"[scihub] 下载失败，回退至仅元数据: {paper.title[:60]}")
            return False

        pdf_path = paper_dir / "paper.pdf"
        if not pdf_path.exists():
            return False

        paper.access_status = AccessStatus.OPEN_ACCESS_PDF
        paper.files["pdf"] = "paper.pdf"

        if self.config.markitdown.enabled:
            md_text = self.converter.convert_file(str(pdf_path))
            full_md = self._build_markdown(paper, md_text, source_file="paper.pdf")
        else:
            full_md = self._build_markdown(paper, None, source_file="paper.pdf")

        await self.storage.save_text(full_md, paper_dir, "paper.md")
        paper.files["markdown"] = "paper.md"
        await self.storage.save_metadata(paper, paper_dir)
        logger.success(f"[scihub] 下载完成: {paper.title[:60]}")
        return True

    # ------------------------------------------------------------------
    # HTTP 工具
    # ------------------------------------------------------------------

    async def _fetch_bytes(self, url: str, retry: int = 3) -> bytes:
        cfg = self.config.download
        headers = {"User-Agent": cfg.user_agent}
        last_exc: Exception = RuntimeError("未知错误")
        for attempt in range(retry):
            try:
                async with httpx.AsyncClient(
                    timeout=httpx.Timeout(cfg.connect_timeout, read=cfg.read_timeout),
                    follow_redirects=True,
                ) as client:
                    resp = await client.get(url, headers=headers)
                    resp.raise_for_status()
                    return resp.content
            except Exception as e:
                last_exc = e
                wait = 2 ** attempt
                logger.debug(f"下载重试 {attempt+1}/{retry}，等待 {wait}s: {e}")
                await asyncio.sleep(wait)
        raise last_exc

    async def _fetch_text(self, url: str) -> str:
        content = await self._fetch_bytes(url)
        return content.decode("utf-8", errors="replace")

    # ------------------------------------------------------------------
    # Markdown 生成
    # ------------------------------------------------------------------

    def _build_markdown(
        self,
        paper: PaperMetadata,
        body: Optional[str],
        source_file: str = "",
        metadata_only: bool = False,
    ) -> str:
        authors_yaml = "\n".join(f'  - "{a.name}"' for a in paper.authors)
        kw_yaml = "\n".join(f'  - "{k}"' for k in (paper.keywords or []))
        abstract_yaml = (paper.abstract or "").replace("\n", " ")
        doi_url = paper.doi_url or ""
        now = paper.downloaded_at.isoformat() if paper.downloaded_at else ""

        front_matter = f"""---
title: "{paper.title.replace('"', "'")}"
authors:
{authors_yaml or '  - "Unknown"'}
year: {paper.year or 'null'}
journal: "{paper.journal or ''}"
doi: "{paper.doi or ''}"
url: "{doi_url}"
access_status: "{paper.access_status.value}"
keywords:
{kw_yaml or '  []'}
abstract: |
  {abstract_yaml}
downloaded_at: "{now}"
source_file: "{source_file}"
---
"""

        authors_display = "; ".join(a.name for a in paper.authors[:5])
        if len(paper.authors) > 5:
            authors_display += " et al."
        doi_link = f"[{paper.doi}](https://doi.org/{paper.doi})" if paper.doi else "—"

        meta_table = f"""
# {paper.title}

## 元数据摘要

| 字段 | 内容 |
|------|------|
| **作者** | {authors_display or '—'} |
| **期刊** | {paper.journal or '—'} |
| **年份** | {paper.year or '—'} |
| **DOI** | {doi_link} |
| **获取状态** | {paper.access_status_display()} |

## 摘要（Abstract）

{paper.abstract or '_暂无摘要_'}

---
"""

        if metadata_only or body is None:
            notice = ""
            if metadata_only:
                notice = f"""
> **注意：** 本论文为收费期刊，仅保存元数据与摘要。
> 如需获取全文，请通过以下链接访问：[出版商链接]({doi_url or '#'})
"""
            return front_matter + meta_table + notice
        else:
            return front_matter + meta_table + f"\n## 正文内容\n\n{body}\n"

    def _cleanup_incomplete(self, task: DownloadTask) -> None:
        """下载失败时清理不完整文件（不删除文件夹，仅记录）"""
        logger.debug(f"下载失败，保留目录以供排查: {task.output_dir}")
