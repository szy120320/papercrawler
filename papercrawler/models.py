"""
核心数据模型 — Pydantic v2
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, field_validator


# ---------------------------------------------------------------------------
# 枚举类型
# ---------------------------------------------------------------------------

class AccessStatus(str, Enum):
    """论文全文获取状态"""
    OPEN_ACCESS_PDF      = "oa_pdf"        # 可下载 PDF 全文
    OPEN_ACCESS_HTML     = "oa_html"       # 可下载 HTML 全文
    OPEN_ACCESS_PREPRINT = "oa_preprint"   # 可下载预印本（arXiv 等）
    METADATA_ONLY        = "metadata_only" # 仅可获取元数据（含摘要）
    UNKNOWN              = "unknown"       # 无法判断


class DownloadStatus(str, Enum):
    """下载任务状态"""
    PENDING  = "pending"
    SUCCESS  = "success"
    FAILED   = "failed"
    SKIPPED  = "skipped"


# ---------------------------------------------------------------------------
# 子模型
# ---------------------------------------------------------------------------

class Author(BaseModel):
    """作者信息"""
    name: str
    affiliation: Optional[str] = None
    orcid: Optional[str] = None

    def display_name(self) -> str:
        """返回姓氏（用于文件命名）"""
        parts = self.name.replace(",", "").split()
        return parts[0] if parts else "unknown"


# ---------------------------------------------------------------------------
# 核心模型
# ---------------------------------------------------------------------------

class PaperMetadata(BaseModel):
    """论文元数据"""
    title: str
    authors: list[Author] = Field(default_factory=list)
    year: Optional[int] = None
    journal: Optional[str] = None
    volume: Optional[str] = None
    issue: Optional[str] = None
    pages: Optional[str] = None
    doi: Optional[str] = None
    url: Optional[str] = None
    abstract: Optional[str] = None
    keywords: list[str] = Field(default_factory=list)
    citations_count: Optional[int] = None
    references_count: Optional[int] = None
    access_status: AccessStatus = AccessStatus.UNKNOWN
    oa_url: Optional[str] = None
    preprint_url: Optional[str] = None
    sources: list[str] = Field(default_factory=list)
    raw_ids: dict = Field(default_factory=dict)
    downloaded_at: Optional[datetime] = None
    files: dict[str, Optional[str]] = Field(default_factory=dict)
    author_match_score: Optional[float] = None  # 作者匹配分数（0.0~1.0），按作者检索时填充
    interest_score: Optional[float] = None     # 最终领域相关性分数（0.0~1.0）= 0.6*coarse + 0.4*semantic
    coarse_score: Optional[float] = None        # 第一阶段粗筛分数（仅 title + must_have/should_have）
    semantic_score: Optional[float] = None      # 第二阶段细筛分数（title+abstract+keywords vs description）
    is_reversed: bool = False                   # 是否命中反向关键词(命中则直接剔除,不进入下游)
    reversed_keywords: list[str] = Field(default_factory=list)  # 命中的反向关键词(用于审计)

    @field_validator("doi", mode="before")
    @classmethod
    def normalize_doi(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        v = v.strip()
        # 去掉前缀 https://doi.org/
        for prefix in ("https://doi.org/", "http://doi.org/", "doi:"):
            if v.lower().startswith(prefix):
                v = v[len(prefix):]
        return v or None

    @property
    def unique_id(self) -> str:
        """唯一标识：优先使用 DOI，否则用标题+作者+年份哈希"""
        if self.doi:
            return self.doi.lower()
        raw = f"{self.title.lower()}|{self.first_author_lastname}|{self.year}"
        return hashlib.sha256(raw.encode()).hexdigest()[:16]

    @property
    def first_author_lastname(self) -> str:
        if not self.authors:
            return "unknown"
        return self.authors[0].display_name()

    @property
    def doi_url(self) -> Optional[str]:
        if self.doi:
            return f"https://doi.org/{self.doi}"
        return self.url

    def access_status_display(self) -> str:
        mapping = {
            AccessStatus.OPEN_ACCESS_PDF:      "开放获取（PDF）",
            AccessStatus.OPEN_ACCESS_HTML:     "开放获取（HTML）",
            AccessStatus.OPEN_ACCESS_PREPRINT: "开放获取（预印本）",
            AccessStatus.METADATA_ONLY:        "仅元数据",
            AccessStatus.UNKNOWN:              "未知",
        }
        return mapping.get(self.access_status, "未知")


class DownloadTask(BaseModel):
    """下载任务"""
    paper: PaperMetadata
    output_dir: str
    status: DownloadStatus = DownloadStatus.PENDING
    error_msg: Optional[str] = None
    retry_count: int = 0

    def mark_success(self) -> None:
        self.status = DownloadStatus.SUCCESS
        self.error_msg = None

    def mark_failed(self, reason: str) -> None:
        self.status = DownloadStatus.FAILED
        self.error_msg = reason

    def mark_skipped(self, reason: str = "already downloaded") -> None:
        self.status = DownloadStatus.SKIPPED
        self.error_msg = reason


class SearchQuery(BaseModel):
    """检索请求"""
    query: Optional[str] = None        # 关键词
    author: Optional[str] = None       # 作者名
    title: Optional[str] = None        # 论文题目
    doi: Optional[str] = None          # DOI
    # 2026-07-05 v1.3.0 语义变更:
    # 旧:`max_results` = 总结果数上限(截顶)
    # 新:`page_size` = 单次 API 请求的页大小,翻页直到 API 终止信号
    # 各 adapter 会在自己 API 上限内自动 clamp(100/200/1000)
    page_size: int = 100
    year_from: Optional[int] = None
    year_to: Optional[int] = None
    sources: list[str] = Field(default_factory=list)   # 空列表=全部
    sort: str = "relevance"            # relevance | date | citations
    oa_only: bool = False

    def has_any_input(self) -> bool:
        return any([self.query, self.author, self.title, self.doi])

    def build_text_query(self) -> str:
        """将各字段拼合为通用文本检索词"""
        parts = []
        if self.query:
            parts.append(self.query)
        if self.author:
            parts.append(self.author)
        if self.title:
            parts.append(self.title)
        return " ".join(parts)
