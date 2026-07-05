# PaperCrawler

> 面向"目标领域"的智能文献爬取工具:根据你定义的兴趣关键词,自动检索、两阶段过滤、下载并导出 CSV。

PaperCrawler 在经典论文检索/下载工具的基础上,新增三个关键能力:

1. **🎯 两阶段领域相关性判定** — **阶段 1 粗筛**(基于 title,粗筛分数 ≥ 阈值才进入 enrich,节省 API 调用)+ **阶段 2 细筛**(基于 title + abstract + keywords,语义关键词命中数 ≥ N 才保留);两个门限独立判断
2. **🚫 反向关键词硬剔除** — `reverse_keywords` 命中即直接剔除,避免被误判的低相关论文进入下游(走严格 substring 匹配,不依赖 fuzzy)
3. **📊 CSV 自动命名导出** — 单次 run 自动生成 `<must_have 关键词>_<年月日>_<入库数量>.csv`,免去手动命名

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
papercrawler search -q "lithium battery SEI formation" -n 10

# ② 配置好 [interest] 后,自动启用全套(领域打分 + CSV 导出)
papercrawler search -q "solid state electrolyte" --year-from 2024 --year-to 2024

# ③ 一条命令:检索 → 过滤 → 导出 → 下载
papercrawler search -q "solid state electrolyte" --download -n 50
```

### 项目结构

```
PaperCrawler/
├── pyproject.toml              ← 包元数据(不动)
├── README.md
├── CHANGELOG.md
├── .gitignore
├── papercrawler/               ← 源代码包
│   ├── cli/                    ← CLI 入口包
│   │   ├── __init__.py         # app + 子命令注册
│   │   ├── _helpers.py         # 共享工具(setup/display/csv/run/CSV 命名等)
│   │   ├── search.py           # papercrawler search
│   │   ├── download.py         # papercrawler download / batch
│   │   ├── convert.py          # papercrawler convert
│   │   ├── history.py          # papercrawler history list/stats
│   │   └── config.py           # papercrawler config init/show
│   ├── config.py
│   ├── classify/               ← 领域打分(粗筛 DomainFilter)+ 细筛(SemanticFilter)
│   ├── search/                 ← 6 个数据源适配器 + manager
│   ├── access/                 ← OA 状态判定
│   ├── download/               ← 下载器 + Sci-Hub + 存储
│   ├── convert/                ← MarkItDown 转换
│   ├── export/                 ← CSV 导出
│   └── utils/
├── tests/                      ← pytest 单测 + 端到端集成测试
├── config/                     ← 配置文件(不进 git)
│   ├── papercrawler.toml.example
│   └── papercrawler.toml       ← 你的实际配置(不进 git)
└── results/                    ← 每次运行的输出(不进 git)
    ├── 2026-07-05__solid_state_electrolyte/   ← 一次 search
    │   ├── papers/                            ← 下载的论文
    │   ├── _download_log.db
    │   ├── _index.md / _index.json
    │   └── solid_state_electrolyte_20260705_62.csv  ← 自动命名的 CSV
    └── total_papers.csv                       ← 合并 CSV(全局)
```

### 自动启用行为(选项 A)

只要 `config/papercrawler.toml` 的 `[interest]` 节配置了**任一关键词**(must_have / should_have / exclude),以下行为**自动生效**(无需传 CLI 标志):

| 自动启用 | 行为 |
|---------|------|
| ✓ | **阶段 1 粗筛**:基于 `title` 打 `coarse_score`(0.0~1.0),≥ `--interest-threshold`(默认 0.6) 才进入 enrich |
| ✓ | **阶段 2 细筛**:基于 `title + abstract + keywords` 数 `semantic_keywords` 命中数,≥ `--semantic-min-matches`(默认 3) 才保留 |
| ✓ | **反向关键词剔除**:`reverse_keywords` 命中即 `is_reversed=True`,score=0,完全不进 enrich |
| ✓ | 按 `must_have` / `should_have` / `exclude` 算粗筛分 |
| ✓ | 导出 CSV 到 `results/<本次运行>/<must_have>_<年月日>_<数量>.csv`,含 `coarse_score` 与 `semantic_score` 两列 |
| ✓ | 同步生成合并 CSV `results/total_papers.csv` |

**显式 opt-out**(用 `--no-X` 关闭单次):
```bash
papercrawler search -q "..." --no-csv          # 本次不导出 CSV
papercrawler search -q "..." --no-interest     # 本次不打分(纯检索)
```

---

## 🎯 核心新特性:领域感知

### 配置文件 `papercrawler.toml`

```toml
[interest]
description = """
固态电解质方向文献检索:
- 单关键词 "solid state electrolyte" 通过 -q 检索
- 反向词剔除不相关方向(聚合物 / 液态 / 燃料电池 / 锌电 / 催化等)
"""

