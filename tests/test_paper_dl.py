"""
测试套件 — 单元测试 + 集成测试
"""

import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from papercrawler.models import (
    AccessStatus, Author, DownloadStatus, DownloadTask, PaperMetadata, SearchQuery
)
from papercrawler.utils.naming import make_paper_dirname, title_to_slug
from papercrawler.utils.dedup import deduplicate, merge_papers


# ===========================================================================
# 测试数据
# ===========================================================================

def make_paper(
    title="Test Paper",
    doi=None,
    year=2024,
    authors=None,
    access_status=AccessStatus.UNKNOWN,
    sources=None,
    abstract=None,
    oa_url=None,
) -> PaperMetadata:
    if authors is None:
        authors = [Author(name="Smith, John")]
    return PaperMetadata(
        title=title,
        authors=authors,
        year=year,
        doi=doi,
        access_status=access_status,
        sources=sources or [],
        abstract=abstract,
        oa_url=oa_url,
    )


# ===========================================================================
# models.py 测试
# ===========================================================================

class TestPaperMetadata:
    def test_doi_normalization_https_prefix(self):
        p = make_paper(doi="https://doi.org/10.1038/test")
        assert p.doi == "10.1038/test"

    def test_doi_normalization_doi_prefix(self):
        p = make_paper(doi="doi:10.1038/test")
        assert p.doi == "10.1038/test"

    def test_doi_normalization_plain(self):
        p = make_paper(doi="10.1038/test")
        assert p.doi == "10.1038/test"

    def test_unique_id_with_doi(self):
        p = make_paper(doi="10.1038/test")
        assert p.unique_id == "10.1038/test"

    def test_unique_id_without_doi(self):
        p = make_paper(doi=None)
        uid = p.unique_id
        assert len(uid) == 16
        # 同内容应产生相同 ID
        p2 = make_paper(doi=None)
        assert p.unique_id == p2.unique_id

    def test_first_author_lastname(self):
        p = make_paper(authors=[Author(name="Warshel, Arieh"), Author(name="Levitt, Michael")])
        assert p.first_author_lastname == "Warshel"

    def test_doi_url(self):
        p = make_paper(doi="10.1038/test")
        assert p.doi_url == "https://doi.org/10.1038/test"

    def test_doi_url_none(self):
        p = make_paper(doi=None, oa_url=None)
        assert p.doi_url is None

    def test_access_status_display(self):
        p = make_paper(access_status=AccessStatus.OPEN_ACCESS_PDF)
        assert "PDF" in p.access_status_display()

    def test_search_query_has_any_input(self):
        q = SearchQuery(query="test")
        assert q.has_any_input()
        q2 = SearchQuery()
        assert not q2.has_any_input()

    def test_search_query_build_text(self):
        q = SearchQuery(query="battery", author="Smith")
        text = q.build_text_query()
        assert "battery" in text
        assert "Smith" in text


# ===========================================================================
# utils/naming.py 测试
# ===========================================================================

class TestNaming:
    def test_title_slug_basic(self):
        slug = title_to_slug("Computer Simulation of Protein Folding")
        assert "computer" in slug
        assert "simulation" in slug
        # 停用词 'of' 应被过滤
        assert "of" not in slug.split("_")

    def test_title_slug_max_words(self):
        slug = title_to_slug("A B C D E F G H I J", max_words=3)
        assert len(slug.split("_")) <= 3

    def test_make_paper_dirname_with_doi(self):
        name = make_paper_dirname(
            year=1975,
            first_author_lastname="Levitt",
            title="Computer simulation of protein folding",
            doi="10.1038/253694a0",
        )
        assert name.startswith("1975_levitt_")
        assert "253694a0" in name

    def test_make_paper_dirname_without_doi(self):
        name = make_paper_dirname(
            year=2024,
            first_author_lastname="Smith",
            title="Test Title",
            doi=None,
            hash_id="abcdef12",
        )
        assert name.startswith("2024_smith_")
        assert "abcdef12" in name

    def test_make_paper_dirname_no_year(self):
        name = make_paper_dirname(
            year=None,
            first_author_lastname="Unknown",
            title="Test",
        )
        assert name.startswith("0000_")

    def test_dirname_length_limit(self):
        name = make_paper_dirname(
            year=2024,
            first_author_lastname="A" * 50,
            title="word " * 30,
            doi="10.1234/" + "x" * 50,
        )
        assert len(name) <= 120


