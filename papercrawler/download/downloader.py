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
        force_global: bool = False,
    ):
        """
        初始化下载器。

        Args:
            output_dir: 本次 run 的输出目录
            config: 全局配置
            db_path: 自定义 DB 路径(测试用)
            force_global: 若为 True,跳过全局 DB 检查,所有论文都重下
                          (即便它们已在其他 run 中下过)
        """
        from papercrawler.paths import GLOBAL_DB_PATH

        self.config = config or get_config()
        self.storage = PaperStorage(output_dir)
        self.force_global = force_global

        # 全局 DB(用户级,跨 run 共享) — 用于去重检查
        self.db = DownloadDatabase(
            db_path if db_path else str(GLOBAL_DB_PATH)
        )

        # 本次 run 的 DB(只记录本 run 下载的) — 用于 `history list --output-dir X`
        run_db_path = str(Path(output_dir) / "_download_log.db")
        self.run_db = DownloadDatabase(run_db_path)

        self.converter = MarkItDownConverter(
            enabled=self.config.markitdown.enabled
        )
        self._semaphore = asyncio.Semaphore(self.config.download.max_concurrent)
        # Sci-Hub 下载器 — 始终自动初始化，metadata_only 论文自动 fallback
        self._scihub = SciHubDownloader(
            proxy=self.config.scihub.proxy,
            mirror=self.config.scihub.mirror,
            headless=self.config.scihub.headless,
        )

        # 本次 run 的名字(从 output_dir 推,如 "2026-07-03_xxx")
        self.run_name = Path(output_dir).name

    # ------------------------------------------------------------------
    # 公共接口
    # ------------------------------------------------------------------

    async def download_all(
        self,
        papers: list[PaperMetadata],
        force: bool = False,
        dry_run: bool = False,
        force_global: Optional[bool] = None,
    ) -> list[DownloadTask]:
        """
        并发下载论文列表,返回 DownloadTask 结果列表。

        Args:
            papers: 论文列表
            force: 强制重下(覆盖本 run DB)
            dry_run: 模拟,不实际下载
            force_global: 强制重下(覆盖全局 DB 记录,即便其他 run 已下载)
                          None=用 self.force_global
        """
        if force_global is not None:
            self.force_global = force_global

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

        # 跨 run 去重:全局 DB 命中就跳过(除非 force 或 force_global)
        if not force and not self.force_global:
            existing = self.db.find_existing(paper.doi, paper.unique_id)
            if existing:
                run_hint = f" (run: {existing.download_run})" if existing.download_run else ""
                task.mark_skipped(f"already downloaded{run_hint}")
                logger.info(
                    f"跳过(已下载{run_hint}): {paper.title[:60]}"
                )
                # 仍然写一条 skipped 记录到本 run DB,方便 `history list --output-dir X` 查
                self.run_db.upsert(
                    doi=paper.doi,
                    hash_id=paper.unique_id,
                    title=paper.title,
                    authors=[a.name for a in paper.authors],
                    year=paper.year,
                    journal=paper.journal,
                    access_status=paper.access_status.value,
                    download_status=task.status.value,
                    output_dir=existing.output_dir or "",
                    error_msg=task.error_msg,
                    download_run=existing.download_run,
                )
                return task

        if dry_run:
            logger.info(f"[DRY-RUN] 将下载: {paper.title[:60]} [{paper.access_status.value}]")
            task.status_str = "dry_run"  # type: ignore
            return task

        async with self._semaphore:
            paper_dir = self.storage.ensure_paper_dir(paper)
            paper.downloaded_at = datetime.utcnow()

            # 记录首选下载方式,失败时记入 error_msg
            primary_failed = False
            primary_err = ""

            # ── 第一阶段:按 access_status 走合法渠道 ──────────────
            try:
                if paper.access_status in (
                    AccessStatus.OPEN_ACCESS_PDF,
                    AccessStatus.OPEN_ACCESS_PREPRINT,
                ):
                    await self._download_pdf(paper, paper_dir)
                elif paper.access_status == AccessStatus.OPEN_ACCESS_HTML:
                    await self._download_html(paper, paper_dir)
                else:
                    # metadata_only / unknown — 不尝试 OA,直接走 Sci-Hub
                    primary_failed = True
                    primary_err = "metadata_only(无 OA 链接)"
            except httpx.HTTPError as e:
                # 网络层错误(timeout / connect / read error / SSL)
                primary_failed = True
                primary_err = f"network: {type(e).__name__}: {e}"
                logger.opt(exception=True).warning(
                    f"[OA 网络失败] {paper.title[:50]} — {primary_err}; 准备 Sci-Hub fallback"
                )
                self._cleanup_incomplete(task)
            except (OSError, ValueError) as e:
                # OSError: 文件读写错;ValueError: oa_url 为空 等业务异常
                primary_failed = True
                primary_err = f"{type(e).__name__}: {e}"
                logger.opt(exception=True).warning(
                    f"[OA 失败] {paper.title[:50]} — {primary_err}; 准备 Sci-Hub fallback"
                )
                self._cleanup_incomplete(task)
            except Exception as e:
                # 兜底:未预期异常,带 traceback 便于排查
                primary_failed = True
                primary_err = f"unexpected: {type(e).__name__}: {e}"
                logger.opt(exception=True).error(
                    f"[OA 未预期异常] {paper.title[:50]} — {primary_err}; 准备 Sci-Hub fallback"
                )
                self._cleanup_incomplete(task)

            # ── 第二阶段:首选失败时统一走 Sci-Hub 兜底 ───────────
            scihub_ok = False
            if primary_failed:
                if self.config.scihub.enabled and self._scihub.is_available():
                    scihub_ok = await self._try_scihub(paper, paper_dir)
                elif not self.config.scihub.enabled:
                    logger.debug(
                        f"[scihub] 配置已禁用,跳过 fallback: {paper.title[:50]}"
                    )

            # ── 第三阶段:Sci-Hub 也失败,才回退到仅元数据 ─────────
            if primary_failed and not scihub_ok:
                await self._save_metadata_only(paper, paper_dir)

            # ── 设置最终状态 ────────────────────────────────────
            if not primary_failed or scihub_ok:
                task.mark_success()
                if scihub_ok:
                    logger.success(
                        f"[通过 Sci-Hub 完成] {paper.title[:60]}"
                    )
                else:
                    logger.success(f"完成: {paper.title[:60]}")
            else:
                # primary 失败 + scihub 失败 → 整体失败
                task.mark_failed(f"OA 失败: {primary_err}; Sci-Hub 兜底也失败")
                logger.error(
                    f"[全部失败] {paper.title[:60]} — OA: {primary_err}; Sci-Hub 也未命中"
                )

            # 同时写入全局 DB 和本 run DB
            upsert_kwargs = dict(
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
                download_run=self.run_name,
            )
            self.db.upsert(**upsert_kwargs)     # 全局
            self.run_db.upsert(**upsert_kwargs)  # 本 run

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
        通过 Sci-Hub 兜底下载论文 PDF(两阶段:DOI → title)。

        调用方应已经确认 config.scihub.enabled = True 且 scidownl 可用。
        成功时更新 access_status、files,并运行 MarkItDown 转换。
        返回 True 表示成功，False 表示失败(调用方应回退到仅元数据)。
        """
        assert self._scihub is not None

        # ── 第一阶段:按 DOI 抓取(精确匹配) ────────────────────────
        success = False
        if paper.doi:
            logger.info(f"[scihub 阶段1] 按 DOI 抓取: {paper.doi}")
            success = await self._scihub.download(
                doi=paper.doi,
                dest_dir=paper_dir,
                filename="paper.pdf",
            )
            if success:
                logger.success(f"[scihub 阶段1 成功] DOI 抓取: {paper.doi}")

        # ── 第二阶段:按 title 二次抓取(模糊匹配) ──────────────────
        if not success and paper.title:
            logger.info(
                f"[scihub 阶段2] DOI 抓取失败,按 title 二次抓取: {paper.title[:60]}"
            )
            success = await self._scihub.download_by_title(
                title=paper.title,
                dest_dir=paper_dir,
                filename="paper.pdf",
            )
            if success:
                logger.success(f"[scihub 阶段2 成功] title 抓取: {paper.title[:60]}")

        if not success:
            logger.warning(
                f"[scihub 全失败] DOI + title 都未命中: {paper.title[:60]}"
            )
            return False

        pdf_path = paper_dir / "paper.pdf"
        if not pdf_path.exists():
            return False

        # ── 把 access_status 升级为 OA-PDF(已经是真 PDF 了) ──────
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
            except httpx.HTTPStatusError as e:
                # 4xx/5xx — 区分一下,403/404 通常不必重试
                last_exc = e
                if e.response.status_code in (403, 404, 410):
                    logger.opt(exception=True).debug(
                        f"下载终止(状态 {e.response.status_code}): {url}"
                    )
                    raise
                wait = 2 ** attempt
                logger.opt(exception=True).debug(
                    f"下载重试 {attempt+1}/{retry},等待 {wait}s: {e}"
                )
                await asyncio.sleep(wait)
            except httpx.HTTPError as e:
                # 网络层错误(timeout / connect / read error / SSL)
                last_exc = e
                wait = 2 ** attempt
                logger.opt(exception=True).debug(
                    f"下载网络错误 {attempt+1}/{retry},等待 {wait}s: {type(e).__name__}: {e}"
                )
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