# 关键词权重(影响粗筛打分)
must_have    = ["solid state electrolyte"]      # 命中 → 基础 0.6
should_have  = []                               # 每个 +0.1(默认空)
exclude      = []                               # 命中 → -0.5(默认空)

# 反向关键词 — 命中即直接剔除(严格 substring,不依赖 fuzzy)
reverse_keywords = [
    "polymer", "liquid", "aqueous", "gel",     # 非固态电解质形态
    "zinc-ion battery", "zinc ion battery",
    "fuel cell", "supercapacitor", "solar cell", "photovoltaic",
    "catalysis", "catalyst", "electrocatalyst", "electrocatalysis",
    "oxygen reduction", "oxygen evolution", "hydrogen evolution",
    "CO2 reduction", "combustion", "pyrolysis",
    "ReaxFF", "reactive force field", "reactive molecular dynamics",
    "proton conductor", "proton conductivity", "thermal conductivity",
    "anode material",
]

# 细筛关键词(命中数 ≥ semantic_min_matches 才保留)
semantic_keywords = [
    # 体系大类
    "solid state electrolyte", "LLZO", "LPS", "garnet", "NASICON",
    "lithium", "Li-ion", "sodium", "inorganic",
    # 物相 / 结构
    "amorphous", "sulfide", "sulphide", "halide", "oxide",
    # 离子电导
    "ionic conductivity", "Na3SbS4", "superionic conductor",
]
semantic_min_matches = 3   # 至少命中 3 个关键词才保留

# 模糊匹配阈值(0.0~1.0,0.7 = 较宽松)
fuzzy_threshold = 0.7
```

### 领域相关性打分(两阶段)

**阶段 1 粗筛**(`domain_filter.py`)— 基于 `title` 的纯规则打分,在 enrich 前执行,**节省 70% API 调用**:

```
coarse_score = 0.0
  + 0.6  if any must_have keyword in title
  + 0.1  per each should_have keyword in title (cap at 0.3)
  - 0.5  if any exclude keyword in title

# 反向关键词在 must_have 检查之前优先处理:
# if any reverse_keyword in title/abstract: score = 0, is_reversed=True
```

**阶段 2 细筛**(`semantic_filter.py`)— enrich 后用 `title + abstract + keywords` 数 `semantic_keywords` 命中数(整数,0~N):

```
semantic_score = count(matches(semantic_keywords, text))
```

**双门限硬过滤**(任一不达标都剔除):

```
保留 IF coarse_score >= --interest-threshold AND semantic_score >= --semantic-min_matches
```

**匹配策略**:
- `must_have` / `should_have` / `exclude` / `semantic_keywords` → 先 substring 匹配,失败时 token 级 fuzzy(`difflib` 相似度 ≥ `fuzzy_threshold`)
- `reverse_keywords` → **仅严格 substring 匹配**(不走 fuzzy)。原因:fuzzy 在多 token 关键词上会把 `"aqueous electrolyte"` 的最长 token `"electrolyte"` 与 `"solid-state electrolyte"` 视为命中,误杀所有相关论文

---

## 支持的数据源(6 个,2026-07-05 关闭 PubMed)

| 数据源 | 覆盖领域 | 是否需要 API Key | 说明 |
|--------|---------|----------------|------|
| Semantic Scholar | 全领域 | 可选(提升速率) | 含引用数、参考文献数 |
| OpenAlex | 全领域 | 否 | 覆盖广,含开放获取状态 |
| CrossRef | 全领域 | 否 | 元数据最全(卷/期/页) |
| ~~PubMed~~ | ~~生物医学~~ | — | **2026-07-05 关闭**(SSE 命中率 <5%,且不返回 abstract) |
| arXiv | 预印本 | 否 | 全部为开放获取 |
| CORE | OA 论文聚合 | 是(免费注册) | 聚合多源开放获取 PDF |
| ChemRxiv | 化学预印本 | 否 | ACS 官方 API,无需 key |

在 `papercrawler.toml` 的 `[sources]` 中可启用/禁用各数据源。

---

## 多维复合检索

`papercrawler search` 支持将多个检索维度**同时组合使用**,各条件之间为 AND 关系。

### 支持的检索维度

| 维度 | 选项 | 说明 |
|------|------|------|
| 关键词 | `-q / --query` | 全文/摘要关键词 |
| 作者 | `-a / --author` | 作者姓名(支持模糊匹配评分) |
| 标题 | `-t / --title` | 论文题目关键词 |
| DOI | `-d / --doi` | 精确 DOI 匹配 |
| 年份范围 | `--year-from / --year-to` | 发表年份区间 |
| 仅开放获取 | `--oa-only` | 过滤出可免费获取全文的论文 |
| 指定数据源 | `--source` | 逗号分隔,如 `arxiv,openalex` |
| 排序方式 | `--sort` | `relevance` / `date` / `citations` |
| **领域过滤** ✨ | `--interest` | 启用 `[interest]` 配置进行相关性判定 |
| **领域阈值** | `--interest-threshold` | 最低粗筛分(默认 0.6,单 must_have 命中 = 0.6) |
| **细筛最小命中数** | `--semantic-min-matches` | 默认 3 |
| **CSV 导出** ✨ | `--csv PATH` | 导出符合条件的结果到 CSV(自动命名 `<must_have>_<日期>_<数量>.csv`) |
| 作者匹配阈值 | `--min-author-score` | 配合 `-a` 使用,过滤低置信结果 |

### 典型多维检索示例

```bash
# 示例 1:SSE 单关键词 + 领域过滤 + CSV 自动导出
papercrawler search -q "solid state electrolyte" \
    --year-from 2024 --year-to 2024 -n 5000