# ===========================================================================
# utils/dedup.py 测试
# ===========================================================================

class TestDedup:
    def test_dedup_same_doi(self):
        p1 = make_paper(doi="10.1038/test", sources=["semantic_scholar"])
        p2 = make_paper(doi="10.1038/test", sources=["openalex"])
        result = deduplicate([p1, p2])
        assert len(result) == 1
        assert "semantic_scholar" in result[0].sources
        assert "openalex" in result[0].sources

    def test_dedup_different_doi(self):
        p1 = make_paper(doi="10.1038/test1")
        p2 = make_paper(doi="10.1038/test2")
        result = deduplicate([p1, p2])
        assert len(result) == 2

    def test_dedup_no_doi_same_content(self):
        p1 = make_paper(doi=None, title="Same Title", year=2024)
        p2 = make_paper(doi=None, title="Same Title", year=2024)
        result = deduplicate([p1, p2])
        assert len(result) == 1

    def test_dedup_no_doi_different_content(self):
        p1 = make_paper(doi=None, title="Title A", year=2024)
        p2 = make_paper(doi=None, title="Title B", year=2024)
        result = deduplicate([p1, p2])
        assert len(result) == 2

    def test_merge_abstract_priority(self):
        p_s2 = make_paper(
            doi="10.1038/test",
            abstract="S2 abstract",
            sources=["semantic_scholar"],
        )
        p_cr = make_paper(
            doi="10.1038/test",
            abstract="CR abstract",
            sources=["crossref"],
        )
        # semantic_scholar 摘要优先级高于 crossref
        merged = merge_papers([p_cr, p_s2])
        assert merged.abstract == "S2 abstract"

    def test_merge_keeps_all_sources(self):
        p1 = make_paper(doi="10.1038/x", sources=["semantic_scholar", "openalex"])
        p2 = make_paper(doi="10.1038/x", sources=["crossref"])
        merged = merge_papers([p1, p2])
        assert set(merged.sources) == {"semantic_scholar", "openalex", "crossref"}


# ===========================================================================
# search/semantic_scholar.py 测试（Mock HTTP）
# ===========================================================================

class TestSemanticScholarAdapter:
    @pytest.mark.asyncio
    async def test_parse_valid_response(self):
        from papercrawler.search.semantic_scholar import SemanticScholarAdapter

        fake_response = {
            "data": [
                {
                    "paperId": "abc123",
                    "title": "Attention Is All You Need",
                    "authors": [{"name": "Vaswani, Ashish"}],
                    "year": 2017,
                    "abstract": "We propose a new architecture...",
                    "openAccessPdf": {"url": "https://arxiv.org/pdf/1706.03762.pdf"},
                    "externalIds": {"ArXiv": "1706.03762", "DOI": None},
                    "citationCount": 50000,
                    "referenceCount": 40,
                    "journal": {"name": "NeurIPS"},
                    "url": "https://api.semanticscholar.org/paper/abc123",
                }
            ]
        }

        adapter = SemanticScholarAdapter()
        with patch.object(adapter, "_get_json", new=AsyncMock(return_value=fake_response)):
            q = SearchQuery(query="attention transformer")
            results = await adapter.search(q)

        assert len(results) == 1
        assert results[0].title == "Attention Is All You Need"
        assert results[0].access_status == AccessStatus.OPEN_ACCESS_PREPRINT
        assert results[0].raw_ids["arxiv"] == "1706.03762"

    @pytest.mark.asyncio
    async def test_empty_response(self):
        from papercrawler.search.semantic_scholar import SemanticScholarAdapter
        adapter = SemanticScholarAdapter()
        with patch.object(adapter, "_get_json", new=AsyncMock(return_value={"data": []})):
            results = await adapter.search(SearchQuery(query="xyz"))
        assert results == []

    @pytest.mark.asyncio
    async def test_network_failure_returns_empty(self):
        from papercrawler.search.semantic_scholar import SemanticScholarAdapter
        adapter = SemanticScholarAdapter()
        with patch.object(adapter, "_get_json", new=AsyncMock(return_value=None)):
            results = await adapter.search(SearchQuery(query="xyz"))
        assert results == []


# ===========================================================================
# access/checker.py 测试
# ===========================================================================

