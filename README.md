# PaperCrawler

> 面向"目标领域"的智能文献爬取工具:根据你定义的兴趣关键词与分类,自动检索、过滤、分类、下载并导出 CSV。

PaperCrawler 在经典论文检索/下载工具的基础上,新增了三个关键能力:

1. **🎯 两阶段领域相关性判定** — **阶段 1 粗筛**(基于 title,粗筛分数≥阈值才进入 enrich,节省 API 调用)+ **阶段 2 细筛**(基于 title+abstract+keywords,语义关键词命中数 ≥ N 才保留);两个门限独立判断
2. **🏷️ 自动多标签分类** — 一篇论文可同时被分到 `材料实验合成 / 理论计算 / 力场开发` 等多个类别
3. **📊 CSV 导出** — 把符合条件的 `title / DOI / 分类 / 粗筛分 / 细筛命中数` 导出为 CSV,方便二次分析

支持 7 个数据源(Semantic Scholar / OpenAlex / CrossRef / PubMed / arXiv / CORE / ChemRxiv)并发检索,自动判断开放获取状态,下载全文 PDF,经 Microsoft MarkItDown 统一转换为 Markdown。

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

# ② 配置好 [interest] 后,自动启用全套(领域过滤 + 分类 + CSV 导出)
papercrawler search -q "solid electrolyte interface"

# ③ 一条命令:检索 → 过滤 → 分类 → 导出 → 下载
papercrawler search -q "ReaxFF force field" --download
```

### 项目结构

```
PaperCrawler/
├── pyproject.toml              ← 包元数据(不动)
├── README.md
├── CHANGELOG.md
├── .gitignore
├── papercrawler/               ← 源代码包
│   ├── cli/                    ← CLI 入口包(2026 重构)
│   │   ├── __init__.py         # app + 子命令注册
│   │   ├── _helpers.py         # 共享工具(setup/display/csv/run 等)
│   │   ├── search.py           # papercrawler search
│   │   ├── download.py         # papercrawler download / batch
│   │   ├── recategorize.py     # papercrawler recategorize
│   │   ├── convert.py          # papercrawler convert
│   │   ├── history.py          # papercrawler history list/stats
│   │   └── config.py           # papercrawler config init/show
│   ├── config.py
│   ├── classify/               ← 领域打分(粗筛 DomainFilter)+ 细筛(SemanticFilter)+ 分类(Categorizer)
│   ├── search/                 ← 7 个数据源适配器 + manager
│   ├── access/                 ← OA 状态判定
│   ├── download/               ← 下载器 + Sci-Hub + 存储
│   ├── convert/                ← MarkItDown 转换
│   ├── export/                 ← CSV 导出
│   └── utils/
├── tests/                      ← 54 个 pytest 单测 + 端到端集成测试
├── config/                     ← 配置文件(原来在根目录,现在统一这里)
│   ├── papercrawler.toml.example
│   └── papercrawler.toml       ← 你的实际配置(不进 git)
└── results/                    ← 每次运行的输出(不进 git)
    ├── 2026-07-03__solid_electrolyte/    ← 一次 search
    │   ├── papers/                       ← 下载的论文
    │   ├── _download_log.db
    │   ├── _index.md / _index.json
    │   └── _interest_filtered.csv         ← 自动导出的 CSV
    ├── 2026-07-04__reaxff_lithium/
    └── migrated_2026-07-03_xxx/          ← 旧数据(迁移自改造前)