# 示例 2:SSE + 多关键词 OR + 标题限定
papercrawler search -q "LLZO LPS sulfide" \
    --year-from 2020 --year-to 2024 -n 1000 \
    --sort citations

# 示例 3:作者 + 关键词 + 下载
papercrawler search -a "John Goodenough" -q "lithium" \
    --year-from 2015 --year-to 2024 --download \
    --output-dir ./goodenough_papers

# 示例 4:关键词 + 年份 + 仅 OA + 指定数据源 + 按引用数排序
papercrawler search -q "garnet NASICON oxide" \
    --year-from 2017 --year-to 2024 \
    --oa-only --source arxiv,openalex \
    --sort citations -n 50

# 示例 5:完整流水线:检索 → 过滤 → CSV 自动命名 → 下载
papercrawler search -q "sodium superionic conductor" \
    --interest --interest-threshold 0.6 \
    --semantic-min-matches 2 \
    --download -n 100 --output-dir ./NaSSE
```

---

## 输出结构

```
results/
└── 2026-07-05__solid_state_electrolyte/   ← 一次 search 的输出目录
    ├── papers/                            ← 下载的论文子目录
    │   ├── <sanitized-title-1>/
    │   │   ├── metadata.json              # 结构化元数据(含 coarse_score / semantic_score)
    │   │   ├── paper.md                   # MarkItDown 转换的 Markdown
    │   │   └── paper.pdf                  # 原始 PDF(若已下载全文)
    │   └── ...
    ├── _index.md                          # 论文索引(Markdown 表格)
    ├── _index.json                        # 论文索引(JSON)
    ├── _download_log.db                   # 本次 run 下载历史 SQLite
    └── solid_state_electrolyte_20260705_62.csv   ← 自动命名的本次 run CSV

