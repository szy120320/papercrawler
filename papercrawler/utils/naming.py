"""
文件命名工具

规则：{year}_{first_author_lastname}_{title_slug}_{doi_suffix}
"""

from __future__ import annotations

import re
import unicodedata
from typing import Optional

from slugify import slugify


# 需要过滤掉的停用词（不计入 title_slug）
_STOP_WORDS = {
    "a", "an", "the", "of", "in", "on", "at", "to", "for", "and",
    "or", "but", "with", "by", "from", "as", "is", "are", "was",
    "were", "be", "been", "being", "have", "has", "had", "do", "does",
    "did", "will", "would", "could", "should", "may", "might",
}


def _sanitize(text: str) -> str:
    """去除特殊字符，仅保留字母数字和下划线"""
    text = unicodedata.normalize("NFKD", text)
    text = text.encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s-]+", "_", text.strip())
    return text.lower()


def title_to_slug(title: str, max_words: int = 5) -> str:
    """
    将论文标题转换为文件名友好的 slug

    Args:
        title: 论文标题
        max_words: 保留的有效词数量（排除停用词后）

    Returns:
        slug 字符串，例如 "multiscale_models_complex_chemical_systems"
    """
    words = re.sub(r"[^\w\s]", " ", title.lower()).split()
    meaningful = [w for w in words if w not in _STOP_WORDS and len(w) > 1]
    selected = meaningful[:max_words] if meaningful else words[:max_words]
    return "_".join(selected) or "untitled"


def doi_to_suffix(doi: str) -> str:
    """
    从 DOI 提取文件名后缀

    例如：10.1038/253694a0  ->  253694a0
    """
    if "/" in doi:
        suffix = doi.split("/", 1)[1]
    else:
        suffix = doi
    # 去除特殊字符
    suffix = re.sub(r"[^\w]", "", suffix)
    return suffix[:20]  # 最多20字符


def make_paper_dirname(
    year: Optional[int],
    first_author_lastname: str,
    title: str,
    doi: Optional[str] = None,
    hash_id: Optional[str] = None,
) -> str:
    """
    生成论文文件夹名称

    格式：{year}_{author}_{title_slug}_{doi_suffix}

    Args:
        year: 发表年份
        first_author_lastname: 第一作者姓氏
        title: 论文标题
        doi: DOI（可选）
        hash_id: 无 DOI 时的哈希值（可选）

    Returns:
        文件夹名称字符串
    """
    year_str = str(year) if year else "0000"
    author_str = _sanitize(first_author_lastname)[:20] or "unknown"
    title_str = title_to_slug(title, max_words=5)

    if doi:
        suffix = doi_to_suffix(doi)
    elif hash_id:
        suffix = hash_id[:8]
    else:
        suffix = "noid"

    dirname = f"{year_str}_{author_str}_{title_str}_{suffix}"
    # 限制总长度，避免路径过长
    return dirname[:120]
