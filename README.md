# PaperCrawler

> 面向"目标领域"的智能文献爬取工具:根据你定义的兴趣关键词,自动检索、两阶段过滤、下载并导出 CSV。

PaperCrawler 在经典论文检索/下载工具的基础上,提供 3 个差异化能力:

1. **🎯 两阶段领域相关性判定** — **阶段 1 粗筛**(基于 title,粗筛分数 ≥ 阈值才进入 enrich,节省 API 调用)+ **阶段 2 细筛**(基于 title + abstract + keywords,语义关键词命中数 ≥ N 才保留);两个门限独立判断
2. **🚫 反向关键词硬剔除** — `reverse_keywords` 命中即直接剔除,避免被误判的低相关论文进入下游(走严格 substring 匹配,不依赖 fuzzy)
3. **🔁 断点续下 + CSV 重下** — 全局 SQLite DB 自动记录已下载论文;任务中断后再次运行会自动跳过已完成的;另支持 `download --from-csv` 从已有 CSV 列表批量下载/续下

支持 6 个数据源(Semantic Scholar / OpenAlex / CrossRef / arXiv / CORE / ChemRxiv)并发检索,自动判断开放获取状态,下载全文 PDF,经 Microsoft MarkItDown 统一转换为 Markdown。

---

## 快速开始

```bash
# 安装
pip install -e .

# 可选:启用 Sci-Hub fallback(见下方说明)
pip install papercrawler[scihub]

# 初始化配置文件(在 ./config/ 下)
papercrawler config init
# 编辑 config/papercrawler.toml,至少填写 unpaywall_email

# ① 基础检索(纯关键词,无 [interest] 配置时)
papercrawler search -q "lithium battery SEI formation"

# ② 配置好 [interest] 后,自动启用全套(领域打分 + CSV 导出)
papercrawler search -q "solid state electrolyte" --year-from 2024 --year-to 2024

# ③ 一条命令:检索 → 过滤 → 导出 CSV → 下载 PDF
papercrawler search -q "solid state electrolyte" --year-from 2024 --year-to 2024 --download
```

### 典型工作流:检索 + 断点续下

```bash
# 第 1 步:检索(自动逐年翻页,可中断;输出 <日期>_<关键词>_<数量>.csv)
papercrawler search -q "solid state electrolyte" --year-from 2020 --year-to 2024 \
    --output-dir results/sse_2020_2024

# 第 2 步:查看自动命名的 CSV,确认目标论文
ls results/sse_2020_2024/*.csv
# → 20260705_solid_state_electrolyte_347.csv

# 第 3 步:首次下载(可以 Ctrl+C 中断)
papercrawler download --from-csv results/sse_2020_2024/20260705_solid_state_electrolyte_347.csv

# 第 4 步:续下(同样命令,自动跳过已下载)
papercrawler download --from-csv results/sse_2020_2024/20260705_solid_state_electrolyte_347.csv

# 第 5 步:重下全部(覆盖全局 DB)
papercrawler download --from-csv results/sse_2020_2024/20260705_solid_state_electrolyte_347.csv \
    --force-global
```

### 项目结构

