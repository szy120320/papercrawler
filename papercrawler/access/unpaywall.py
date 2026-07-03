"""
Unpaywall API 集成
API 文档: https://unpaywall.org/products/api

通过 DOI 查询论文的合法开放获取链接。
"""

from __future__ import annotations

from typing import Optional

import httpx
from loguru import logger

_BASE = "https://api.unpaywall.org/v2"


class UnpaywallClient:
    def __init__(self, email: str, timeout: int = 15):
        if not email or email == "your@email.com":
            logger.warning(
                "Unpaywall email 未配置（当前为占位符）。"
                "请在 config.toml 中设置 api_keys.unpaywall_email"
            )
        self.email = email
        self.timeout = timeout

    async def get_oa_url(self, doi: str) -> Optional[str]:
        """
        通过 DOI 查询最佳 OA 全文 PDF URL。

        Returns:
            PDF URL 字符串，或 None（无 OA 版本 / 查询失败）
        """
        if not doi:
            return None
        if not self.email or self.email == "your@email.com":
            return None

        url = f"{_BASE}/{doi}"
        params = {"email": self.email}

        try:
            async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=True) as client:
                resp = await client.get(url, params=params)
                if resp.status_code == 404:
                    return None
                resp.raise_for_status()
                data = resp.json()
        except httpx.HTTPError as e:
            logger.opt(exception=True).debug(f"[unpaywall] 查询 HTTP 错误 DOI={doi}: {e}")
            return None
        except (ValueError, KeyError, TypeError) as e:
            logger.opt(exception=True).debug(f"[unpaywall] JSON 解析失败 DOI={doi}: {e}")
            return None
        except Exception as e:
            logger.opt(exception=True).warning(f"[unpaywall] 查询异常 DOI={doi}: {e}")
            return None

        # 优先选择 best_oa_location 中的 PDF URL
        best = data.get("best_oa_location") or {}
        pdf_url = best.get("url_for_pdf") or best.get("url")

        if pdf_url:
            logger.debug(f"[unpaywall] 找到 OA URL: {pdf_url}")
            return pdf_url

        # 遍历所有 oa_locations
        for loc in data.get("oa_locations", []):
            url_pdf = loc.get("url_for_pdf")
            if url_pdf:
                return url_pdf

        return None

    async def get_oa_info(self, doi: str) -> dict:
        """
        返回完整的 Unpaywall OA 信息字典。
        包含 oa_status、best_oa_location 等字段。
        """
        if not doi or not self.email or self.email == "your@email.com":
            return {}

        url = f"{_BASE}/{doi}"
        params = {"email": self.email}
        try:
            async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=True) as client:
                resp = await client.get(url, params=params)
                if resp.status_code == 404:
                    return {}
                resp.raise_for_status()
                return resp.json()
        except httpx.HTTPError as e:
            logger.opt(exception=True).debug(f"[unpaywall] 详情查询 HTTP 错误 DOI={doi}: {e}")
            return {}
        except (ValueError, KeyError, TypeError) as e:
            logger.opt(exception=True).debug(f"[unpaywall] 详情查询 JSON 解析失败 DOI={doi}: {e}")
            return {}
        except Exception as e:
            logger.opt(exception=True).warning(f"[unpaywall] 详情查询异常 DOI={doi}: {e}")
            return {}
