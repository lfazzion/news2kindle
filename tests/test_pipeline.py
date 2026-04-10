"""Tests for Phase 2.5 matcher, Phase 2 categorizer, and Phase 3 summarizer."""

import json
from unittest.mock import MagicMock

import pytest
from google.genai import errors as genai_errors

from core.config import (
    CacheDocument,
    EnrichedNews,
)
from core.pipeline import (
    _validate_grouped_item,
    categorize_news,
    match_cache_to_groups,
    summarize_text,
)

# ===========================================================================
# match_cache_to_groups (Phase 2.5 — pure Python, no API)
# ===========================================================================


class TestMatchCacheToGroups:
    def test_basic_matching(self, sample_cache_docs, sample_grouped_news):
        result = match_cache_to_groups(sample_grouped_news, sample_cache_docs)
        assert len(result) == 5  # Each group has one matching doc
        assert all(isinstance(r, EnrichedNews) for r in result)

    def test_preserves_titles_and_levels(self, sample_cache_docs, sample_grouped_news):
        result = match_cache_to_groups(sample_grouped_news, sample_cache_docs)
        titles = {r.title for r in result}
        assert "Economia em Recuperação" in titles

    def test_principal_claims_first(self, sample_cache_docs):
        """When two groups claim the same doc_id, the principal-level group wins."""
        grouped = [
            {
                "title": "Secundaria Title",
                "level": "secundaria",
                "cache_ids": ["doc_1"],
            },
            {"title": "Principal Title", "level": "principal", "cache_ids": ["doc_1"]},
        ]
        result = match_cache_to_groups(grouped, sample_cache_docs)
        assert len(result) == 1
        assert result[0].level == "principal"

    def test_missing_cache_id_logged(self, sample_cache_docs, caplog):
        grouped = [
            {"title": "Nonexistent", "level": "secundaria", "cache_ids": ["doc_999"]},
        ]
        result = match_cache_to_groups(grouped, sample_cache_docs)
        assert len(result) == 0
        assert "not found in cache" in caplog.text

    def test_empty_groups(self, sample_cache_docs):
        result = match_cache_to_groups([], sample_cache_docs)
        assert result == []

    def test_empty_cache(self):
        grouped = [
            {"title": "Some News", "level": "principal", "cache_ids": ["doc_1"]},
        ]
        result = match_cache_to_groups(grouped, [])
        assert result == []

    def test_merges_multiple_docs_into_content(self, sample_cache_docs):
        grouped = [
            {
                "title": "Economy & War",
                "level": "principal",
                "cache_ids": ["doc_1", "doc_2"],
            },
        ]
        result = match_cache_to_groups(grouped, sample_cache_docs)
        assert len(result) == 1
        assert "---" in result[0].content  # Separator between merged texts
        assert "economy" in result[0].content.lower()
        assert "War" in result[0].content

    def test_skips_duplicate_ids_across_groups(self, sample_cache_docs, caplog):
        grouped = [
            {"title": "Group A", "level": "principal", "cache_ids": ["doc_1"]},
            {
                "title": "Group B",
                "level": "secundaria",
                "cache_ids": ["doc_1", "doc_2"],
            },
        ]
        result = match_cache_to_groups(grouped, sample_cache_docs)
        assert len(result) == 2
        group_a = next(r for r in result if r.title == "Group A")
        group_b = next(r for r in result if r.title == "Group B")
        assert "economy" in group_a.content.lower()
        assert "War" in group_b.content

    def test_orphaned_docs_logged(self, sample_cache_docs, caplog):
        grouped = [
            {"title": "Only Doc 1", "level": "principal", "cache_ids": ["doc_1"]},
        ]
        match_cache_to_groups(grouped, sample_cache_docs)
        assert "not referenced by any group" in caplog.text


# ===========================================================================
# categorize_news (Phase 2 — tests with mocked GenAI)
# ===========================================================================