```
PaperCrawler/
├── pyproject.toml              ← 包元数据
├── README.md
├── CHANGELOG.md
├── .gitignore
├── papercrawler/               ← 源代码包
│   ├── cli/                    ← CLI 入口包
│   │   ├── __init__.py         # app + 子命令注册
│   │   ├── _helpers.py         # 共享工具(setup/display/csv/run/CSV 命名等)
│   │   ├── search.py           # papercrawler search
│   │   ├── download.py         # papercrawler download / batch(含 --from-csv)
│   │   ├── convert.py          # papercrawler convert
│   │   ├── history.py          # papercrawler history list/stats
│   │   └── config.py           # papercrawler config init/show
│   ├── config.py
│   ├── classify/               ← 领域打分(粗筛 DomainFilter)+ 细筛(SemanticFilter)
│   ├── search/                 ← 6 个数据源适配器 + manager
│   ├── access/                 ← OA 状态判定
│   ├── download/               ← 下载器 + Sci-Hub + 存储 + DB
│   ├── convert/                ← MarkItDown 转换
│   ├── export/                 ← CSV 写入 + 读取(v1.3 新增)
│   └── utils/
├── tests/                      ← pytest 单测 + 端到端集成测试(含 test_from_csv.py)
├── config/                     ← 配置文件(不进 git)
│   ├── papercrawler.toml.example
│   └── papercrawler.toml       ← 你的实际配置(不进 git)
└── results/                    ← 每次运行的输出(不进 git)
    ├── sse_2020_2024/
    │   ├── papers/                            ← 下载的论文
    │   ├── _download_log.db
    │   ├── _index.md / _index.json
    │   └── 20260705_solid_state_electrolyte_347.csv   ← 自动命名的 CSV
    └── total_papers.csv                       ← 合并 CSV(全局)
```

---

## v1.3 新特性

### 1. 删除 `-n / --max-results`(语义改为 `page_size`)

v1.2 时代命令长这样:
```bash
papercrawler search -q "..." -n 50      # 截顶 50 条
```

v1.3 改为 **以单页大小翻页,直到 API 终止信号**(无硬性总结果上限):
```bash
papercrawler search -q "..."            # 默认翻页直到 API 终止
```

`page_size`(单次 API 请求的页大小)在 `config/papercrawler.toml` 的 `[cli.defaults]` 中配置,各 adapter 会在自己 API 上限内自动 clamp:

| Adapter | 默认 page_size | 内部 clamp |
|---|---|---|
| CrossRef | 100 | min(requested, 100) |
| OpenAlex | 100 | min(requested, 200) |
| ArXiv | 100 | min(requested, 1000) |
| Semantic Scholar | 100 | min(requested, 100) |
| CORE | 100 | min(requested, 100) |
| ChemRxiv (via Crossref) | 100 | min(requested, 100) |

如需调整,修改 `config/papercrawler.toml`:
```toml
[cli.defaults]
page_size = 200   # 单页 200 条,各 adapter 自动 clamp 到 API 上限
```

### 2. 逐年查询(避免数据源返回上限被截顶)

`SearchManager` 在年份范围跨度 > 1 年时,**自动拆为单年多次查询并汇总**,确保每个源对每一年都能翻到 API 终止信号:

```bash
papercrawler search -q "solid state electrolyte" --year-from 2020 --year-to 2024
# 自动拆为 2020/2021/2022/2023/2024 五次单年查询
```

单年查询时则不拆分(避免无谓的 API 开销)。

### 3. CLI 默认参数归 `config/papercrawler.toml`

`query` / `year_from` / `year_to` 等常用默认值移到配置文件,**命令行只跑功能**:
```toml
[cli.defaults]
query = "solid state electrolyte"
year_from = 2015
year_to = 2024
page_size = 100
sort = "relevance"   # relevance | date | citations
```

直接 `papercrawler search` 即使用 toml 中配置的默认值。

### 4. CSV 自动命名:`<日期>_<关键词>_<数量>.csv`

```bash
# 输入
papercrawler search -q "solid state electrolyte" --year-from 2024 --year-to 2024

# 输出(在 --output-dir 下,自动命名)
results/sse_2024/20260705_solid_state_electrolyte_347.csv
#           └──日期──┘└──关键词slug──┘└─入库数量─┘
```

文件以字母开头(无 `_` 前缀),日期在前便于排序,数量直观反映本次检索规模。

### 5. **`download --from-csv`** + 断点续下 ⭐

新命令支持从已有 CSV 批量下载/续下,典型用例见顶部"典型工作流"。

```bash
# 基础用法
papercrawler download --from-csv <file.csv>

# 跳过 CSV 中已 downloaded=true 的行(谨慎使用,推荐依赖全局 DB)
papercrawler download --from-csv <file.csv> --skip-already

# 重下全部(覆盖全局 DB)
papercrawler download --from-csv <file.csv> --force-global
```

