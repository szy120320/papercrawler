#!/usr/bin/env python3
"""
跑 10 篇文献的完整流水线测试(端到端集成测试)。

注意:这是手动运行的脚本,**不是 pytest 单元测试**。
  - 运行方式: ``python tests/test_full_flow.py``
  - pytest 收集时会被自动跳过(见下方的 _pytest_skip 标记)

流水线(等价于 `papercrawler search -q "ReaxFF lithium battery" -n 10 --download`):
  1) SearchManager — 并发检索多数据源(用 config/papercrawler_test.toml,只 3 个无 key 源)
  2) DomainFilter(粗打分) — 按 [interest].must_have/should_have/exclude 在 title 上打分
  3) MetadataExtractor — 补充 abstract + 检查 OA 状态
  4) SemanticFilter(细打分) — 用 description 拆解的关键词 + 短语 在 title+abstract+keywords 上精筛
  5) Categorizer — 按 [interest].categories 打多标签
  6) CSVWriter — 导出 _interest_filtered.csv(含 coarse_score / semantic_score / final_score)
  7) PaperDownloader — 并发下载 PDF + MarkItDown 转 Markdown
  8) PaperStorage.write_index — 生成 _index.md / _index.json
"""
from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

# pytest 收集时跳过(本脚本是手动运行的端到端测试,不是单元测试)
try:
    import pytest  # noqa: F401
    if __name__ != "__main__":
        pytest.skip("端到端集成测试,仅手动运行", allow_module_level=True)
except ImportError:
    pass

# 让本脚本能 import 到包
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from papercrawler.classify import Categorizer, DomainFilter
from papercrawler.config import load_config, set_config
from papercrawler.download.downloader import PaperDownloader
from papercrawler.export.csv_writer import CSVWriter
from papercrawler.metadata.extractor import MetadataExtractor
from papercrawler.models import SearchQuery
from papercrawler.search.manager import SearchManager

# 改用项目已有的"简化"测试配置,只启用 openalex/crossref/arxiv 三个无 key 源
TEST_CONFIG_PATH = ROOT / "config" / "papercrawler_test.toml"
OUTPUT_DIR = ROOT / "test_output_10_papers"
N_RESULTS = 10
QUERY_TEXT = "ReaxFF lithium battery"


def banner(msg: str) -> None:
    line = "═" * 70
    print(f"\n{line}\n  {msg}\n{line}")