results/total_papers.csv                   ← 全局合并 CSV(跨 run 去重)
```

### CSV 文件命名规则(2026-07-05)

| 文件 | 命名规则 | 示例 |
|---|---|---|
| 本次 run CSV | `<must_have 关键词 slug>_<YYYYMMDD>_<入库数量>.csv` | `solid_state_electrolyte_20260705_62.csv` |
| 全局合并 CSV | 固定名 `total_papers.csv` | `results/total_papers.csv` |

> slug 规则:`"solid state electrolyte"` → `"solid_state_electrolyte"`,只保留字母 / 数字,其他变 `_`,合并连续 `_`,转小写。所有输出文件均以字母开头(无下划线前缀)。

### CSV 字段说明(12 列)

| 字段 | 说明 |
|------|------|
| `title` | 论文标题 |
| `doi` | DOI(若无则为空) |
| `year` | 发表年份 |
| `journal` | 期刊/来源 |
| `authors` | 作者列表(`;` 分隔) |
| `coarse_score` | 阶段 1 粗筛分数(0~1) |
| `semantic_score` | 阶段 2 细筛命中关键词数(整数) |
| `citations` | 引用数 |
| `access_status` | OA 状态(`oa_pdf` / `oa_preprint` / `metadata_only` 等) |
| `oa_url` | 开放获取 URL |
| `sources` | 来源数据源(`;` 分隔) |
| `downloaded` | 是否已下载(`true` / `false`) |

> 注:2026-07-05 移除 `categories` 列(分类功能下线)。

---

## 配置文件

| 配置节 | 说明 |
|--------|------|
| `[general]` | 输出目录、默认结果数、排序方式 |
| `[download]` | 并发数、超时、重试次数、User-Agent |
| `[sources]` | 启用的数据源列表 |
| `[api_keys]` | Semantic Scholar / CORE / Unpaywall 邮箱 |
| `[markitdown]` | MarkItDown 格式转换开关 |
| `[filters]` | OA 过滤、年份范围、作者匹配阈值 |
| `[interest]` ✨ | **用户兴趣描述、关键词权重、反向关键词、细筛词** |
| `[scihub]` | Sci-Hub fallback 配置(默认关闭) |

### `request_delay` 参数(2026-07-05 解释)

**同域名请求之间的最小间隔**(秒)。由 `utils/rate_limiter.py` 实现,全局域名级控制:

```python
limiter.set_delay("api.semanticscholar.org", 3.0)   # S2 无 key 设 3 秒
limiter.set_delay("export.arxiv.org", 3.5)          # arXiv 强制 ≥ 3 秒
```

**作用**:避免触发各 API 的 rate limit。各 API 免费层限制 + PaperCrawler 设置:

| API | 免费层限制 | PaperCrawler 设置 |
|---|---|---|
| OpenAlex | 100k/天 | 1 秒 |
| CrossRef | 50 req/s(礼貌性) | 1 秒 |
| ArXiv | 1 req/3s(明确) | **3.5 秒** |
| Semantic Scholar | 100 req/5min(无 key) | **3 秒** |
| Unpaywall | 100k/月 | 1 秒 |
| CORE | 10 req/10s(无 key) | 没 key 跳过 |
| ChemRxiv | 限速 + UA 检测 | 1 秒(常 403) |

### `fuzzy_threshold` 参数

模糊匹配阈值。粗筛 / 细筛在判断"某个关键词是否命中论文文本"时,有两种策略:

| 策略 | 实现 | 适用 |
|---|---|---|
| **严格 substring** | `"MLIP" in title` | `reverse_keywords`(黑名单,误杀代价高) |
| **fuzzy 模糊匹配** | `difflib.SequenceMatcher` 计算字符相似度 | `must_have`/`should_have`/`exclude`/`semantic_keywords`(召回,放过一篇不相关的代价低) |

fuzzy 在 token 级别匹配,对 multi-word keyword 取**最长 token**参与计算。例:keyword = `"molecular dynamics"`,title token = `"molcular"`(漏字母)→ 相似度 ≈ 0.85,>= 0.7 → **命中**。

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

## 所有 CLI 命令(6 个)

```
papercrawler search        检索论文(关键词/作者/标题/DOI + 领域过滤 + CSV 自动导出)
papercrawler download      通过 DOI 或 URL 直接下载单篇论文
papercrawler batch         从文件批量执行检索+下载任务
papercrawler convert       将本地 PDF/HTML/DOCX 转换为 Markdown
papercrawler history       查询下载历史记录
papercrawler history list  列出最近下载(支持 --status / --output-dir)
papercrawler history stats 显示下载统计(成功率 / 失败分布)
papercrawler config        配置管理
papercrawler config init   在 ./config/ 生成默认 papercrawler.toml
papercrawler config show   显示当前生效配置
```

> 2026-07-05 移除 `papercrawler recategorize` 命令(分类功能下线)。

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
│   ├── download.py       # cmd_download + cmd_batch
│   ├── convert.py        # cmd_convert
│   ├── history.py        # history list/stats
│   └── config.py         # config init/show
```

### Search 适配器

```
papercrawler/search/
├── base.py        # BaseSearchAdapter + SourceError 异常类
├── manager.py     # SearchManager + SourceStats(分类失败计数表)
├── arxiv.py       # JSON / XML 端点共用 _get_json / _get_text
├── openalex.py
├── crossref.py
├── semantic_scholar.py
├── core.py
└── chemrxiv.py
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

PaperCrawler 在原 `paper-dl` 基础上,增加了领域感知(两阶段打分 + 反向关键词剔除)与自动 CSV 命名导出能力。如需回退到无领域过滤的传统模式,只需不传 `--interest` 即可,行为与 `paper-dl` 完全一致。