```

### 自动启用行为(选项 A)

只要 `config/papercrawler.toml` 的 `[interest]` 节配置了**任一关键词或分类**,以下行为**自动生效**(无需传 CLI 标志):

| 自动启用 | 行为 |
|---------|------|
| ✓ | **阶段 1 粗筛**:基于 `title` 打 `coarse_score` (0.0~1.0),≥ `--interest-threshold`(默认 0.7) 才进入 enrich |
| ✓ | **阶段 2 细筛**:基于 `title + abstract + keywords` 数 `semantic_keywords` 命中数,≥ `--semantic-min-matches`(默认 3) 才保留 |
| ✓ | 按 `must_have` / `should_have` / `exclude` 算粗筛分 |
| ✓ | 按 `categories` 给每篇论文打多分类标签 |
| ✓ | 导出 CSV 到 `results/<本次运行>/_interest_filtered.csv`,含 `coarse_score` 与 `semantic_score` 两列 |

**显式 opt-out**(用 `--no-X` 关闭单次):
```bash
papercrawler search -q "..." --no-csv          # 本次不导出 CSV
papercrawler search -q "..." --no-categorize   # 本次不打分类
papercrawler search -q "..." --no-interest     # 本次不打分
```

**给已下载的论文补跑分类**:
```bash
papercrawler recategorize                              # 扫描 ./results/ 全部
papercrawler recategorize -o ./results/migrated_xxx    # 指定目录
papercrawler recategorize --interest-threshold 0.5     # 过滤并导出 CSV
```

---

## 🎯 核心新特性:领域感知

### 配置文件 `papercrawler.toml`

```toml
[interest]
description = """
材料基因组与计算材料学,重点关注:
- 锂离子电池固态电解质(LLZO, LPS, 硫化物)
- 分子动力学力场开发(ReaxFF, MLIP, MACE)
- 第一性原理计算(DFT, AIMD)
- 实验合成与表征
"""

# 关键词权重(影响打分)
must_have    = ["solid electrolyte", "force field"]   # 命中 → 基础 0.6
should_have  = ["Li-ion", "MD simulation", "DFT"]     # 每个 +0.1
exclude      = ["review", "perspective"]               # 命中 → -0.5

# 自定义分类(每类匹配命中即打标签)
[[interest.categories]]
name = "材料实验合成"
keywords = ["synthesis", "sintering", "XRD", "calcination", "sol-gel"]

[[interest.categories]]
name = "理论计算"
keywords = ["DFT", "first-principles", "VASP", "ab initio", "第一性原理"]

[[interest.categories]]
name = "力场开发"
keywords = ["ReaxFF", "force field", "MLIP", "MACE", "MACE-OFF", "potential development"]

[[interest.categories]]
name = "分子动力学模拟"
keywords = ["molecular dynamics", "AIMD", "LAMMPS", "MD simulation", "GROMACS"]
```

### 领域相关性打分(两阶段)

**阶段 1 粗筛**(`domain_filter.py`)— 基于 `title` 的纯规则打分,在 enrich 前执行,**节省 70% API 调用**:

```
coarse_score = 0.0
  + 0.6  if any must_have keyword in title
  + 0.1  per each should_have keyword in title (cap at 0.3)
  - 0.5  if any exclude keyword in title
  + 0.1  bonus per category match in title (cap at 0.2)
