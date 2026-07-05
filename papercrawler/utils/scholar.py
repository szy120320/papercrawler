"""
Google Scholar 引用查询封装(2026-07-05 集成 paperscraper 的 scholarly 功能)

背景:
  - paperscraper 用 `scholarly` 库做 Google Scholar 引用数查询
  - 本项目原本只用 Semantic Scholar(S2)一个源,有 API key 时延迟低
  - 但 S2 经常没及时索引新论文,citation_count 可能为 None
  - 兜底:用 scholarly 查 Google Scholar 引用数

⚠️ 重要警告:
  - Google Scholar **没有公开 API**,scholarly 是非官方爬虫
  - 没有 API key 严格 rate limit:常 503 / 429 / CAPTCHA
  - scholarly 库本身 2023 年起基本不维护,可能在某天彻底坏掉
  - 本模块做成"软依赖":没装 scholarly 就 import 失败,fallback 路径自动跳过
  - 即使装了,失败时返回 None,不阻塞主流程

使用:
  from papercrawler.utils.scholar import get_citation_count
  n = get_citation_count("10.1021/acs.jcim.3c00132")
  # -> 12 或 None(失败)
"""

from __future__ import annotations

from typing import Optional
from loguru import logger

# 软依赖:scholarly 没装不报错
try:
    from scholarly import scholarly as _scholarly
    _HAS_SCHOLARLY = True
except ImportError:
    _scholarly = None
    _HAS_SCHOLARLY = False


def is_available() -> bool:
    """scholarly 是否可用(已装)"""
    return _HAS_SCHOLARLY


def get_citation_count(doi: str, timeout_sec: float = 30.0) -> Optional[int]:
    """
    通过 Google Scholar 查 DOI 的引用数。

    Args:
        doi: 论文 DOI(任意来源,例 "10.1021/acs.jcim.3c00132")
        timeout_sec: 整体超时秒数(scholarly 库没内置 timeout,这里做软限制)

    Returns:
        引用数(int)或 None(失败 / scholarly 未装 / 找不到)

    Note:
        - 不在 DOI 前加 "DOI:" 前缀(scholarly.search_pubs 会自动处理)
        - 第一次请求可能需要 5-15 秒(Google Scholar 慢)
        - 失败一律返回 None,不抛异常
    """
    if not _HAS_SCHOLARLY:
        logger.debug("[scholar] scholarly 未安装,跳过")
        return None

    if not doi or not doi.strip():
        return None

    import time
    t0 = time.perf_counter()

    try:
        # scholarly 用 "DOI:xxx" 格式检索
        query = doi.strip()
        # scholarly.search_pubs 返回 generator
        results = _scholarly.search_pubs(query)
        # 拿第一条
        for pub in results:
            elapsed = time.perf_counter() - t0
            if elapsed > timeout_sec:
                logger.debug(f"[scholar] {doi} 超时 ({elapsed:.1f}s > {timeout_sec}s)")
                return None
            # scholarly 返回 OrderedDict,找 num_citations 字段
            bib = pub.get("bib", {}) or {}
            n_cites = bib.get("num_citations")
            if n_cites is not None:
                try:
                    return int(n_cites)
                except (TypeError, ValueError):
                    return None
            # 备用字段名
            n_cites_alt = pub.get("num_citations")
            if n_cites_alt is not None:
                try:
                    return int(n_cites_alt)
                except (TypeError, ValueError):
                    return None
            # 找到第一条但无引用数,返回 0(论文存在但没人引)
            return 0
        # generator 耗尽都找不到
        logger.debug(f"[scholar] {doi} 在 Google Scholar 找不到")
        return None
    except Exception as e:
        # 抓各种异常: 503 / 429 / CAPTCHA / 网络错误 / scholarly API 变化
        logger.debug(f"[scholar] {doi} 失败: {type(e).__name__}: {str(e)[:80]}")
        return None