#!/usr/bin/env python3
"""
跑 20 篇文献的完整流水线测试(端到端集成测试)。

流水线(等价于 `papercrawler search -q ... -n 20 --download`):
  1) SearchManager — 并发检索多数据源(openalex + crossref + arxiv)
  2) DomainFilter(粗筛,基于 title) — 粗筛分数 ≥ 0.7 才进 enrich
  3) MetadataExtractor — enrich(补 abstract + OA 状态)
  4) SemanticFilter(细筛,纯关键词命中计数) — 命中 ≥ 3 才保留
  5) CSVWriter — 导出 _interest_filtered.csv(含 coarse_score / semantic_score)
  6) PaperDownloader — 三阶段下载(OA → Sci-Hub fallback → metadata_only)
  7) PaperStorage.write_index — 生成 _index.md / _index.json
  8) 失败计数汇总 — 按 kind 分类(http_error / parse_error / timeout / other)

2026-07-05:删除 Categorizer 步骤(分类功能下线)。
"""
from __future__ import annotations

import asyncio
import io
import json
import sys
import time
from collections import Counter
from datetime import datetime
from pathlib import Path

# Windows GBK 终端兼容:强制 stdout/stderr 使用 UTF-8,避免 ✓/✗/═ 等字符抛 UnicodeEncodeError
if sys.platform == "win32" and hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

# 让本脚本能 import papercrawler 包
# 注:必须用 .parent.parent(项目根),不是 .parent(tests 目录)
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from papercrawler.classify import DomainFilter, SemanticFilter
from papercrawler.config import load_config, set_config
from papercrawler.download.downloader import PaperDownloader
from papercrawler.download.storage import PaperStorage
from papercrawler.export.csv_writer import CSVWriter
from papercrawler.metadata.extractor import MetadataExtractor
from papercrawler.models import SearchQuery
from papercrawler.search.manager import SearchManager, SourceStats

TEST_CONFIG_PATH = ROOT / "config" / "papercrawler_test.toml"
N_RESULTS = 20
COARSE_THRESHOLD = 0.7
SEMANTIC_MIN_MATCHES = 3
QUERY_TEXT = "ReaxFF lithium battery"


def banner(msg: str) -> None:
    line = "═" * 70
    print(f"\n{line}\n  {msg}\n{line}")


def fmt_dur(sec: float) -> str:
    if sec < 60:
        return f"{sec:.1f}s"
    m, s = divmod(sec, 60)
    return f"{int(m)}m{s:.0f}s"