```

**阶段 2 细筛**(`semantic_filter.py`)— enrich 后用 `title + abstract + keywords` 数 `semantic_keywords` 命中数(整数,0~N):

```
semantic_score = count(matches(semantic_keywords, text))
```

**双门限硬过滤**(任一不达标都剔除):

```
保留 IF coarse_score >= --interest-threshold AND semantic_score >= --semantic-min-matches
```

支持模糊匹配:`reaxff` ≈ `ReaxFF`(用 `difflib` 字符相似度 ≥ 0.85 视为命中)。

### 自动分类(`categorizer.py`)

每篇论文**可同时属于多类**,分类结果存入 `paper.categories` 字段,导出 CSV 时用 `;` 分隔。

---

## 支持的数据源

| 数据源 | 覆盖领域 | 是否需要 API Key | 说明 |
|--------|---------|----------------|------|
| Semantic Scholar | 全领域 | 可选(提升速率) | 含引用数、参考文献数 |
| OpenAlex | 全领域 | 否 | 覆盖广,含开放获取状态 |
| CrossRef | 全领域 | 否 | 元数据最全(卷/期/页) |
| PubMed | 生物医学 | 可选 | NCBI E-utilities |
| arXiv | 预印本 | 否 | 全部为开放获取 |
| CORE | OA 论文聚合 | 是(免费注册) | 聚合多源开放获取 PDF |
| **ChemRxiv** | **化学预印本** | **否** | **ACS 官方 API,全部免费下载** |

在 `papercrawler.toml` 的 `[sources]` 中可启用/禁用各数据源。

---

## 多维复合检索

`papercrawler search` 支持将多个检索维度**同时组合使用**,各条件之间为 AND 关系。

### 支持的检索维度

| 维度 | 选项 | 说明 |
|------|------|------|
| 关键词 | `-q / --query` | 全文/摘要关键词 |
| 作者 | `-a / --author` | 作者姓名(支持模糊匹配评分) |
| 标题 | `-t / --title` | 论文标题关键词 |
| DOI | `-d / --doi` | 精确 DOI 匹配 |
| 年份范围 | `--year-from / --year-to` | 发表年份区间 |
| 仅开放获取 | `--oa-only` | 过滤出可免费获取全文的论文 |
| 指定数据源 | `--source` | 逗号分隔,如 `arxiv,openalex` |
| 排序方式 | `--sort` | `relevance` / `date` / `citations` |
| **领域过滤** ✨ | `--interest` | 启用 `[interest]` 配置进行相关性判定 |
| **领域阈值** | `--interest-threshold` | 最低匹配分数(默认 0.0) |
| **自动分类** ✨ | `--categorize` | 对结果按 `[interest.categories]` 打标签 |
| **CSV 导出** ✨ | `--csv PATH` | 导出符合条件的结果到 CSV |
| 作者匹配阈值 | `--min-author-score` | 配合 `-a` 使用,过滤低置信结果 |

### 典型多维检索示例

```bash
# 示例 1:关键词 + 领域过滤 + 分类 + CSV 导出
papercrawler search -q "solid electrolyte interface" \
    --year-from 2020 --year-to 2024 \
    --interest --interest-threshold 0.5 \
    --categorize --csv ./sei_papers.csv

# 示例 2:作者 + 关键词 + 领域过滤 + 下载
papercrawler search -a "John Newman" -q "electrochemical" \
    --interest --categorize --download \
    --output-dir ./newman_papers

# 示例 3:关键词 + 标题 + 年份 + 指定数据源 + 按引用数排序
papercrawler search -q "transformer attention mechanism" \
    -t "attention is all you need" \
    --year-from 2017 \
    --source semantic_scholar,openalex,crossref \
    --sort citations -n 20

# 示例 4:作者 + 年份 + 仅 OA + 指定化学预印本源
papercrawler search -a "Omar Yaghi" \
    --year-from 2022 --year-to 2024 \
    --oa-only --source chemrxiv,openalex \
    -n 30 --download

# 示例 5:完整流水线:检索 → 过滤 → 分类 → 导出 → 下载
papercrawler search -q "ReaxFF Li battery" \
    --interest --categorize \
    --interest-threshold 0.5 \
    --csv ./matched.csv --download \
    --output-dir ./papers
```

---

## 输出结构

```
papers/
├── 2017_vaswani_attention_all_you_need_1706.03762/
│   ├── metadata.json    # 结构化元数据(含 categories / match_score)
│   ├── paper.md         # MarkItDown 转换的 Markdown(含 YAML front matter)
│   └── paper.pdf        # 原始 PDF(若已下载全文)
├── _index.md            # 论文索引(Markdown 表格)
├── _index.json          # 论文索引(JSON)
└── _download_log.db     # 下载历史 SQLite 数据库

