# 更新日志

所有对 PaperCrawler 项目的显著修改都会记录在此文件中。

格式基于 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/),
本项目遵循 [语义化版本](https://semver.org/lang/zh-CN/) 规范。

## [Unreleased]

### 新增 (Added)
- **新命令 `papercrawler recategorize`**:对已下载论文补跑领域打分与分类,扫描 `results/` 下所有 `metadata.json`,支持 `--interest-threshold` 过滤 + 导出 CSV
- **目录结构重构**:
  - `config/` 目录统一管理所有 TOML 配置文件(原 `papercrawler.toml` 已移入)
  - `results/` 目录统一管理所有运行输出(原 `papers/` 已迁入)
  - 每次 `search` / `download` / `batch` 自动在 `results/<时间戳 或 --name>/` 下创建独立子目录
- **CLI 选项 A 自动启用**:`[interest]` 配置存在时,自动启用:
  - 领域相关性打分
  - 自动多标签分类
  - CSV 导出到 `results/<本次运行>/_interest_filtered.csv`
  - 配合 `--interest-threshold` 可自动过滤
- **三态 CLI 标志**:`--interest/--no-interest`、`--categorize/--no-categorize`、`--csv PATH` / `--no-csv`,允许自动 / 强制 / 关闭
- **新 CLI 选项 `--name`**:自定义本次运行名称(用作 `results/` 子目录名)
- **新 CLI 选项 `--no-csv`**:显式禁用 CSV 导出
- 领域相关性判定模块 `papercrawler.classify.DomainFilter`(基于 title+abstract 关键词 + 模糊匹配)
- 自动多标签分类模块 `papercrawler.classify.Categorizer`(一篇可同时属多类)
- CSV 导出器 `papercrawler.export.CSVWriter`(UTF-8 BOM, Excel 友好)
- 配置文件 `[interest]` 节(描述、must_have / should_have / exclude 关键词权重、自定义分类)
- `PaperMetadata` 模型新增 `interest_score` 与 `categories` 字段
- GitHub 仓库: https://github.com/szy120320/papercrawler(remote 使用 `ghfast.top` 镜像绕过 github.com 封锁)

### 变更 (Changed)
- **配置文件位置**:`./papercrawler.toml` → `./config/papercrawler.toml`(`config.py` 同时支持旧位置,向后兼容)
- **下载 / 搜索默认输出**:`./papers` → `./results/<时间戳 或 --name>/`
- `papercrawler config init` 改为在 `./config/` 下生成配置
- 项目重命名:`paper-dl` → `PaperCrawler`
- 包目录重命名:`paper_dl/` → `papercrawler/`
- CLI 命令名:`paper-dl` → `papercrawler`
- `README.md` 完全重写,加入目录结构说明 + 选项 A 自动启用说明
- 远程仓库 URL:`https://ghfast.top/https://github.com/szy120320/papercrawler.git`(因 github.com 在本机网络下不可直连)
- `.gitignore` 规则更新:`config/papercrawler.toml`、`results/` 全部进 ignore 名单

### 修复 (Fixed)
- 修复了"必须每次手动加 `--interest --categorize --csv xxx.csv` 才会导出"的设计反人类问题(选项 A)

### 安全 (Security)
- 清理了首次提交中误包含的本地临时脚本(`_setup_github.py` / `_test_github.py` / `_test_proxy.py`)
- 现有 21 篇论文从 `./papers/` 迁移到 `./results/migrated_<时间戳>/papers/`,原始数据不丢失

---

## [1.1.0] - 2026-07-04

### 新增 (Added)
- **两阶段领域打分**:`DomainFilter` 粗筛(基于 title,≥0.7)+ `SemanticFilter` 细筛(基于 title+abstract+keywords,命中关键词数 ≥ 3);双门限硬过滤
- **`PaperMetadata` 新字段**:`coarse_score`(粗筛浮点)/ `semantic_score`(细筛命中数整数);保留旧 `interest_score` 字段向后兼容
- **CSV 新列**:`coarse_score` + `semantic_score`(替换原 `interest_score`)
- **新 CLI 选项 `--semantic-min-matches`**:细筛最少命中关键词数(默认 3)
- **`search/base.py` 公共 HTTP 工具**:`_get_json()`(返回 dict) + `_get_text()`(返回 str,支持 XML);两方法共用限速 / 重试 / jitter
- **`SourceError` 异常类**:统一表达数据源错误,带 `kind`(`http_error` / `parse_error` / `timeout` / `rate_limit` / `other`)与 `cause`
- **`SourceStats` dataclass**:`SearchManager.search_with_stats()` 返回新结构(失败计数分类,不再用 `-1` 标记)
- **CLI 包结构**:`papercrawler/cli.py`(996 行单文件)→ `papercrawler/cli/`(8 个子模块),允许独立测试与维护
- **`cli/_helpers.py`** 共享工具:setup / display / csv / run / parse_task_file / default_run_name
- **`cli/__main__.py`** 允许 `python -m papercrawler.cli`
- **失败计数 UI**:`_display_source_stats()` 按失败类型标色(红 / 黄 / 绿),向后兼容 `dict[str, int]` 旧接口
- **限速 jitter**:`429` 重试时加 ±20% 随机抖动,避免多并发任务同步命中 thundering herd
- **`_fetch_bytes` 重试分类**:403 / 404 / 410 立即终止(不需要重试),其他 5xx 重试 3 次

### 变更 (Changed)
- **`papercrawler.cli` 入口形态**:由单文件模块拆为 `papercrawler.cli/` 包,`papercrawler cli:app` console script 不变
- **`SearchAdapter._get` → `_get_json`**(语义更准确,XML 用 `_get_text()`);6 个 adapter 同步更新
- **`BaseSearchAdapter._get` 拆分**:核心 `_get_raw()` 私有,`_get_json()` / `_get_text()` 公共包装
- **`SearchManager.search_with_stats` 返回类型**:`dict[str, int]` → `dict[str, SourceStats]`;旧接口通过 `source_stats_to_int_map()` shim 兼容
- **`MetadataExtractor.enrich_batch` 失败计数**:按 `http_error` / `parse_error` / `timeout` / `other` 分类,统一 INFO 日志汇总
- **`PaperDownloader._download_one` 异常分类**:`httpx.HTTPError`(网络层)/ `(OSError, ValueError)`(业务层)/ `Exception` 兜底,每条带 traceback
- **`PaperDownloader._fetch_bytes` 重试策略**:`403 / 404 / 410` 立即终止;`HTTPStatusError` 单独捕获,区分 5xx 重试与 4xx 终止
- **`SciHubDownloader` 异常分类**:`binascii.Error` / `asyncio.TimeoutError` / `RuntimeError`(浏览器未找到)/ Playwright 异常分类捕获

### 修复 (Fixed)
- **`except Exception` 静默吞错**(35 处)→ 改为具体异常类 (`httpx.HTTPError` / `(OSError, ValueError)` / `(KeyError, AttributeError, TypeError, ValueError)` 等) + `logger.opt(exception=True).error(...)` 自动带 traceback
- **测试被 stale `.pth` 文件掩盖**:`.venv/lib/site-packages/_editable_impl_paper_dl.pth` 指向了桌面的旧 `paper-dl-main` 项目,导致 `tests/` 跑的是旧项目代码;清理该 `.pth` 后才能跑当前代码;同时把 `tests/test_paper_dl.py` 的 `from paper_dl.X` 全部改成 `from papercrawler.X`
- **测试中过时的 `_get` 引用**:3 个 S2 adapter 测试 `patch.object(adapter, "_get", ...)` → `_get_json`
- **`semantic_scholar.py` 等 6 个 adapter 仍用旧 `_get` 方法名**:统一改为 `_get_json`
- **`_fetch_bytes` 缩进损坏**:之前重构时把 classmethod 改成 top-level function,导致 `IndentationError`;修复为正确的 4 空格缩进
- **`download_run` 数据库迁移**:已有 DB 缺 `download_run` 列时,启动时自动 ALTER TABLE(幂等)
- **`paper_dl` → `papercrawler` 重命名时未同步**:测试文件导入语句 + 编辑器配置同步更新

### 安全 (Security)
- **清理 stale editable 安装**:`_editable_impl_paper_dl.pth` 移到回收站,防止 Python 误加载桌面的同名旧项目
- **`binascii.Error` 单独捕获**(Sci-Hub base64 解码):防止任何编码异常被静默吞掉

### 测试 (Tests)
- 新增 **16 个单元测试**(54 → 70 总数),覆盖:
  - `SourceError` 构造 + `kind` 枚举值
  - `SourceStats` dataclass + `source_stats_to_int_map` 兼容 shim
  - `BaseSearchAdapter._get_json` 成功 / 404 / parse_error / `_get_text` 成功(respx mock)
  - `_display_source_stats` 新旧两种接口
  - CLI 包结构(目录 / 无 cli.py 残留 / 7 命令注册 / 子 typer)
- **测试导入路径修正**:`tests/test_paper_dl.py` 全部 `from paper_dl.X` → `from papercrawler.X`(21 处)
- **测试运行时间**:54 测试 8.4 秒;2 个手动跑测试跳过(`test_full_flow.py` 端到端 + ArXiv 集成)

### 重构统计
- 删除:`papercrawler/cli.py`(1022 行)
- 新增:`papercrawler/cli/`(8 个文件,共 ~900 行)
- 修改:9 个 search adapter 文件 + 3 个 download 文件 + 3 个 cli 相关文件
- 净增:约 200 行(主要是类型注解 + 文档)

---

## 版本标签说明

- **Major(X.0.0)**:不兼容的 API 修改
- **Minor(0.X.0)**:向下兼容的功能新增
- **Patch(0.0.X)**:向下兼容的 bug 修复

预发布版本与构建元数据见 [semver.org](https://semver.org/lang/zh-CN/)。
