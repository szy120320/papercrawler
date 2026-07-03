"""
AccessChecker — 开放获取状态检查器

按优先级依次尝试各种 OA 渠道，确定论文的实际可获取状态
并填充 oa_url 字段。
"""

from __future__ import annotations

import re
from typing import Optional

import httpx
from loguru import logger

from papercrawler.access.unpaywall import UnpaywallClient
from papercrawler.config import AppConfig, get_config
from papercrawler.models import AccessStatus, PaperMetadata


class AccessChecker:
    """
    检查单篇论文的 OA 状态，按以下优先级：

    1. arXiv 直链（raw_ids 或 preprint_url 含 arxiv）
    2. Unpaywall API（通过 DOI）
    3. Semantic Scholar openAccessPdf（已在搜索阶段填充 oa_url）
    4. OpenAlex open_access（已在搜索阶段填充 oa_url）
    5. CORE 全文链接（已在搜索阶段填充 oa_url）
    6. 出版商官网 HEAD 探测（简单判断是否重定向到 PDF）
    """

    def __init__(self, config: Optional[AppConfig] = None):
        self.config = config or get_config()
        self._unpaywall = UnpaywallClient(
            email=self.config.api_keys.unpaywall_email,
            timeout=self.config.download.connect_timeout,
        )

    async def check(self, paper: PaperMetadata) -> PaperMetadata:
        """
        原地更新 paper.access_status 和 paper.oa_url，并返回更新后的对象。
        """
        # 若已有明确的 OA 状态（搜索阶段已确定），仅补充 oa_url
        if paper.access_status in (
            AccessStatus.OPEN_ACCESS_PDF,
            AccessStatus.OPEN_ACCESS_HTML,
            AccessStatus.OPEN_ACCESS_PREPRINT,
        ) and paper.oa_url:
            return paper

        # 1. arXiv 直链
        arxiv_url = self._check_arxiv(paper)
        if arxiv_url:
            paper.access_status = AccessStatus.OPEN_ACCESS_PREPRINT
            paper.oa_url = arxiv_url
            logger.debug(f"OA via arXiv: {paper.title[:60]}")
            return paper

        # 2. Unpaywall（需要 DOI）
        if paper.doi:
            oa_url = await self._unpaywall.get_oa_url(paper.doi)
            if oa_url:
                if oa_url.endswith(".pdf") or "pdf" in oa_url.lower():
                    paper.access_status = AccessStatus.OPEN_ACCESS_PDF
                else:
                    paper.access_status = AccessStatus.OPEN_ACCESS_HTML
                paper.oa_url = oa_url
                logger.debug(f"OA via Unpaywall: {paper.title[:60]}")
                return paper

        # 3. 已有 oa_url（来自搜索阶段 S2 / OpenAlex / CORE）
        if paper.oa_url:
            if "arxiv" in paper.oa_url.lower():
                paper.access_status = AccessStatus.OPEN_ACCESS_PREPRINT
            elif paper.oa_url.endswith(".pdf"):
                paper.access_status = AccessStatus.OPEN_ACCESS_PDF
            else:
                paper.access_status = AccessStatus.OPEN_ACCESS_HTML
            return paper

        # 4. 出版商官网 HEAD 探测（仅简单判断 Content-Type）
        if paper.doi or paper.url:
            detected = await self._probe_publisher(paper)
            if detected:
                paper.access_status = AccessStatus.OPEN_ACCESS_PDF
                paper.oa_url = detected
                logger.debug(f"OA via publisher probe: {paper.title[:60]}")
                return paper

        # 未找到任何 OA 渠道
        paper.access_status = AccessStatus.METADATA_ONLY
        return paper

    # ------------------------------------------------------------------
    # 私有方法
    # ------------------------------------------------------------------

    def _check_arxiv(self, paper: PaperMetadata) -> Optional[str]:
        """检查是否存在 arXiv ID，返回 PDF URL"""
        arxiv_id = paper.raw_ids.get("arxiv")
        if arxiv_id:
            return f"https://arxiv.org/pdf/{arxiv_id}.pdf"

        # 从 preprint_url 中提取
        if paper.preprint_url:
            m = re.search(r"arxiv\.org/abs/([^\s?#]+)", paper.preprint_url)
            if m:
                return f"https://arxiv.org/pdf/{m.group(1)}.pdf"

        # 从 DOI 中提取 (10.48550/arXiv.XXXX)
        if paper.doi:
            m = re.search(r"arxiv[./:](\d{4}\.\d{4,5})", paper.doi, re.IGNORECASE)
            if m:
                return f"https://arxiv.org/pdf/{m.group(1)}.pdf"

        return None

    async def _probe_publisher(self, paper: PaperMetadata) -> Optional[str]:
        """
        尝试 HEAD 请求出版商 URL，判断是否可直接访问 PDF。
        仅做轻量探测，不实际下载。
        """
        url = paper.url or (f"https://doi.org/{paper.doi}" if paper.doi else None)
        if not url:
            return None

        try:
            async with httpx.AsyncClient(
                timeout=self.config.download.connect_timeout,
                follow_redirects=True,
            ) as client:
                resp = await client.head(url)
                ct = resp.headers.get("content-type", "")
                final_url = str(resp.url)
                if "pdf" in ct.lower() or final_url.endswith(".pdf"):
                    return final_url
        except Exception:
            pass
        return None

    async def check_batch(self, papers: list[PaperMetadata]) -> list[PaperMetadata]:
        """对一批论文并发执行 OA 检查"""
        import asyncio
        semaphore = asyncio.Semaphore(10)

        async def _check_one(p: PaperMetadata) -> PaperMetadata:
            async with semaphore:
                return await self.check(p)

        return list(await asyncio.gather(*[_check_one(p) for p in papers]))