matched_papers.csv       # 导出的符合领域条件的论文清单
```

### CSV 字段说明

| 字段 | 说明 |
|------|------|
| `title` | 论文标题 |
| `doi` | DOI(若无则为空) |
| `year` | 发表年份 |
| `journal` | 期刊/来源 |
| `authors` | 作者列表(`;` 分隔) |
| `categories` | 自动分类标签(`;` 分隔,可多个) |
| `coarse_score` | 阶段 1 粗筛分数(0~1) |
| `semantic_score` | 阶段 2 细筛命中关键词数(整数) |
| `interest_score` | 兼容字段(语义过滤后的保留分数,可选) |
| `access_status` | OA 状态(`oa_pdf` / `oa_preprint` / `metadata_only` 等) |
| `oa_url` | 开放获取 URL |
| `downloaded` | 是否已下载(`true` / `false`) |
| `sources` | 来源数据源(`;` 分隔) |

---

## 配置文件

| 配置节 | 说明 |
|--------|------|
| `[general]` | 输出目录、默认结果数、排序方式 |
| `[download]` | 并发数、超时、重试次数、User-Agent |
| `[sources]` | 启用的数据源列表 |
| `[api_keys]` | Semantic Scholar / PubMed / CORE / Unpaywall 邮箱 |
| `[markitdown]` | MarkItDown 格式转换开关 |
| `[filters]` | OA 过滤、年份范围、作者匹配阈值 |
| `[interest]` ✨ | **用户兴趣描述、关键词权重、自定义分类** |
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

## 所有 CLI 命令

```
papercrawler search        检索论文(关键词/作者/标题/DOI + 领域过滤 + 分类 + CSV 导出)
papercrawler download      通过 DOI 或 URL 直接下载单篇论文
papercrawler batch         从文件批量执行检索+下载任务
papercrawler convert       将本地 PDF/HTML/DOCX 转换为 Markdown
papercrawler recategorize  对已下载论文补跑领域打分 + 分类(可写回 metadata.json)
papercrawler history       查询下载历史记录
papercrawler history list  列出最近下载(支持 --status / --output-dir)
papercrawler history stats 显示下载统计(成功率 / 失败分布)
papercrawler config        配置管理
papercrawler config init   在 ./config/ 生成默认 papercrawler.toml
papercrawler config show   显示当前生效配置
```

---

## 架构与代码组织

### CLI 包结构(2026 重构后)

```
papercrawler/
├── cli/                  ← CLI 包(原 cli.py 996 行已拆分)
│   ├── __init__.py       # app + 子命令注册
│   ├── __main__.py       # 允许 `python -m papercrawler.cli`
│   ├── _helpers.py       # 共享工具:setup / display / csv / run / parse_task_file
│   ├── search.py         # cmd_search
│   ├── download.py       # cmd_download + cmd_batch
│   ├── recategorize.py   # cmd_recategorize
│   ├── convert.py        # cmd_convert
│   ├── history.py        # history list/stats
│   └── config.py         # config init/show
```

### Search 适配器模板(2026 重构后)

```
papercrawler/search/
├── base.py        # BaseSearchAdapter + SourceError 异常类
├── manager.py     # SearchManager + SourceStats(分类失败计数表)
├── arxiv.py       # JSON / XML 端点共用 _get_json / _get_text
├── pubmed.py      # JSON (esearch) + XML (efetch)
├── openalex.py
├── crossref.py
├── semantic_scholar.py
├── core.py
└── chemrxiv.py
```

每个 adapter 只需实现 `SOURCE_ID` + `async search()` + `_parse()`,所有 HTTP / 限速 / 重试 / 异常分类由 `BaseSearchAdapter` 提供。

### 异常分类(2026 重构后)

所有错误不再被 `except Exception` 静默吞掉,而是按 `SourceError.kind` 分类:

| kind | 含义 | 上游原因 |
|------|------|---------|
| `http_error` | HTTP 4xx/5xx | 401 / 403 / 404 / 500 |
| `parse_error` | JSON/XML 解析失败 | 服务端返回了非预期格式 |
| `timeout` | 请求超时 | 网络慢 / 服务端 hang |
| `rate_limit` | 速率限制持续 | 重试 2 次后仍 429 |
| `other` | 未预期异常 | 带 traceback 写入日志 |

`SearchManager.search_with_stats()` 返回 `dict[str, SourceStats]`,CLI 表格会按失败类型标色。

---

## 合规声明

本工具默认仅通过以下合法开放获取渠道下载全文:Unpaywall、PubMed Central、arXiv、OpenAlex、Semantic Scholar、CORE、ChemRxiv。下载内容仅供个人学术研究使用。Sci-Hub 功能需用户显式启用,并由用户自行承担相关法律责任。

---

## 鸣谢

PaperCrawler 在原 `paper-dl` 基础上,增加了领域感知与自动分类能力。如需回退到无领域过滤的传统模式,只需不传 `--interest` 即可,行为与 `paper-dl` 完全一致。
