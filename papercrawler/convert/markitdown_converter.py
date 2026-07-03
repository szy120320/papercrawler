"""
MarkItDown 转换器集成

Microsoft MarkItDown: https://github.com/microsoft/markitdown
支持 PDF、HTML、DOCX、XML 等格式转换为 Markdown。
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from loguru import logger


class MarkItDownConverter:
    """
    封装 Microsoft MarkItDown，将多种格式文件转换为 Markdown 文本。

    转换失败时返回空字符串（降级为仅元数据模式），不抛出异常。
    """

    def __init__(self, enabled: bool = True):
        self.enabled = enabled
        self._md = None
        if enabled:
            self._init_markitdown()

    def _init_markitdown(self) -> None:
        try:
            from markitdown import MarkItDown
            self._md = MarkItDown()
            logger.debug("MarkItDown 初始化成功")
        except ImportError:
            logger.warning(
                "markitdown 未安装,格式转换功能不可用。"
                "请运行: pip install markitdown[all]"
            )
            self.enabled = False
        except Exception as e:
            logger.opt(exception=True).warning(f"MarkItDown 初始化失败: {e}")
            self.enabled = False

    def convert_file(self, file_path: str) -> Optional[str]:
        """
        将指定文件转换为 Markdown 文本。

        Args:
            file_path: 文件绝对路径（PDF、HTML、DOCX、XML）

        Returns:
            Markdown 文本字符串，失败时返回 None
        """
        if not self.enabled or self._md is None:
            return None

        path = Path(file_path)
        if not path.exists():
            logger.warning(f"[CONV] 文件不存在: {file_path}")
            return None

        suffix = path.suffix.lower()
        supported = {".pdf", ".html", ".htm", ".docx", ".xml", ".pptx", ".xlsx", ".csv"}
        if suffix not in supported:
            logger.warning(f"[CONV] 不支持的文件格式: {suffix}")
            return None

        try:
            result = self._md.convert(str(path))
            text = result.text_content
            if text:
                logger.debug(f"[CONV] 转换成功: {path.name} ({len(text)} 字符)")
            return text or None
        except Exception as e:
            logger.warning(f"[CONV_ERROR] 转换失败 {path.name}: {e}")
            return None

    def convert_url(self, url: str) -> Optional[str]:
        """
        将 URL 内容转换为 Markdown（适用于 HTML 页面）。
        """
        if not self.enabled or self._md is None:
            return None
        try:
            result = self._md.convert(url)
            return result.text_content or None
        except Exception as e:
            logger.warning(f"[CONV_ERROR] URL 转换失败 {url}: {e}")
            return None

    def is_available(self) -> bool:
        return self.enabled and self._md is not None