class TestCategorizeNews:
    @pytest.fixture
    def small_cache(self):
        return [
            CacheDocument(
                id="doc_1", source="newsletter", text="Economy news content."
            ),
            CacheDocument(id="doc_2", source="newsletter", text="War news content."),
        ]

    @pytest.mark.asyncio
    async def test_returns_none_without_api_key(self, small_cache, monkeypatch):
        monkeypatch.setattr("core.pipeline._get_genai_client", lambda: None)
        result = await categorize_news(small_cache)
        assert result is None

    @pytest.mark.asyncio
    async def test_successful_categorization(self, small_cache, monkeypatch):
        """Mocks a successful GenAI response and verifies the output structure."""
        mock_response = MagicMock()
        mock_response.text = json.dumps(
            {
                "grouped_news": [
                    {"title": "Economia", "level": "principal", "cache_ids": ["doc_1"]},
                    {"title": "Guerra", "level": "secundaria", "cache_ids": ["doc_2"]},
                ]
            }
        )

        mock_client = MagicMock()
        monkeypatch.setattr("core.pipeline._get_genai_client", lambda: mock_client)

        async def mock_generate(*args, **kwargs):
            return mock_response

        monkeypatch.setattr("core.pipeline._generate_content_async", mock_generate)

        result = await categorize_news(small_cache)
        assert result is not None
        assert "grouped_news" in result
        assert len(result["grouped_news"]) == 2

    @pytest.mark.asyncio
    async def test_handles_invalid_json_schema(self, small_cache, monkeypatch):
        """When the LLM returns invalid schema, should gracefully handle."""
        mock_response = MagicMock()
        mock_response.text = json.dumps({"invalid_key": []})

        mock_client = MagicMock()
        monkeypatch.setattr("core.pipeline._get_genai_client", lambda: mock_client)

        async def mock_generate(*args, **kwargs):
            return mock_response

        monkeypatch.setattr("core.pipeline._generate_content_async", mock_generate)

        result = await categorize_news(small_cache)
        assert result is None  # No valid groups means None

    @pytest.mark.asyncio
    async def test_deduplicates_across_chunks(self, monkeypatch):
        """When two chunks produce groups with the same title, they should be merged."""
        mock_client = MagicMock()
        monkeypatch.setattr("core.pipeline._get_genai_client", lambda: mock_client)

        call_count = 0

        async def mock_generate(client, prompt, model=None):
            nonlocal call_count
            call_count += 1
            response = MagicMock()
            if call_count == 1:
                response.text = json.dumps(
                    {
                        "grouped_news": [
                            {
                                "title": "Economy",
                                "level": "principal",
                                "cache_ids": ["doc_1"],
                            }
                        ]
                    }
                )
            else:
                response.text = json.dumps(
                    {
                        "grouped_news": [
                            {
                                "title": "Economy",
                                "level": "secundaria",
                                "cache_ids": ["doc_2"],
                            }
                        ]
                    }
                )
            return response

        monkeypatch.setattr("core.pipeline._generate_content_async", mock_generate)
        monkeypatch.setattr("core.pipeline.MAX_TOKENS_PER_CHUNK", 1)

        cache = [
            CacheDocument(id="doc_1", source="newsletter", text="Economy article one."),
            CacheDocument(id="doc_2", source="newsletter", text="Economy article two."),
        ]
        result = await categorize_news(cache)
        assert result is not None
        groups = result["grouped_news"]
        economy_groups = [g for g in groups if g["title"].lower() == "economy"]
        assert len(economy_groups) == 1
        assert set(economy_groups[0]["cache_ids"]) == {"doc_1", "doc_2"}
        assert economy_groups[0]["level"] == "principal"

    @pytest.mark.asyncio
    async def test_fallback_on_api_error(self, small_cache, monkeypatch):
        """When the API fails, should create a fallback bucket."""
        mock_client = MagicMock()
        monkeypatch.setattr("core.pipeline._get_genai_client", lambda: mock_client)

        async def mock_generate(*args, **kwargs):
            raise genai_errors.APIError(503, response_json={"message": "Service down"})

        monkeypatch.setattr("core.pipeline._generate_content_async", mock_generate)

        result = await categorize_news(small_cache)
        assert result is not None
        groups = result["grouped_news"]
        assert len(groups) == 1
        assert groups[0]["title"] == "Recortes Adicionais"
        assert set(groups[0]["cache_ids"]) == {"doc_1", "doc_2"}