async def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # ── 1. 加载配置 ────────────────────────────────────────────────
    banner("STEP 0: 加载测试配置")
    cfg = load_config(str(TEST_CONFIG_PATH))
    set_config(cfg)
    print(f"  配置文件:    {TEST_CONFIG_PATH}")
    print(f"  启用数据源:  {cfg.sources.enabled}")
    print(f"  must_have:   {cfg.interest.must_have}")
    print(f"  分类数:      {len(cfg.interest.categories)}")
    print(f"  输出目录:    {OUTPUT_DIR}")

    # ── 2. 多源检索 ────────────────────────────────────────────────
    banner(f"STEP 1: 检索 → \"{QUERY_TEXT}\" (目标 {N_RESULTS} 篇)")
    t0 = time.perf_counter()
    manager = SearchManager(config=cfg)
    q = SearchQuery(query=QUERY_TEXT, max_results=N_RESULTS)
    papers, source_counts = await manager.search_with_stats(q, show_progress=False)
    t_search = time.perf_counter() - t0

    print(f"  检索耗时: {t_search:.1f}s")
    print("  各数据源命中数:")
    for src, stats in source_counts.items():
        # source_counts 是 dict[str, SourceStats];失败时 failure_kind 不为 None
        if stats.failure_kind:
            status = f"✗ 失败 ({stats.failure_kind}: {stats.failure_message or ''})"
        else:
            status = f"{stats.ok_count} 篇"
        print(f"    - {src:<18} {status}")
    print(f"  去重合并后: {len(papers)} 篇")
    for i, p in enumerate(papers, 1):
        first_author = p.authors[0].display_name() if p.authors else "—"
        print(f"    {i:2}. [{first_author:<14}] ({p.year or '—'}) {p.title[:55]}")

    if not papers:
        print("  ⚠️  没有检索到任何论文,流程中止")
        return

    # ── 3. 元数据增强(OA 状态) ────────────────────────────────────
    banner("STEP 2: 元数据增强(OA 状态)")
    t1 = time.perf_counter()
    extractor = MetadataExtractor(config=cfg)
    papers = await extractor.enrich_batch(papers)
    t_enrich = time.perf_counter() - t1

    print(f"  增强耗时: {t_enrich:.1f}s")
    oa_stats = {}
    for p in papers:
        oa_stats[p.access_status.value] = oa_stats.get(p.access_status.value, 0) + 1
    print("  OA 状态分布:")
    for k, v in sorted(oa_stats.items()):
        print(f"    - {k:<14} {v} 篇")

    # ── 4. 阶段1 粗筛(基于 title,只在 search 后立刻做)────────────
    banner("STEP 3a: 阶段1 粗筛 — DomainFilter(只看 title)")
    df = DomainFilter(cfg.interest)
    df.annotate(papers)
    papers.sort(key=lambda p: p.coarse_score or 0.0, reverse=True)

    print(f"  {'#':<3} {'粗筛分':<7} {'标题'}")
    for i, p in enumerate(papers, 1):
        s = p.coarse_score or 0.0
        marker = "★" if s >= 0.7 else ("·" if s >= 0.4 else " ")
        print(f"  {i:2}. {s:<6.3f} {marker} {p.title[:60]}")

    # ── 4b. 阶段2 细筛(基于 title+abstract+keywords,需 abstract) ──
    banner("STEP 3b: 阶段2 细筛 — 关键词命中计数")
    from papercrawler.classify import SemanticFilter

    sf = SemanticFilter(cfg.interest)
    sf.annotate(papers)

    # 按命中数降序
    papers.sort(key=lambda p: p.semantic_score or 0, reverse=True)

    print(f"  {'#':<3} {'粗筛':<6} {'命中数':<6} {'标题'}")
    for i, p in enumerate(papers, 1):
        coarse = p.coarse_score or 0.0
        hits = p.semantic_score or 0
        marker = "★" if coarse >= 0.7 and hits >= 3 else "·"
        print(f"  {i:2}. {coarse:<6.3f} {hits:<6} {marker} {p.title[:55]}")

    # ── 5. 双门限过滤(粗筛≥0.7 AND 命中数≥3,各自独立判断)────────
    banner("STEP 3c: 双门限硬过滤 — 粗筛≥0.7 AND 命中关键词≥3")
    COARSE_THRESHOLD = 0.7
    SEMANTIC_MIN_MATCHES = 3
    before_filter = len(papers)
    papers = [
        p for p in papers
        if (p.coarse_score or 0.0) >= COARSE_THRESHOLD
        and (p.semantic_score or 0) >= SEMANTIC_MIN_MATCHES
    ]
    after_filter = len(papers)
    print(f"  过滤前: {before_filter} 篇")
    print(f"  过滤后: {after_filter} 篇(剔除 {before_filter - after_filter} 篇)")
    print(f"  保留条件: coarse_score ≥ {COARSE_THRESHOLD} AND 命中关键词 ≥ {SEMANTIC_MIN_MATCHES}")

    # ── 6. 自动分类 ────────────────────────────────────────────────
    banner("STEP 4: 自动多标签分类(Categorizer)")
    cat = Categorizer(cfg.interest)
    cat.annotate(papers)

    n_categorized = sum(1 for p in papers if p.categories)
    print(f"  命中分类: {n_categorized}/{len(papers)} 篇")
    cat_stats: dict[str, int] = {}
    for p in papers:
        for c in p.categories:
            cat_stats[c] = cat_stats.get(c, 0) + 1
    print("  分类命中统计:")
    for c, n in sorted(cat_stats.items(), key=lambda x: -x[1]):
        print(f"    - {c:<20} {n} 篇")

    # ── 6. CSV 导出 ────────────────────────────────────────────────
    banner("STEP 5: CSV 导出")
    csv_path = OUTPUT_DIR / "_interest_filtered.csv"
    writer = CSVWriter()
    n_csv = writer.write(papers, csv_path)
    print(f"  写入 {n_csv} 行 → {csv_path}")

    # ── 7. 并发下载全文 + MarkItDown 转换 ─────────────────────────
    banner(f"STEP 6: 下载全文 + MarkItDown 转换({len(papers)} 篇)")
    t2 = time.perf_counter()
    downloader = PaperDownloader(output_dir=str(OUTPUT_DIR), config=cfg)
    # 强制重新走全流程(让 OA 失败时能实际触发 Sci-Hub fallback)
    tasks = await downloader.download_all(papers, force=True, force_global=True)
    t_dl = time.perf_counter() - t2

    succ = sum(1 for t in tasks if t.status.value == "success")
    skip = sum(1 for t in tasks if t.status.value == "skipped")
    fail = sum(1 for t in tasks if t.status.value == "failed")
    print(f"  下载耗时: {t_dl:.1f}s")
    print(f"  结果:    成功 {succ} | 跳过 {skip} | 失败 {fail}")
    print(f"  {'状态':<8} {'OA':<12} {'输出目录'}")
    for t in tasks:
        marker = {"success": "✓ 成功", "skipped": "→ 跳过", "failed": "✗ 失败"}.get(
            t.status.value, t.status.value
        )
        # paper_dir 是 slug 子目录名
        from papercrawler.utils.naming import make_paper_dirname
        slug = make_paper_dirname(
            year=t.paper.year,
            first_author_lastname=t.paper.first_author_lastname,
            title=t.paper.title,
            doi=t.paper.doi,
        )
        rel = f"{slug}/paper.pdf" if t.status.value == "success" else (
            f"{slug}/paper.md" if t.status.value == "skipped" else "—"
        )
        err = f" — {t.error_msg}" if t.error_msg else ""
        print(f"  {marker:<8} {t.paper.access_status.value:<12} {rel}{err}")

    # ── 7.5 回填 CSV 的 downloaded 列(更准确的语义:磁盘上有 paper.pdf/md 即算已下载)──
    banner("STEP 6.5: 回填 CSV 的 downloaded 列(磁盘文件感知)")
    # 用 PaperStorage 拿目录路径,检查 paper.pdf 或 paper.md 是否存在
    from papercrawler.download.storage import PaperStorage
    storage = PaperStorage(str(OUTPUT_DIR))
    downloaded_lookup = {}
    for p in papers:
        paper_dir = storage.get_paper_dir(p)
        has_pdf = (paper_dir / "paper.pdf").exists()
        downloaded_lookup[p.unique_id] = has_pdf
    n_csv2 = writer.write(papers, csv_path, downloaded_lookup=downloaded_lookup)
    n_true = sum(1 for v in downloaded_lookup.values() if v)
    print(f"  重写 {n_csv2} 行, downloaded=true (有 paper.pdf): {n_true}/{len(downloaded_lookup)} 篇")

    # ── 8. 索引(已在 PaperDownloader.download_all 末尾自动生成)───
    banner("STEP 7: 索引文件")
    index_md = OUTPUT_DIR / "_index.md"
    index_json = OUTPUT_DIR / "_index.json"
    print(f"  _index.md:    {index_md.exists()} ({index_md.stat().st_size if index_md.exists() else 0} B)")
    print(f"  _index.json:  {index_json.exists()} ({index_json.stat().st_size if index_json.exists() else 0} B)")
    if index_json.exists():
        try:
            idx = json.loads(index_json.read_text(encoding="utf-8"))
            print(f"  索引条数:    {len(idx) if isinstance(idx, list) else 'dict'}")
        except Exception as e:
            print(f"  (解析失败: {e})")

    # ── 9. 总结 ────────────────────────────────────────────────────
    banner("流程总结")
    print(f"  总耗时:    检索 {t_search:.1f}s + 增强 {t_enrich:.1f}s + 下载 {t_dl:.1f}s")
    print(f"  检索结果:  {len(papers)} 篇(原始={sum(max(s.ok_count, 0) for s in source_counts.values())},去重后)")
    print(f"  领域打分:  {n_categorized}/{len(papers)} 篇命中分类")
    print(f"  CSV 导出:  {csv_path}")
    print(f"  下载:      成功 {succ} | 跳过 {skip} | 失败 {fail}")
    print(f"  输出目录:  {OUTPUT_DIR}")

    # 显示 CSV 内容样本(前 3 行)
    if csv_path.exists():
        print(f"\n  📄 CSV 样本(前 3 行):")
        with open(csv_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        for line in lines[:4]:  # header + 3 rows
            print(f"    {line.rstrip()[:140]}")
        if len(lines) > 4:
            print(f"    ... (共 {len(lines)-1} 条记录)")


if __name__ == "__main__":
    asyncio.run(main())