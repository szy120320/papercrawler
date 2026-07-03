"""
papercrawler.paths — 全局路径常量

集中管理跨项目、跨运行共享的路径(全局 DB、缓存等)。
"""

from __future__ import annotations

import os
from pathlib import Path


# ---------------------------------------------------------------------------
# 全局数据库(用户级,跨项目,跨运行共享)
# ---------------------------------------------------------------------------
# 位置:~/.papercrawler/_download_log.db
# 作用:记录所有已下载论文,实现跨 run 去重
# 不进 git(用户级配置,不应该入库)

_USER_DATA_DIR = Path(
    os.environ.get("PAPERCRAWLER_HOME", str(Path.home() / ".papercrawler"))
)
GLOBAL_DATA_DIR: Path = _USER_DATA_DIR
GLOBAL_DB_PATH: Path = _USER_DATA_DIR / "_download_log.db"

# 确保目录存在(惰性,首次使用时创建)
def _ensure_global_dir() -> None:
    GLOBAL_DATA_DIR.mkdir(parents=True, exist_ok=True)
    # 在 Windows 上设置隐藏属性(更直观,类似 .git)
    if os.name == "nt":
        try:
            import ctypes
            FILE_ATTRIBUTE_HIDDEN = 0x02
            ctypes.windll.kernel32.SetFileAttributesW(
                str(GLOBAL_DATA_DIR), FILE_ATTRIBUTE_HIDDEN
            )
        except (OSError, AttributeError) as e:
            # OSError:权限/文件不存在;AttributeError:非 Windows 或 ctypes 不可用
            # 隐藏属性是 best-effort,失败也不影响主流程
            import logging
            logging.getLogger(__name__).debug(f"[paths] 设置隐藏属性失败(忽略): {e}")


# 模块导入时即创建(简单可靠)
_ensure_global_dir()
