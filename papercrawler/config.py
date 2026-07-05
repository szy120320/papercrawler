"""
配置管理 — pydantic-settings + TOML
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

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
    """Sci-Hub 下载配置（默认启用,作为所有下载失败后的统一兜底）

    ⚠️  法律免责声明:Sci-Hub 在美国、欧盟及部分其他地区属于版权侵权行为。
    请在启用前确认当地法规与机构网络使用政策;建议优先通过合法渠道获取全文。

    工作流:
      - 任何来源的 PDF / HTML 下载失败时(OA 链接 403/超时/解析失败等),
        自动尝试 Sci-Hub,先用 DOI 抓取,失败再用 title 二次抓取。
      - Sci-Hub 也失败,才会回退到「仅元数据」(只生成 paper.md + metadata.json)。

    关闭方式(完全不启用 Sci-Hub):在配置中设 `enabled = false`
    """
    model_config = SettingsConfigDict(extra="ignore")
    enabled: bool = True           # 默认启用 — 所有下载失败时统一兜底
    proxy: str = ""                # 代理地址，如 "socks5://127.0.0.1:7890"
    mirror: str = "https://sci-hub.st"   # Sci-Hub 镜像 URL(可改,域名经常变)
    # 浏览器路径可通过环境变量 PAPERCRAWLER_BROWSER_PATH 设置,
    # 默认自动检测 Edge / Chrome / Chromium
    headless: bool = True          # 是否无头模式(headless=True 一般够用)


# ---------------------------------------------------------------------------
# [interest] — 用户兴趣 / 领域感知配置
# ---------------------------------------------------------------------------

class InterestConfig(BaseSettings):
    """
    用户研究兴趣描述 + 关键词权重。

    两阶段打分配置:
      - 阶段 1(粗筛): 基于 title,用 must_have/should_have/exclude 关键词权重打分
      - 阶段 2(细筛): 命中关键词计数,title+abstract+keywords 中命中 semantic_keywords
                     数量 ≥ semantic_min_matches 才保留

    字段说明:
      - description         自然语言描述,仅供阅读注释
      - must_have           阶段 1 命中即得基础分 0.6(粗筛快速过滤)
      - should_have         阶段 1 每个命中 +0.1,封顶 0.3
      - exclude             阶段 1 命中即 -0.5(明确剔除)
      - reverse_keywords    命中即粗筛直接剔除(与 exclude 平级,但语义上更明确)
      - semantic_keywords   阶段 2 用于计数的关键词列表
      - semantic_min_matches 阶段 2 保留阈值(命中关键词数 ≥ 此值才通过)
      - fuzzy_threshold      模糊匹配阈值(difflib 字符相似度)

    注意(2026-07-05):
      - 删除 InterestCategory / categories 字段(分类功能下线)

    示例(关注"固体电解质相关分子动力学模拟"):
        description = "我想找固体电解质相关的分子动力学模拟研究"
        must_have = ["molecular dynamics"]
        semantic_keywords = [
            "solid electrolyte", "LLZO", "lithium", "Li-ion",
            "inorganic", "ReaxFF", "force field", "MD simulation",
        ]
        semantic_min_matches = 3   # 至少命中 3 个关键词才保留
    """
    model_config = SettingsConfigDict(extra="ignore")
    description: str = ""
    must_have: list[str] = []
    should_have: list[str] = []
    exclude: list[str] = []
    # 反向关键词 — 命中即硬剔除,两道都检查;语义清晰,与 exclude 区分
    reverse_keywords: list[str] = []
    # 第二阶段(细筛)字段
    semantic_keywords: list[str] = []       # 用于计数的关键词列表
    semantic_min_matches: int = 3          # 命中数 ≥ 此值才保留,默认 3
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
    """按优先级搜索配置文件

    搜索顺序:
      1. ./config/papercrawler.toml  (项目级配置,推荐)
      2. ./papercrawler.toml          (向后兼容,项目根)
      3. ~/.config/papercrawler/config.toml  (系统级,XDG 标准)
    """
    candidates = [
        Path("config") / "papercrawler.toml",
        Path("papercrawler.toml"),  # 向后兼容
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

    # 2026-07-05 移除 categories 解析(分类功能下线)
    return AppConfig(
        general=GeneralConfig(**data.get("general", {})),
        download=DownloadConfig(**data.get("download", {})),
        sources=SourcesConfig(**data.get("sources", {})),
        api_keys=ApiKeysConfig(**data.get("api_keys", {})),
        markitdown=MarkItDownConfig(**data.get("markitdown", {})),
        filters=FiltersConfig(**data.get("filters", {})),
        scihub=SciHubConfig(**data.get("scihub", {})),
        interest=InterestConfig(**data.get("interest", {})),
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
