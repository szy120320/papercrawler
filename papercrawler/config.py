"""
配置管理 — pydantic-settings + TOML
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class GeneralConfig(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")
    output_dir: str = "./papers"
    max_results: int = 20
    default_sort: str = "relevance"


class DownloadConfig(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")
    max_concurrent: int = 5
    request_delay: float = 1.0
    connect_timeout: int = 10
    read_timeout: int = 60
    retry_times: int = 3
    user_agent: str = "PaperDownloader/1.0 (research tool; mailto:user@example.com)"


class SourcesConfig(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")
    enabled: list[str] = [
        "semantic_scholar", "openalex", "crossref", "pubmed", "arxiv", "core", "chemrxiv"
    ]


class ApiKeysConfig(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")
    semantic_scholar: str = ""
    pubmed: str = ""
    core: str = ""
    unpaywall_email: str = ""


class MarkItDownConfig(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")
    enabled: bool = True
    save_original: bool = True
    include_images: bool = False


class FiltersConfig(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")
    oa_only: bool = False
    min_year: int = 0
    max_year: int = 0
    author_match_threshold: float = 0.0  # 作者匹配分数阈值（0.0=不过滤，0.6=推荐过滤值）


class SciHubConfig(BaseSettings):
    """Sci-Hub 下载配置（须显式启用；请确认在当地合法使用）"""
    model_config = SettingsConfigDict(extra="ignore")
    enabled: bool = False          # 默认关闭，需在 papercrawler.toml 中设 enabled = true
    proxy: str = ""                # 代理地址，如 "socks5://127.0.0.1:7890"


# ---------------------------------------------------------------------------
# [interest] — 用户兴趣 / 领域感知配置
# ---------------------------------------------------------------------------

class InterestCategory(BaseModel):
    """单个用户自定义分类"""
    name: str
    keywords: list[str] = []
    # 至少需要命中几个关键词才算入该类（默认 1）
    min_hits: int = 1


class InterestConfig(BaseSettings):
    """
    用户研究兴趣描述 + 关键词权重 + 自定义分类。

    用法:
      - description:    自然语言描述,仅供阅读/未来 LLM 提示词使用
      - must_have:      命中即得基础分 0.6
      - should_have:    每个命中 +0.1,封顶 0.3
      - exclude:        命中即 -0.5
      - categories:     每类独立计算,一篇可同时属多类
    """
    model_config = SettingsConfigDict(extra="ignore")
    description: str = ""
    must_have: list[str] = []
    should_have: list[str] = []
    exclude: list[str] = []
    categories: list[InterestCategory] = []
    # 模糊匹配阈值(distance),0.0~1.0,1.0=完全相等,0.85 是较宽松
    fuzzy_threshold: float = 0.85


class AppConfig(BaseSettings):
    """全局配置，从 TOML 文件加载"""
    model_config = SettingsConfigDict(extra="ignore")

    general: GeneralConfig = GeneralConfig()
    download: DownloadConfig = DownloadConfig()
    sources: SourcesConfig = SourcesConfig()
    api_keys: ApiKeysConfig = ApiKeysConfig()
    markitdown: MarkItDownConfig = MarkItDownConfig()
    filters: FiltersConfig = FiltersConfig()
    scihub: SciHubConfig = SciHubConfig()
    interest: InterestConfig = InterestConfig()


def _find_config_file() -> Optional[Path]:
    """按优先级搜索配置文件"""
    candidates = [
        Path("papercrawler.toml"),
        Path.home() / ".config" / "papercrawler" / "config.toml",
    ]
    for p in candidates:
        if p.exists():
            return p
    return None


def load_config(config_path: Optional[str] = None) -> AppConfig:
    """加载配置文件，返回 AppConfig 实例"""
    path = Path(config_path) if config_path else _find_config_file()

    if path is None or not path.exists():
        return AppConfig()

    if sys.version_info >= (3, 11):
        import tomllib
        loader = tomllib
        mode = "rb"
    else:
        try:
            import tomllib
            mode = "rb"
            loader = tomllib
        except ImportError:
            try:
                import tomli as loader  # type: ignore
                mode = "rb"
            except ImportError:
                raise ImportError(
                    "Python < 3.11 需要安装 tomli: pip install tomli"
                )

    with open(path, mode) as f:
        data = loader.loads(f.read()) if mode == "r" else loader.load(f)

    # 解析嵌套的 [interest.categories] 数组
    interest_data = data.get("interest", {})
    categories_data = interest_data.pop("categories", []) if interest_data else []

    return AppConfig(
        general=GeneralConfig(**data.get("general", {})),
        download=DownloadConfig(**data.get("download", {})),
        sources=SourcesConfig(**data.get("sources", {})),
        api_keys=ApiKeysConfig(**data.get("api_keys", {})),
        markitdown=MarkItDownConfig(**data.get("markitdown", {})),
        filters=FiltersConfig(**data.get("filters", {})),
        scihub=SciHubConfig(**data.get("scihub", {})),
        interest=InterestConfig(
            **interest_data,
            categories=[InterestCategory(**c) for c in categories_data],
        ),
    )


# 全局单例（CLI 初始化时赋值）
_config: Optional[AppConfig] = None


def get_config() -> AppConfig:
    global _config
    if _config is None:
        _config = load_config()
    return _config


def set_config(cfg: AppConfig) -> None:
    global _config
    _config = cfg