async def main() -> None:
    timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    OUTPUT_DIR = ROOT / f"test_output_20_papers_{timestamp}"
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
    print(f"  目标:        {N_RESULTS} 篇 / 粗筛 ≥ {COARSE_THRESHOLD} / 命中关键词 ≥ {SEMANTIC_MIN_MATCHES}")

    # ── 2. 多源检索 ────────────────────────────────────────────────
    banner(f"STEP 1: 检索 → \"{QUERY_TEXT}\" (目标 {N_RESULTS} 篇)")
    t0 = time.perf_counter()
    manager = SearchManager(config=cfg)
    q = SearchQuery(query=QUERY_TEXT, max_results=N_RESULTS)
    papers, source_stats = await manager.search_with_stats(q, show_progress=False)
    t_search = time.perf_counter() - t0

    print(f"  检索耗时: {fmt_dur(t_search)}")
    print(f"  各数据源结果:")
    for sid, stats in source_stats.items():
        if stats.failure_kind:
            label = {
                "http_error":  "HTTP 错误",
                "parse_error": "解析失败",
                "timeout":     "超时",
                "rate_limit":  "限速持续",
                "other":       "异常",
            }.get(stats.failure_kind, stats.failure_kind)
            print(f"    ✗ {sid:<18} {label} ({stats.failure_message or ''})")
        else:
            n = stats.ok_count
            mark = "✓" if n > 0 else "·"
            print(f"    {mark} {sid:<18} {n} 篇")
    print(f"  去重合并后: {len(papers)} 篇(原始={sum(max(s.ok_count, 0) for s in source_stats.values())})")

    if not papers:
        print("\n  ⚠️  没有任何论文,流程中止")
        return

    # ── 3. 元数据增强(OA 状态) ────────────────────────────────────
    banner("STEP 2: 元数据增强(enrich abstract + OA 状态)")
    t1 = time.perf_counter()
    extractor = MetadataExtractor(config=cfg)
    papers = await extractor.enrich_batch(papers)
    t_enrich = time.perf_counter() - t1

    print(f"  增强耗时: {fmt_dur(t_enrich)}")
    oa_stats: dict[str, int] = {}
    for p in papers:
        oa_stats[p.access_status.value] = oa_stats.get(p.access_status.value, 0) + 1
    print(f"  OA 状态分布:")
    for k, v in sorted(oa_stats.items()):
        bar = "█" * min(v, 30)
        print(f"    {k:<18} {v:>3}  {bar}")

    # ── 4. 阶段1 粗筛 ─────────────────────────────────────────────
    banner(f"STEP 3a: 阶段1 粗筛(DomainFilter,只看 title,阈值={COARSE_THRESHOLD})")
    df = DomainFilter(cfg.interest)
    df.annotate(papers)
    papers.sort(key=lambda p: p.coarse_score or 0.0, reverse=True)

    print(f"  {'#':<3} {'粗筛分':<7} 标题")
    for i, p in enumerate(papers, 1):
        s = p.coarse_score or 0.0
        marker = "★" if s >= COARSE_THRESHOLD else ("·" if s >= 0.4 else " ")
        print(f"  {i:2}. {s:<6.3f} {marker} {p.title[:55]}")

    # ── 4b. 阶段2 细筛 ─────────────────────────────────────────────
    banner(f"STEP 3b: 阶段2 细筛(SemanticFilter,关键词命中数,阈值={SEMANTIC_MIN_MATCHES})")
    sf = SemanticFilter(cfg.interest)
    sf.annotate(papers)
    papers.sort(key=lambda p: p.semantic_score or 0, reverse=True)

    print(f"  {'#':<3} {'粗筛':<6} {'命中':<5} 标题")
    for i, p in enumerate(papers, 1):
        coarse = p.coarse_score or 0.0
        hits = p.semantic_score or 0
        marker = "★" if coarse >= COARSE_THRESHOLD and hits >= SEMANTIC_MIN_MATCHES else "·"
        print(f"  {i:2}. {coarse:<6.3f} {hits:<5} {marker} {p.title[:50]}")

    # ── 5. 双门限硬过滤 ───────────────────────────────────────────
    banner(f"STEP 3c: 双门限硬过滤 — 粗筛≥{COARSE_THRESHOLD} AND 命中关键词≥{SEMANTIC_MIN_MATCHES}")
    before_filter = len(papers)
    papers = [
        p for p in papers
        if (p.coarse_score or 0.0) >= COARSE_THRESHOLD
        and (p.semantic_score or 0) >= SEMANTIC_MIN_MATCHES
    ]
    after_filter = len(papers)
    print(f"  过滤前: {before_filter} 篇")
    print(f"  过滤后: {after_filter} 篇(剔除 {before_filter - after_filter} 篇)")

    if not papers:
        print("\n  ⚠️  双门限后无保留论文,流程中止")
        return

    # ── 6. CSV 导出 ────────────────────────────────────────────────
    banner("STEP 4: CSV 导出")
    csv_path = OUTPUT_DIR / "_interest_filtered.csv"
    writer = CSVWriter()
    n_csv = writer.write(papers, csv_path)
    print(f"  写入 {n_csv} 行 → {csv_path}")

    # ── 8. 下载全文 + MarkItDown 转换 ─────────────────────────
    banner(f"STEP 6: 下载全文 + MarkItDown 转换({len(papers)} 篇)")
    t2 = time.perf_counter()
    downloader = PaperDownloader(output_dir=str(OUTPUT_DIR), config=cfg)
    # force_global=True 跳过跨 run DB 检查;force=True 跳过本 run DB
    tasks = await downloader.download_all(
        papers, force=True, dry_run=False, force_global=True
    )
    t_dl = time.perf_counter() - t2

    succ = sum(1 for t in tasks if t.status.value == "success")
    skip = sum(1 for t in tasks if t.status.value == "skipped")
    fail = sum(1 for t in tasks if t.status.value == "failed")
    print(f"  下载耗时: {fmt_dur(t_dl)}")
    print(f"  结果:    ✓ 成功 {succ}  → 跳过 {skip}  ✗ 失败 {fail}")

    # 显式写索引(_do_download 会自动写,但我们直接调了 download_all,要补这一步)
    await downloader.storage.write_index(papers)
    print(f"  {'状态':<6} {'OA':<14} {'#':<3} {'标题'}")
    for i, t in enumerate(tasks, 1):
        marker = {"success": "✓", "skipped": "→", "failed": "✗"}.get(t.status.value, "?")
        err = f" — {t.error_msg[:40]}" if t.error_msg else ""
        print(f"  {marker} {t.paper.access_status.value:<14} {i:>2}. {t.paper.title[:50]}{err}")

    # ── 8.5 回填 CSV downloaded 列(磁盘文件感知) ─────────────────
    banner("STEP 6.5: 回填 CSV 的 downloaded 列(磁盘文件感知)")
    storage = PaperStorage(str(OUTPUT_DIR))
    downloaded_lookup = {}
    for p in papers:
        paper_dir = storage.get_paper_dir(p)
        has_pdf = (paper_dir / "paper.pdf").exists()
        downloaded_lookup[p.unique_id] = has_pdf
    n_csv2 = writer.write(papers, csv_path, downloaded_lookup=downloaded_lookup)
    n_true = sum(1 for v in downloaded_lookup.values() if v)
    print(f"  重写 {n_csv2} 行, downloaded=true (有 paper.pdf): {n_true}/{len(downloaded_lookup)} 篇")

    # ── 9. 索引(已在 PaperDownloader.download_all 末尾自动生成)──
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

    # ── 10. 失败原因汇总 ────────────────────────────────────────
    banner("STEP 8: 失败原因分类汇总")
    failure_kinds: Counter[str] = Counter()
    failure_msgs: list[str] = []
    for t in tasks:
        if t.status.value == "failed" and t.error_msg:
            # 错误信息形如 "OA 失败: metadata_only(...)" 或 "OA 失败: ...; Sci-Hub 兜底也失败"
            failure_kinds["OA 未命中"] += 1 if "metadata_only" in t.error_msg else 0
            failure_kinds["网络错误"] += 1 if any(k in t.error_msg for k in ("Timeout", "network", "Connect", "ReadError")) else 0
            failure_kinds["Sci-Hub 未收录"] += 1 if "Sci-Hub" in t.error_msg else 0
            failure_kinds["其他"] += 1
            failure_msgs.append(f"  [{t.paper.access_status.value}] {t.paper.title[:50]} — {t.error_msg[:80]}")
    if failure_kinds:
        print("  失败分类:")
        for k, v in sorted(failure_kinds.items(), key=lambda x: -x[1]):
            if v > 0:
                print(f"    {k:<20} {v}")
        print("\n  详情:")
        for m in failure_msgs[:10]:
            print(m)
        if len(failure_msgs) > 10:
            print(f"  ... 共 {len(failure_msgs)} 条")

    # ── 11. 总结 ───────────────────────────────────────────────
    banner("流程总结")
    print(f"  总耗时:    检索 {fmt_dur(t_search)} + 增强 {fmt_dur(t_enrich)} + 下载 {fmt_dur(t_dl)}")
    print(f"  检索结果:  {len(papers)} 篇(双门限后,原始={sum(max(s.ok_count, 0) for s in source_stats.values())},去重前)")
    print(f"  领域打分:  命中数基于粗筛/细筛双门限(分类功能已下线)")
    print(f"  CSV:       {csv_path} ({n_csv} 行)")
    print(f"  下载:      ✓ {succ}  → {skip}  ✗ {fail}")
    print(f"  成功率:    {succ / (succ + fail) * 100:.0f}% (失败 {fail} 篇)")
    print(f"  输出目录:  {OUTPUT_DIR}")
    print()

    # 显示 CSV 内容样本
    if csv_path.exists():
        print(f"  📄 CSV 样本(前 3 行):")
        with open(csv_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        for line in lines[:4]:
            print(f"    {line.rstrip()[:160]}")
        if len(lines) > 4:
            print(f"    ... (共 {len(lines)-1} 条记录)")


if __name__ == "__main__":
    asyncio.run(main())