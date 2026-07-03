"""
Sci-Hub 下载封装器（通过 scidownl 库）

⚠️  法律免责声明 / Legal Disclaimer ⚠️
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Sci-Hub 未经版权持有人授权分发学术论文。在美国、欧盟及部分其他
地区，访问 Sci-Hub 可能构成版权侵权。多家主要出版商已在美国法院
获得针对 Sci-Hub 的默认判决。

请在使用本模块前：
  1. 了解并遵守您所在地区的版权法律；
  2. 确认您的机构网络使用政策；
  3. 优先通过合法渠道（Unpaywall、PubMed Central、机构图书馆等）
     获取论文全文。

本功能需在 paper_dl.toml 中显式启用 [scihub] enabled = true，
且需额外安装: pip install paper-dl[scihub]
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

from __future__ import annotations

import asyncio
import os
import tempfile
from pathlib import Path
from typing import Optional

from loguru import logger


class SciHubDownloader:
    """
    通过 scidownl 库异步下载 Sci-Hub 论文 PDF。

    依赖:
        pip install scidownl>=1.0

    使用前提:
        config.scihub.enabled = True（默认关闭）
    """

    def __init__(self, proxy: str = ""):
        self.proxy: Optional[dict] = None
        if proxy:
            self.proxy = {"http": proxy, "https": proxy}

    @staticmethod
    def is_available() -> bool:
        """检查 scidownl 是否已安装"""
        try:
            import scidownl  # noqa: F401
            return True
        except ImportError:
            return False

    async def download(
        self,
        doi: str,
        dest_dir: str | Path,
        filename: str = "paper.pdf",
    ) -> bool:
        """
        通过 Sci-Hub 下载指定 DOI 的论文 PDF。

        参数:
            doi: 论文 DOI（如 "10.1038/nature12373"）
            dest_dir: 目标目录
            filename: 保存的文件名（默认 paper.pdf）

        返回:
            True 表示下载成功，False 表示失败
        """
        if not self.is_available():
            logger.error(
                "[scihub] scidownl 未安装，请执行: pip install paper-dl[scihub]"
            )
            return False

        logger.warning(
            "⚠️  正在通过 Sci-Hub 尝试下载，请确认在您所在地区合法使用。"
            f" DOI: {doi}"
        )

        dest_path = Path(dest_dir) / filename
        dest_path.parent.mkdir(parents=True, exist_ok=True)

        # scidownl 是同步库，在线程池中运行以避免阻塞事件循环
        try:
            result = await asyncio.to_thread(
                self._sync_download, doi, str(dest_path)
            )
            return result
        except Exception as e:
            logger.warning(f"[scihub] 下载失败 (DOI: {doi}): {e}")
            return False

    def _sync_download(self, doi: str, dest_path: str) -> bool:
        """
        同步执行 scidownl 下载（在线程池中运行）。
        scidownl 将文件写入 dest_path 所在目录，文件名由库决定。
        我们用临时目录接收后再重命名到目标路径。
        """
        from scidownl import scihub_download

        dest_dir = str(Path(dest_path).parent)

        # scidownl 的 out 参数支持目录路径
        kwargs: dict = {
            "paper": doi,
            "paper_type": "doi",
            "out": dest_dir,
        }
        if self.proxy:
            kwargs["proxy"] = self.proxy

        try:
            scihub_download(**kwargs)
        except Exception as e:
            logger.debug(f"[scihub] scidownl 内部异常: {e}")
            return False

        # 检查目录内是否新增了 PDF 文件
        pdf_files = list(Path(dest_dir).glob("*.pdf"))
        if not pdf_files:
            logger.debug(f"[scihub] 下载后未找到 PDF 文件: {dest_dir}")
            return False

        # 如果目标文件名与生成的不同，重命名最新的 PDF
        latest_pdf = max(pdf_files, key=lambda p: p.stat().st_mtime)
        target = Path(dest_path)
        if latest_pdf != target:
            try:
                latest_pdf.rename(target)
            except Exception as e:
                logger.debug(f"[scihub] PDF 重命名失败: {e}")
                # 重命名失败不算下载失败，文件已存在即可
        return True