**断点续下机制**:
- 全局 SQLite DB(`~/.papercrawler/_download_log.db`)记录所有已 `success` 的论文(DOI / hash_id / output_dir)
- 默认情况下,所有 `download` / `search --download` 子命令**自动跳过**已下载的论文
- `--force` 覆盖本次 run 的本地记录;`--force-global` 覆盖全局 DB

**实测**(见 `tests/test_from_csv.py`):第一次下载 1 篇 → 第二次同样命令全部 skipped → 第三次 `--force-global` 重新下载。

### 6. ChemRxiv 通过 CrossRef 集成(v1.3 整合)

ChemRxiv 直连经常被 Cloudflare 拦截(403),v1.3 改为通过 CrossRef 的 `query.bibliographic=` + DOI 前缀 `10.26434` 检索,绕开 Cloudflare。

数据源从 7 个减为 6 个(`PubMed` 在 v1.2 已关闭,SSE 命中率 < 5% 且不返回 abstract)。

---

## 自动启用行为

只要 `config/papercrawler.toml` 的 `[interest]` 节配置了**任一关键词**(must_have / should_have / exclude / reverse_keywords / semantic_keywords),以下行为**自动生效**(无需传 CLI 标志):

| 自动启用 | 行为 |
|---------|------|
| ✓ | **阶段 1 粗筛**:基于 `title` 打 `coarse_score`(0.0~1.0),≥ `--interest-threshold`(默认 0.6) 才进入 enrich |
| ✓ | **阶段 2 细筛**:基于 `title + abstract + keywords` 数 `semantic_keywords` 命中数,≥ `--semantic-min-matches`(默认 3) 才保留 |
| ✓ | **反向关键词硬剔除**:`reverse_keywords` 任一命中即直接丢弃(严格 substring) |
| ✓ | **CSV 自动导出**:本次 run 自动写 `<日期>_<关键词>_<数量>.csv` 到 `--output-dir` |

如果不希望启用,传 `--no-interest` 即可(纯检索 + enrich,不过滤)。

---

## 所有 CLI 命令(7 个)

```
papercrawler search          检索论文(关键词/作者/标题/DOI + 领域过滤 + CSV 自动导出)
papercrawler download        下载单篇(DOI/URL)或从已有 CSV 批量下载(--from-csv)
papercrawler batch           从任务文件批量执行检索+下载
papercrawler convert         将本地 PDF/HTML/DOCX 转换为 Markdown
papercrawler history         查询下载历史记录
papercrawler history list    列出最近下载(支持 --status / --output-dir)
papercrawler history stats   显示下载统计(成功率 / 失败分布)
papercrawler config          配置管理
papercrawler config init     在 ./config/ 生成默认 papercrawler.toml
papercrawler config show     显示当前生效配置
```

---

## `request_delay` 参数

**同域名请求之间的最小间隔**(秒)。由 `utils/rate_limiter.py` 实现,全局域名级控制:

| API | 免费层限制 | PaperCrawler 设置 |
|---|---|---|
| OpenAlex | 100k/天 | 1 秒 |
| CrossRef | 50 req/s(礼貌性) | 1 秒 |
| ArXiv | 1 req/3s(明确) | **3.5 秒** |
| Semantic Scholar | 100 req/5min(无 key) | **3 秒** |
| Unpaywall | 100k/月 | 1 秒 |
| CORE | 10 req/10s(无 key) | 没 key 跳过 |
| ChemRxiv | 限速 + UA 检测 | 1 秒(常 403) |

---

## 配置参考

完整字段说明见 `config/papercrawler.toml.example`,核心节:

| 节 | 用途 |
|---|---|
| `[api_keys]` | 各 API 的 key(S2 / OpenAlex / CORE / Unpaywall / Crossref / PubMed) |
| `[download]` | 超时 / 重试 / 并发 / request_delay / Unpaywall email |
| `[filters]` | OA 过滤、年份范围、作者匹配阈值 |
| `[interest]` ✨ | **用户兴趣描述、关键词权重、反向关键词、细筛词** |
| `[cli.defaults]` ✨ | CLI 默认值(query/year_from/year_to/page_size/sort) |
| `[scihub]` | Sci-Hub fallback 配置(默认关闭) |

