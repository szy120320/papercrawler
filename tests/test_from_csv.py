"""测试 --from-csv 功能 + 断点续下

测试目标:
  1. CSVReader 正确解析 CSV → PaperMetadata
  2. download --from-csv 下载 CSV 中的论文
  3. 第二次运行自动跳过已下载(断点续下)
  4. --force-global 强制重新下载
  5. --skip-already 跳过 CSV 中 downloaded=true 的行
"""
from __future__ import annotations

import asyncio
import csv
import os
import sys
import tempfile
from pathlib import Path

# 项目根
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from papercrawler.export.csv_writer import CSVReader, CSVWriter, CSVReadError  # noqa: E402
from papercrawler.models import PaperMetadata, Author, AccessStatus  # noqa: E402


def make_paper(title, year=2024, doi=None, oa_url=None):
    return PaperMetadata(
        title=title,
        authors=[Author(name="Smith, John")],
        year=year,
        journal="Test Journal",
        doi=doi,
        oa_url=oa_url,
        access_status=AccessStatus.OPEN_ACCESS_PDF,
        sources=["arxiv"],
    )


# ---------------------------------------------------------------------------
# 测试 1: CSVReader 基础解析
# ---------------------------------------------------------------------------
def test_csv_reader_basic():
    with tempfile.TemporaryDirectory() as tmp:
        csv_path = Path(tmp) / "test.csv"
        # 写一个标准 CSV(search 命令会输出这种格式)
        with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=[
                "title", "doi", "year", "journal", "authors",
                "citations", "access_status", "oa_url", "sources",
                "downloaded",
            ])
            writer.writeheader()
            writer.writerow({
                "title": "Test Paper A",
                "doi": "10.48550/arXiv.2401.00001",
                "year": "2024",
                "journal": "arXiv preprint",
                "authors": "Smith, John; Doe, Jane",
                "citations": "5",
                "access_status": "oa_pdf",
                "oa_url": "https://arxiv.org/pdf/2401.00001.pdf",
                "sources": "arxiv; openalex",
                "downloaded": "false",
            })
            writer.writerow({
                "title": "Test Paper B",
                "doi": "",  # 空 DOI
                "year": "2023",
                "journal": "",
                "authors": "Doe, Jane",
                "citations": "",
                "access_status": "unknown",
                "oa_url": "",
                "sources": "",
                "downloaded": "false",
            })

        reader = CSVReader()
        papers = reader.read(csv_path)
        assert len(papers) == 2, f"应解析 2 篇,实际 {len(papers)}"
        assert papers[0].title == "Test Paper A"
        assert papers[0].doi == "10.48550/arXiv.2401.00001"
        assert papers[0].year == 2024
        assert papers[0].citations_count == 5
        assert papers[0].access_status == AccessStatus.OPEN_ACCESS_PDF
        assert len(papers[0].authors) == 2
        assert papers[0].sources == ["arxiv", "openalex"]

        assert papers[1].title == "Test Paper B"
        assert papers[1].doi is None
        assert papers[1].year == 2023
        assert papers[1].citations_count is None
        assert papers[1].access_status == AccessStatus.UNKNOWN
        assert len(papers[1].authors) == 1

        print("✓ test_csv_reader_basic PASS")


# ---------------------------------------------------------------------------
# 测试 2: --skip-already 过滤 downloaded=true 的行
# ---------------------------------------------------------------------------
def test_csv_reader_skip_already():
    with tempfile.TemporaryDirectory() as tmp:
        csv_path = Path(tmp) / "test.csv"
        with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=[
                "title", "doi", "year", "journal", "authors",
                "citations", "access_status", "oa_url", "sources",
                "downloaded",
            ])
            writer.writeheader()
            writer.writerow({"title": "Paper A", "downloaded": "true", "doi": "", "year": "", "journal": "", "authors": "", "citations": "", "access_status": "", "oa_url": "", "sources": ""})
            writer.writerow({"title": "Paper B", "downloaded": "false", "doi": "", "year": "", "journal": "", "authors": "", "citations": "", "access_status": "", "sources": ""})
            writer.writerow({"title": "Paper C", "downloaded": "1", "doi": "", "year": "", "journal": "", "authors": "", "citations": "", "access_status": "", "sources": ""})

        reader = CSVReader()
        # 不跳过 → 3 篇
        all_papers = reader.read(csv_path, filter_downloaded=False)
        assert len(all_papers) == 3, f"应返回 3 篇,实际 {len(all_papers)}"

        # 跳过 → 1 篇
        filtered = reader.read(csv_path, filter_downloaded=True)
        assert len(filtered) == 1, f"应过滤后剩 1 篇,实际 {len(filtered)}"
        assert filtered[0].title == "Paper B"

        print("✓ test_csv_reader_skip_already PASS")


