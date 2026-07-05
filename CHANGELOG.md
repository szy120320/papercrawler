# 更新日志

所有对 PaperCrawler 项目的显著修改都会记录在此文件中。

格式基于 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/),
本项目遵循 [语义化版本](https://semver.org/lang/zh-CN/) 规范。

## [1.2.0] - 2026-07-05

### 新增 (Added)
- **CSV 自动命名导出**:本次 run 的 CSV 自动以 `<must_have 关键词>_<YYYYMMDD>_<入库数量>.csv` 命名(例:`solid_state_electrolyte_20260705_62.csv`),不再用 `_interest_filtered.csv` 下划线前缀
- **全局合并 CSV 改名**:`results/_all_filtered.csv` → `results/total_papers.csv`
- **`reverse_keywords` 反向关键词机制**:命中即直接剔除(`is_reversed=True`,score=0),与 `exclude` 平级但语义更明确;严格 substring 匹配,不走 fuzzy(防误杀)
- **`[interest].semantic_keywords` 细筛命中计数**:在 title + abstract + keywords 中数命中关键词数,≥ `semantic_min_matches`(默认 3)才保留

### 变更 (Changed)
- **数据源:7 → 6**:`PubMed` 关闭(SSE 命中率 < 5%,且不返回 abstract)
- **删除自动分类功能**:`Categorizer` 模块、`recategorize` CLI 命令、`InterestCategory` 配置模型、`PaperMetadata.categories` 字段、CSV `categories` 列全部移除
- **关键词配置瘦身**:`must_have` 从 4 词(`solid state electrolyte` / `machine learning force field` / `molecular dynamics` / `DFT`)收敛到 1 词(`solid state electrolyte`)
- **`reverse_keywords` 单词化**:原 `polymer electrolyte` / `liquid electrolyte` / `aqueous electrolyte` / `gel electrolyte` 4 个复合词改为单字 `polymer` / `liquid` / `aqueous` / `gel`,更激进反向过滤
- **`semantic_keywords` 精简**:删除 MLIP / 力场 / DFT 相关词(7 个),新增 `amorphous` / `sulfide` / `sulphide` / `halide` / `oxide` 物相/结构关键词
- **CLI 默认 `interest_threshold`**:0.7 → 0.6(单 must_have 命中 = 0.6,刚好过线)
- **6 个 adapter 翻页上限大幅提高**:
  | Adapter | 旧 → 新 |
  |---|---|
  | CrossRef | rows 80→100, max_pages 20→50(1,600 → 5,000 条) |
  | OpenAlex | max_pages 50→100(10,000 → 20,000 条) |
  | ArXiv | max_results 500→1000, max_pages 10→20(5,000 → 20,000 条) |
  | Semantic Scholar | max_pages 20→40(2,000 → 4,000 条,无 key 仍限 5 页) |
  | CORE | max_pages 20→50(2,000 → 5,000 条) |
  | ChemRxiv | 无变化 |
- **CSV 列调整**:删除 `categories` 列(13 → 12 列)

### 修复 (Fixed)
- **`must_have` / `should_have` / `exclude` 不存在时**:`[interest]` 流水线不会自动开启(原逻辑依赖 categories 字段)
- **`reverse_keywords` 多 token fuzzy 误杀**:严格 substring 匹配避免 `"aqueous electrolyte"` 误杀所有 SSE 论文

### 重构统计
- **删除文件**:
  - `papercrawler/classify/categorizer.py`(98 行)
  - `papercrawler/cli/recategorize.py`(167 行)
- **修改文件**:9 个搜索 adapter + 9 个生产代码文件 + 3 个测试文件 + 1 个 config
- **净减**:约 350 行代码

### 安全 (Security)
- **清理测试结果**:删除 `results/` 下 21 个 run 子目录、`test_output_20_papers_*`、`merge_yearly_results.py` 临时脚本,保证 git 历史中不混入个人测试数据
- **测试文件 Categorizer 清理**:`tests/test_20_papers.py` / `tests/test_full_flow.py` / `tests/test_paper_dl.py` 移除 Categorizer 调用,测试可继续运行

---

## [Unreleased]

### 新增 (Added)

### 变更 (Changed)

### 修复 (Fixed)

---

## [1.3.0] - 2026-07-05

> **核心主题:领域感知 + 断点续下 + 逐年翻页**

### 新增 (Added)

- **`download --from-csv <csv>` 命令**:从 `search` 输出的 CSV 批量下载/续下;典型工作流:检索 → 人工确认 CSV → 批量下载,可分多日完成
- **`CSVReader` 类**(`papercrawler/export/csv_writer.py`):把 `search` 导出的 CSV 重新加载为 `PaperMetadata` 列表;支持 `--skip-already` 过滤 `downloaded=true` 行
- **`--skip-already` CLI 选项**(配合 `--from-csv`):跳过 CSV 中 `downloaded=true` 的行(常规推荐依赖全局 DB 自动跳过,此选项用于 CSV 标记与 DB 不一致时的兜底)
- **`SearchManager._split_year_range()`**:年份范围跨度 > 1 年时自动拆为单年多次查询并汇总,避免单个数据源返回上限被截顶;单年查询不拆分(避免无谓的 API 开销)
- **`papercrawler.toml` 的 `[cli.defaults]` 节**:CLI 默认参数(`query` / `year_from` / `year_to` / `page_size` / `sort`)移到配置文件,命令行只跑功能;`papercrawler search` 不传任何参数即使用 toml 默认值
- **`download_status` 字段持久化**:`SearchQuery.max_results` → `SearchQuery.page_size`(单页大小),语义从"总结果上限"改为"单页请求大小";各 adapter 在自己 API 上限内自动 clamp
- **`download_run` 数据库迁移**:已有 DB 缺 `download_run` 列时启动自动 ALTER TABLE(幂等,见 v1.1.0)

### 变更 (Changed)

- **删除 `-n / --max-results` CLI 选项**:`SearchQuery.max_results` 字段重命名为 `page_size`,语义变更;配置文件同步
- **6 个 adapter 改名 `max_results → page_size`**:`arxiv` / `crossref` / `openalex` / `core` / `semantic_scholar` / `pubmed` / `chemrxiv_via_crossref`
- **删除 6 个 adapter 的 `[: query.page_size]` 截顶**:改为翻页直到 API 终止信号(`next_cursor=null` 或 `total` 耗尽)
- **7 个 adapter 清理 UTF-8 BOM**:之前 `Set-Content -Encoding UTF8` 写入时残留的 BOM 被 Python 批处理 `b[3:]` 修复
- **CSV 自动命名格式变更**:`<must_have 关键词>_<YYYYMMDD>_<入库数量>.csv` → `<YYYYMMDD>_<关键词slug>_<数量>.csv`(日期在前便于排序,文件名以字母开头无下划线前缀)
- **CSV 列调整**:保留 12 列(`downloaded` 在 v1.2 已加)
- **6 个数据源页面大小自动 clamp**:`CrossRef/S2/CORE/chemrxiv_via_crossref` max 100;`OpenAlex` max 200;`ArXiv` max 1000

### 修复 (Fixed)

- **`chemrxiv_via_crossref.py` line 28 合并行 bug**:`_PAGE_SIZE = 100  # ... # _MAX_PAGES = 50` 两行被吞成一行导致 `NameError: name '_MAX_PAGES' is not defined`;修复为正确的两行
- **`arxiv.py` line 166-167 合并行 bug**:`# 分类 = 关键词` 注释后换行被吞,导致 `categories = [` 缺少缩进,触发 `SyntaxError: expected 'except' or 'finally' block`;修复为正确的两行(用正则 `\s*#[^
]*?\s+(categories\s*=\s*\[)` 拆分)
- **`compileall -q papercrawler/` 通过**:本次修复后所有 .py 文件无语法错误

### 重构统计

- **新增**:
  - `papercrawler/export/csv_writer.py` 新增 `CSVReader` / `_row_to_paper` / `CSVReadError`(约 110 行)
- **修改**:
  - `papercrawler/cli/download.py`(`cmd_download` 加 `--from-csv` / `--skip-already`,参数互斥检查)
  - `papercrawler/models.py`(`SearchQuery.max_results` → `page_size`)
  - `papercrawler/search/manager.py`(`_split_year_range` 拆单年)
  - `papercrawler/config.py`(`CliDefaultsConfig` 类)
  - `config/papercrawler.toml`(新增 `[cli.defaults]` 节)
  - 7 个 search adapter:`max_results → page_size`,删 `[: query.page_size]` 截顶
- **测试新增**:
  - `tests/test_from_csv.py`(4 个测试,全部通过):
    - `test_csv_reader_basic` — CSVReader 解析标准 CSV(search 输出格式)
    - `test_csv_reader_skip_already` — `--skip-already` 过滤 `downloaded=true` 行
    - `test_csv_reader_errors` — 错误 CSV 处理(文件不存在 / 缺列)
    - `test_cli_from_csv_with_resume` — 集成测试:首次下载 1 篇 arXiv 论文 → 第二次同样命令全部 skipped → 第三次 `--force-global` 强制重下

---

## [1.1.0] - 2026-07-04

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