---

## Sci-Hub Fallback(可选)⚠️

> 法律提示:Sci-Hub 在美国、欧盟等地区属于版权侵权行为。请确认当地法规。

```bash
pip install papercrawler[scihub]
```

```toml
[scihub]
enabled = true
proxy = ""   # 如需代理:"socks5://127.0.0.1:7890"
```

---

## 架构与代码组织

### CLI 包结构

```
papercrawler/
├── cli/                  ← CLI 包
│   ├── __init__.py       # app + 子命令注册
│   ├── __main__.py       # 允许 `python -m papercrawler.cli`
│   ├── _helpers.py       # 共享工具:setup / display / csv / run / 命名生成
│   ├── search.py         # cmd_search
│   ├── download.py       # cmd_download(支持 --from-csv) + cmd_batch
│   ├── convert.py        # cmd_convert
│   ├── history.py        # history list/stats
│   └── config.py         # config init/show
```

### Search 适配器

```
papercrawler/search/
├── base.py        # BaseSearchAdapter + SourceError 异常类
├── manager.py     # SearchManager + SourceStats(分类失败计数表)
├── arxiv.py
├── openalex.py
├── crossref.py
├── semantic_scholar.py
├── core.py
└── chemrxiv_via_crossref.py   # 通过 Crossref 检索 ChemRxiv(DOI 前缀 10.26434)
```

每个 adapter 只需实现 `SOURCE_ID` + `async search()` + `_parse()`,所有 HTTP / 限速 / 重试 / 异常分类由 `BaseSearchAdapter` 提供。每个 adapter 都带**翻页**(cursor / offset / start)+ **跨页 DOI 去重**+ **max_pages 硬上限**(40-100 页/源)+ **终止信号**(next_cursor=null 或 total 耗尽)。

### 异常分类

所有错误不再被 `except Exception` 静默吞掉,而是按 `SourceError.kind` 分类:

| kind | 含义 | 上游原因 |
|------|------|---------|
| `http_error` | HTTP 4xx/5xx | 401 / 403 / 404 / 500 |
| `parse_error` | JSON/XML 解析失败 | 服务端返回了非预期格式 |
| `timeout` | 请求超时 | 网络慢 / 服务端 hang |
| `rate_limit` | 速率限制持续 | 重试 2 次后仍 429 |
| `other` | 未预期异常 | 带 traceback 写入日志 |

`SearchManager.search_with_stats()` 返回 `dict[str, SourceStats]`,CLI 表格会按失败类型标色。

### enrich 阶段(已知瓶颈)

`MetadataExtractor.enrich_batch(papers)` 对**每篇论文**:
1. 缺 abstract → S2 `paper/DOI:{doi}` → 失败再用 CrossRef `/works/{doi}`
2. AccessChecker.check → arXiv 直链 → Unpaywall → 出版商 HEAD 探测

并发 5(`asyncio.Semaphore(5)`),**这是当前最大的瓶颈**(~1500 篇 enrich 要 30 分钟)。

---

## 合规声明

本工具默认仅通过以下合法开放获取渠道下载全文:Unpaywall、PubMed Central、arXiv、OpenAlex、Semantic Scholar、CORE、ChemRxiv。下载内容仅供个人学术研究使用。Sci-Hub 功能需用户显式启用,并由用户自行承担相关法律责任。

---

## 鸣谢

PaperCrawler 在原 `paper-dl` 基础上,增加了领域感知(两阶段打分 + 反向关键词剔除)、逐年翻页检索、全局断点续下、CSV 重下 与 自动 CSV 命名导出能力。如需回退到无领域过滤的传统模式,只需不传 `--interest` 即可,行为与 `paper-dl` 完全一致。