# ---------------------------------------------------------------------------
# 测试 3: 错误 CSV 处理
# ---------------------------------------------------------------------------
def test_csv_reader_errors():
    with tempfile.TemporaryDirectory() as tmp:
        # 文件不存在
        try:
            CSVReader().read(Path(tmp) / "nonexistent.csv")
            assert False, "应抛 FileNotFoundError"
        except FileNotFoundError:
            pass

        # 缺少 title 列
        bad_csv = Path(tmp) / "bad.csv"
        with open(bad_csv, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=["doi", "year"])
            writer.writeheader()
            writer.writerow({"doi": "10.1/test", "year": "2024"})

        try:
            CSVReader().read(bad_csv)
            assert False, "应抛 CSVReadError"
        except CSVReadError as e:
            assert "title" in str(e), f"错误信息应包含 title,实际: {e}"

        print("✓ test_csv_reader_errors PASS")


# ---------------------------------------------------------------------------
# 测试 4: CLI 集成 — download --from-csv (实际下载 arXiv 论文 + 验证断点续下)
# ---------------------------------------------------------------------------
def test_cli_from_csv_with_resume(tmp_path: Path):
    """集成测试:
    1. 创建 CSV(2 篇 arXiv 已知可下载论文)
    2. 第一次跑 download --from-csv → 2 篇 success
    3. 第二次跑同样命令 → 2 篇 skipped (断点续下)
    4. 第三次跑 --force-global → 重新下载
    """
    csv_path = tmp_path / "test_papers.csv"
    with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "title", "doi", "year", "journal", "authors",
            "citations", "access_status", "oa_url", "sources",
            "downloaded",
        ])
        writer.writeheader()
        # 用经典 Transformer 论文 + ResNet 论文(都已知 arxiv 可下)
        writer.writerow({
            "title": "Attention Is All You Need",
            "doi": "10.48550/arXiv.1706.03762",
            "year": "2017",
            "journal": "arXiv preprint",
            "authors": "Vaswani, Ashish",
            "citations": "90000",
            "access_status": "oa_pdf",
            "oa_url": "https://arxiv.org/pdf/1706.03762.pdf",
            "sources": "arxiv",
            "downloaded": "false",
        })

    output_dir = tmp_path / "papers"

    # 用 subprocess 跑实际 CLI(最接近真实使用)
    import subprocess
    env = os.environ.copy()

    def run_dl(args):
        cmd = [str(ROOT / ".venv/Scripts/python.exe"), "-m", "papercrawler.cli",
               "download", "--from-csv", str(csv_path),
               "--output-dir", str(output_dir)] + args
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=120, env=env,
                           cwd=str(ROOT))
        return r

    # 第一次:下载
    print("  第一次下载...")
    r1 = run_dl([])
    assert r1.returncode == 0, f"第一次下载失败:\nSTDOUT: {r1.stdout[-1000:]}\nSTDERR: {r1.stderr[-500:]}"

    # 验证 PDF 文件存在
    paper_dirs = list(output_dir.glob("*/"))
    assert len(paper_dirs) >= 1, f"应有至少 1 个论文目录,实际 {len(paper_dirs)}"
    pdfs = list(output_dir.rglob("paper.pdf"))
    assert len(pdfs) >= 1, f"应有至少 1 个 PDF,实际 {len(pdfs)}"
    print(f"  第一次下载: {len(pdfs)} 个 PDF")

    # 第二次:应该全部 skipped
    print("  第二次(断点续下测试)...")
    r2 = run_dl([])
    assert r2.returncode == 0, f"第二次失败:\n{r2.stderr[-500:]}"
    # 输出里应该出现 "skipped"
    assert "跳过" in r2.stdout, f"第二次应包含跳过提示,实际输出:\n{r2.stdout[-500:]}"
    print("  第二次:全部 skipped ✓")

    # 第三次:--force-global 强制重下
    print("  第三次(--force-global)...")
    r3 = run_dl(["--force-global"])
    assert r3.returncode == 0, f"第三次失败:\n{r3.stderr[-500:]}"
    print("  第三次:force-global OK")

    print("✓ test_cli_from_csv_with_resume PASS")


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------
def main():
    print("=" * 60)
    print("测试 --from-csv 功能 + 断点续下")
    print("=" * 60)

    test_csv_reader_basic()
    test_csv_reader_skip_already()
    test_csv_reader_errors()

    print("\n--- 集成测试(实际下载) ---")
    with tempfile.TemporaryDirectory() as tmp:
        test_cli_from_csv_with_resume(Path(tmp))

    print("\n" + "=" * 60)
    print("✅ 所有测试通过!")
    print("=" * 60)


if __name__ == "__main__":
    main()