class TestAccessChecker:
    @pytest.mark.asyncio
    async def test_arxiv_detection_from_raw_ids(self):
        from papercrawler.access.checker import AccessChecker
        checker = AccessChecker()

        paper = make_paper(doi="10.48550/arXiv.1706.03762")
        paper.raw_ids["arxiv"] = "1706.03762"

        result = await checker.check(paper)
        assert result.access_status == AccessStatus.OPEN_ACCESS_PREPRINT
        assert "arxiv.org/pdf" in result.oa_url

    @pytest.mark.asyncio
    async def test_arxiv_detection_from_doi(self):
        from papercrawler.access.checker import AccessChecker
        checker = AccessChecker()

        paper = make_paper(doi="10.48550/arXiv.2301.00001")
        result = await checker.check(paper)
        assert result.access_status == AccessStatus.OPEN_ACCESS_PREPRINT

    @pytest.mark.asyncio
    async def test_already_has_oa_url_pdf(self):
        from papercrawler.access.checker import AccessChecker
        checker = AccessChecker()

        paper = make_paper(
            access_status=AccessStatus.OPEN_ACCESS_PDF,
            oa_url="https://example.com/paper.pdf",
        )
        result = await checker.check(paper)
        assert result.access_status == AccessStatus.OPEN_ACCESS_PDF
        assert result.oa_url == "https://example.com/paper.pdf"

    @pytest.mark.asyncio
    async def test_metadata_only_when_no_oa(self):
        from papercrawler.access.checker import AccessChecker
        checker = AccessChecker()

        paper = make_paper(doi="10.1038/253694a0")
        # Mock Unpaywall 返回 None
        with patch.object(checker._unpaywall, "get_oa_url", new=AsyncMock(return_value=None)):
            with patch.object(checker, "_probe_publisher", new=AsyncMock(return_value=None)):
                result = await checker.check(paper)
        assert result.access_status == AccessStatus.METADATA_ONLY


# ===========================================================================
# convert/markitdown_converter.py 测试
# ===========================================================================

class TestMarkItDownConverter:
    def test_disabled_converter_returns_none(self):
        from papercrawler.convert.markitdown_converter import MarkItDownConverter
        converter = MarkItDownConverter(enabled=False)
        assert converter.convert_file("/any/path.pdf") is None
        assert not converter.is_available()

    def test_nonexistent_file_returns_none(self):
        from papercrawler.convert.markitdown_converter import MarkItDownConverter
        converter = MarkItDownConverter(enabled=False)
        result = converter.convert_file("/nonexistent/file.pdf")
        assert result is None

    def test_unsupported_format_returns_none(self):
        from papercrawler.convert.markitdown_converter import MarkItDownConverter
        import tempfile, os
        converter = MarkItDownConverter(enabled=True)
        # 创建临时文件
        with tempfile.NamedTemporaryFile(suffix=".xyz", delete=False) as f:
            f.write(b"content")
            tmp = f.name
        try:
            result = converter.convert_file(tmp)
            assert result is None
        finally:
            os.unlink(tmp)


# ===========================================================================
# download/storage.py 测试
# ===========================================================================

class TestPaperStorage:
    @pytest.mark.asyncio
    async def test_save_metadata_creates_file(self, tmp_path):
        from papercrawler.download.storage import PaperStorage
        storage = PaperStorage(str(tmp_path))
        paper = make_paper(doi="10.1038/test", title="Test Paper", year=2024)
        paper_dir = storage.ensure_paper_dir(paper)
        meta_path = await storage.save_metadata(paper, paper_dir)
        assert meta_path.exists()
        data = json.loads(meta_path.read_text(encoding="utf-8"))
        assert data["title"] == "Test Paper"

    @pytest.mark.asyncio
    async def test_write_index(self, tmp_path):
        from papercrawler.download.storage import PaperStorage
        storage = PaperStorage(str(tmp_path))
        papers = [
            make_paper(doi="10.1038/p1", title="Paper One", year=2023),
            make_paper(doi="10.1038/p2", title="Paper Two", year=2024),
        ]
        await storage.write_index(papers)
        assert (tmp_path / "_index.md").exists()
        assert (tmp_path / "_index.json").exists()


# ===========================================================================
# download/database.py 测试
# ===========================================================================