# ===========================================================================
# summarize_text (Phase 3 — tests with mocked GenAI)
# ===========================================================================


class TestSummarizeText:
    @pytest.mark.asyncio
    async def test_returns_none_without_api_key(
        self, sample_enriched_news, monkeypatch
    ):
        monkeypatch.setattr("core.pipeline._get_genai_client", lambda: None)
        result = await summarize_text(sample_enriched_news)
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_for_empty_list(self, monkeypatch):
        mock_client = MagicMock()
        monkeypatch.setattr("core.pipeline._get_genai_client", lambda: mock_client)
        result = await summarize_text([])
        assert result is None

    @pytest.mark.asyncio
    async def test_successful_html_generation(self, sample_enriched_news, monkeypatch):
        mock_client = MagicMock()
        monkeypatch.setattr("core.pipeline._get_genai_client", lambda: mock_client)

        async def mock_generate(*args, **kwargs):
            response = MagicMock()
            response.text = "<h1>Test Article</h1><p>Translated content.</p>"
            return response

        monkeypatch.setattr("core.pipeline._generate_content_async", mock_generate)

        result = await summarize_text(sample_enriched_news)
        assert result is not None
        assert "<html>" in result
        assert "</html>" in result
        assert "Newsletter Summary" in result
        assert "Índice de Hoje" in result

    @pytest.mark.asyncio
    async def test_generates_table_of_contents(self, sample_enriched_news, monkeypatch):
        mock_client = MagicMock()
        monkeypatch.setattr("core.pipeline._get_genai_client", lambda: mock_client)

        async def mock_generate(*args, **kwargs):
            response = MagicMock()
            response.text = "<p>Translated content.</p>"
            return response

        monkeypatch.setattr("core.pipeline._generate_content_async", mock_generate)

        result = await summarize_text(sample_enriched_news)
        assert result is not None
        assert "noticia-0" in result
        assert "Editoriais Principais" in result or "Giro de Notícias" in result

    @pytest.mark.asyncio
    async def test_retries_failed_bandejas(self, sample_enriched_news, monkeypatch):
        mock_client = MagicMock()
        monkeypatch.setattr("core.pipeline._get_genai_client", lambda: mock_client)

        call_count = 0

        async def mock_generate(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise ConnectionError("Simulated first failure")
            response = MagicMock()
            response.text = "<p>Retry succeeded.</p>"
            return response

        monkeypatch.setattr("core.pipeline._generate_content_async", mock_generate)

        news = [
            EnrichedNews(
                title="Test", level="principal", content="Content", anchor_id="n-0"
            )
        ]
        result = await summarize_text(news)
        assert result is not None
        assert "Retry succeeded" in result


# ===========================================================================
# _validate_grouped_item (CS-05 — pure Python, no API)
# ===========================================================================


class TestValidateGroupedItem:
    def test_valid_item_passes(self):
        item = {"title": "Título OK", "level": "principal", "cache_ids": ["doc_1"]}
        assert _validate_grouped_item(item, 0) is True

    def test_invalid_level_corrected_to_secundaria(self):
        item = {"title": "T", "level": "tertiary", "cache_ids": ["doc_1"]}
        assert _validate_grouped_item(item, 0) is True
        assert item["level"] == "secundaria"

    def test_missing_title_returns_false(self):
        item = {"title": "", "level": "principal", "cache_ids": ["doc_1"]}
        assert _validate_grouped_item(item, 0) is False

    def test_none_title_returns_false(self):
        item = {"title": None, "level": "principal", "cache_ids": ["doc_1"]}
        assert _validate_grouped_item(item, 0) is False

    def test_empty_cache_ids_returns_false(self):
        item = {"title": "T", "level": "secundaria", "cache_ids": []}
        assert _validate_grouped_item(item, 0) is False

    def test_none_cache_ids_returns_false(self):
        item = {"title": "T", "level": "secundaria", "cache_ids": None}
        assert _validate_grouped_item(item, 0) is False

    def test_valid_levels_all_pass(self):
        for level in ("principal", "secundaria", "notas_curtas"):
            item = {"title": "T", "level": level, "cache_ids": ["doc_1"]}
            assert _validate_grouped_item(item, 0) is True
            assert item["level"] == level  # not modified