class TestDownloadDatabase:
    def test_upsert_and_is_downloaded(self, tmp_path):
        from papercrawler.download.database import DownloadDatabase
        db = DownloadDatabase(str(tmp_path / "test.db"))
        db.upsert(
            doi="10.1038/test",
            hash_id="abc12345",
            title="Test",
            authors=["Smith"],
            year=2024,
            journal="Nature",
            access_status="oa_pdf",
            download_status="success",
            output_dir="/tmp/test",
        )
        assert db.is_downloaded("10.1038/test", "abc12345")

    def test_not_downloaded_initially(self, tmp_path):
        from papercrawler.download.database import DownloadDatabase
        db = DownloadDatabase(str(tmp_path / "test2.db"))
        assert not db.is_downloaded("10.1038/nonexistent", "xyz99999")

    def test_stats(self, tmp_path):
        from papercrawler.download.database import DownloadDatabase
        db = DownloadDatabase(str(tmp_path / "stats.db"))
        db.upsert("10.1/a", "h1", "T1", [], 2024, None, "oa_pdf", "success", ".")
        db.upsert("10.1/b", "h2", "T2", [], 2024, None, "metadata_only", "success", ".")
        db.upsert("10.1/c", "h3", "T3", [], 2024, None, "unknown", "failed", ".", "err")
        stats = db.stats()
        assert stats.get("success", 0) == 2
        assert stats.get("failed", 0) == 1


# ===========================================================================
# 集成测试（真实 arXiv API，默认跳过，CI 可用 --integration 开启）
# ===========================================================================

@pytest.mark.skip(reason="需要网络连接，本地运行时去掉 skip 标记")
class TestArXivIntegration:
    @pytest.mark.asyncio
    async def test_search_and_access_check(self):
        from papercrawler.search.manager import SearchManager
        from papercrawler.access.checker import AccessChecker
        from papercrawler.config import AppConfig

        cfg = AppConfig()
        manager = SearchManager(config=cfg)
        q = SearchQuery(query="attention transformer", max_results=3, sources=["arxiv"])
        papers = await manager.search(q)

        assert len(papers) > 0
        assert all(p.title for p in papers)

        checker = AccessChecker(config=cfg)
        for paper in papers:
            paper = await checker.check(paper)
            # arXiv 论文应为预印本
            assert paper.access_status == AccessStatus.OPEN_ACCESS_PREPRINT
            assert paper.oa_url is not None


# ===========================================================================
# 重构后的 search/base + manager + cli/_helpers 测试
# ===========================================================================

class TestSourceError:
    """SourceError 异常结构"""

    def test_construction_with_cause(self):
        from papercrawler.search.base import SourceError
        cause = ValueError("bad JSON")
        err = SourceError("openalex", "parse_error", "JSON broken", cause=cause)
        assert err.source_id == "openalex"
        assert err.kind == "parse_error"
        assert err.cause is cause
        assert "JSON broken" in str(err)

    def test_kind_known_values(self):
        from papercrawler.search.base import SourceError
        for kind in ("http_error", "parse_error", "timeout", "rate_limit", "other"):
            err = SourceError("test", kind, "msg")
            assert err.kind == kind


class TestSourceStats:
    """SourceStats dataclass + 兼容 shim"""

    def test_default_construction(self):
        from papercrawler.search.manager import SourceStats
        s = SourceStats()
        assert s.ok_count == 0
        assert s.failure_kind is None
        assert s.failure_message is None

    def test_full_construction(self):
        from papercrawler.search.manager import SourceStats
        s = SourceStats(ok_count=5, failure_kind="http_error", failure_message="401")
        assert s.ok_count == 5
        assert s.failure_kind == "http_error"
        assert s.failure_message == "401"

    def test_compat_shim_failure(self):
        from papercrawler.search.manager import SourceStats, source_stats_to_int_map
        s = SourceStats(failure_kind="http_error")
        back = source_stats_to_int_map({"s1": s})
        assert back == {"s1": -1}

    def test_compat_shim_success(self):
        from papercrawler.search.manager import SourceStats, source_stats_to_int_map
        s = SourceStats(ok_count=42)
        back = source_stats_to_int_map({"s1": s})
        assert back == {"s1": 42}


class TestBaseAdapterHttpHelpers:
    """BaseSearchAdapter._get_json / _get_text 公共行为"""

    @pytest.mark.asyncio
    async def test_get_json_success(self):
        from papercrawler.search.base import BaseSearchAdapter

        class _DummyAdapter(BaseSearchAdapter):
            SOURCE_ID = "dummy"
            async def search(self, query): return []

        import respx, httpx
        with respx.mock(base_url="https://dummy.test") as mock:
            mock.get("/ok").mock(return_value=httpx.Response(200, json={"hello": "world"}))
            a = _DummyAdapter(timeout=5, request_delay=0)
            data = await a._get_json("https://dummy.test/ok")
            assert data == {"hello": "world"}

    @pytest.mark.asyncio
    async def test_get_json_404_returns_none(self):
        from papercrawler.search.base import BaseSearchAdapter

        class _DummyAdapter(BaseSearchAdapter):
            SOURCE_ID = "dummy"
            async def search(self, query): return []

        import respx, httpx
        with respx.mock(base_url="https://dummy.test") as mock:
            mock.get("/missing").mock(return_value=httpx.Response(404))
            a = _DummyAdapter(timeout=5, request_delay=0)
            data = await a._get_json("https://dummy.test/missing")
            assert data is None

    @pytest.mark.asyncio
    async def test_get_json_parse_error_raises_source_error(self):
        """服务端返回 200 但 body 不是 JSON 时,应抛 SourceError(parse_error)"""
        from papercrawler.search.base import BaseSearchAdapter, SourceError

        class _DummyAdapter(BaseSearchAdapter):
            SOURCE_ID = "dummy"
            async def search(self, query): return []

        import respx, httpx
        with respx.mock(base_url="https://dummy.test") as mock:
            mock.get("/broken").mock(return_value=httpx.Response(200, text="not json {{{"))
            a = _DummyAdapter(timeout=5, request_delay=0)
            try:
                await a._get_json("https://dummy.test/broken")
            except SourceError as e:
                assert e.kind == "parse_error"
                assert e.source_id == "dummy"
                return
            assert False, "SourceError not raised"

    @pytest.mark.asyncio
    async def test_get_text_success(self):
        from papercrawler.search.base import BaseSearchAdapter

        class _DummyAdapter(BaseSearchAdapter):
            SOURCE_ID = "dummy"
            async def search(self, query): return []

        import respx, httpx
        with respx.mock(base_url="https://dummy.test") as mock:
            mock.get("/xml").mock(return_value=httpx.Response(200, text="<root/>"))
            a = _DummyAdapter(timeout=5, request_delay=0)
            data = await a._get_text("https://dummy.test/xml")
            assert data == "<root/>"


class TestCliHelpersDisplay:
    """_display_source_stats 新旧两种接口"""

    def test_display_with_new_sourcestats(self):
        from papercrawler.cli._helpers import _display_source_stats
        from papercrawler.search.manager import SourceStats
        # 不抛异常即通过(只调用 console.print)
        stats = {
            "openalex": SourceStats(ok_count=5),
            "semantic": SourceStats(failure_kind="rate_limit", failure_message="429"),
            "core":     SourceStats(failure_kind="http_error"),
        }
        _display_source_stats(stats)

    def test_display_with_legacy_int_dict(self):
        """向后兼容:旧调用方仍可能传 dict[str, int]"""
        from papercrawler.cli._helpers import _display_source_stats
        legacy = {"openalex": 5, "crossref": -1, "arxiv": 3}
        _display_source_stats(legacy)


class TestCliPackageLayout:
    """CLI 包结构测试 — 防止 cli.py 复活"""

    def test_cli_is_package(self):
        import os
        cli_path = os.path.join(os.path.dirname(__file__), "..", "papercrawler", "cli")
        assert os.path.isdir(cli_path), "papercrawler/cli should be a package (directory)"
        assert os.path.isfile(os.path.join(cli_path, "__init__.py"))

    def test_cli_no_legacy_module(self):
        """确保旧的 cli.py 不存在(避免包/模块冲突)"""
        import os
        legacy = os.path.join(os.path.dirname(__file__), "..", "papercrawler", "cli.py")
        assert not os.path.exists(legacy), f"{legacy} should not exist; cli/ is the new package"

    def test_cli_exports_app(self):
        from papercrawler.cli import app
        assert hasattr(app, "registered_commands")
        names = {c.name for c in app.registered_commands}
        assert {"search", "download", "batch", "recategorize", "convert"} <= names

    def test_cli_sub_typers(self):
        from papercrawler.cli import app
        sub_names = {g.name for g in app.registered_groups}
        assert {"history", "config"} <= sub_names
