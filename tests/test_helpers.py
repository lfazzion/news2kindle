"""Tests for DRY helpers, dataclasses, constants, and utility functions."""

import json
from dataclasses import asdict

import pytest

from core.config import (
    _BLOCKED_PHRASES,
    _INDEX_TITLES,
    _LEVEL_MERGE_PRIORITY,
    _NOISE_LINE_RE,
    _NOISE_PHRASE_RE,
    _PRELOADED_JSON_RE,
    _SECTION_SPLIT_RE,
    _TABLE_TAG_RE,
    LEVEL_ORDER,
    LEVEL_PRIORITY,
    CacheDocument,
    EnrichedNews,
    _extract_clean_response,
    _is_admin_text,
    _is_blocked_content,
    _is_junk_section,
    _strip_query,
    _validate_config,
)

# ===========================================================================
# Dataclasses
# ===========================================================================


class TestCacheDocument:
    def test_creation(self):
        doc = CacheDocument(id="doc_1", source="newsletter", text="Hello world")
        assert doc.id == "doc_1"
        assert doc.source == "newsletter"
        assert doc.text == "Hello world"

    def test_asdict_roundtrip(self):
        doc = CacheDocument(id="doc_1", source="link_externo", text="Some text")
        d = asdict(doc)
        restored = CacheDocument(**d)
        assert restored == doc

    def test_json_serialisation(self):
        doc = CacheDocument(id="doc_1", source="newsletter", text="Test")
        d = asdict(doc)
        json_str = json.dumps(d)
        restored = CacheDocument(**json.loads(json_str))
        assert restored == doc


class TestEnrichedNews:
    def test_default_anchor_id(self):
        news = EnrichedNews(title="Test", level="principal", content="Body text")
        assert news.anchor_id == ""

    def test_with_anchor_id(self):
        news = EnrichedNews(
            title="Test", level="secundaria", content="Body", anchor_id="noticia-3"
        )
        assert news.anchor_id == "noticia-3"


# ===========================================================================
# Helper functions
# ===========================================================================


class TestStripQuery:
    def test_removes_query_string(self):
        assert (
            _strip_query("https://example.com/article?ref=home")
            == "https://example.com/article"
        )

    def test_no_query_string(self):
        assert (
            _strip_query("https://example.com/article") == "https://example.com/article"
        )

    def test_multiple_question_marks(self):
        assert _strip_query("https://example.com/q?a=1?b=2") == "https://example.com/q"

    def test_empty_string(self):
        assert _strip_query("") == ""


class TestIsBlockedContent:
    @pytest.mark.parametrize("phrase", _BLOCKED_PHRASES)
    def test_detects_each_blocked_phrase(self, phrase):
        text = f"Some text before {phrase} and some text after"
        assert _is_blocked_content(text) is True

    def test_case_insensitive(self):
        assert _is_blocked_content("ACCESS DENIED on this page") is True

    def test_clean_content(self):
        assert (
            _is_blocked_content("This is a normal news article about the economy.")
            is False
        )

    def test_empty_text(self):
        assert _is_blocked_content("") is False


class TestExtractCleanResponse:
    def test_strips_json_code_fence(self):
        raw = '```json\n{"key": "value"}\n```'
        assert _extract_clean_response(raw) == '{"key": "value"}'

    def test_strips_html_code_fence(self):
        raw = "```html\n<h1>Title</h1>\n```"
        assert _extract_clean_response(raw) == "<h1>Title</h1>"

    def test_strips_generic_code_fence(self):
        raw = "```\nsome content\n```"
        assert _extract_clean_response(raw) == "some content"

    def test_no_code_fence(self):
        raw = '{"key": "value"}'
        assert _extract_clean_response(raw) == '{"key": "value"}'

    def test_preserves_content_with_extra_whitespace(self):
        raw = "  some content with spaces  "
        assert _extract_clean_response(raw) == "some content with spaces"

    def test_nested_backticks_inside_fence(self):
        raw = '```json\n{"code": "use ```three``` backticks"}\n```'
        result = _extract_clean_response(raw)
        assert "code" in result

    def test_multiple_code_fences_concatenated(self):
        raw = '```json\n{"a": 1}\n```\n```json\n{"b": 2}\n```'
        result = _extract_clean_response(raw)
        assert '{"a": 1}' in result
        assert '{"b": 2}' in result

    def test_no_code_fence_returns_stripped(self):
        raw = "  Some text without fences  "
        assert _extract_clean_response(raw) == "Some text without fences"


class TestIsAdminText:
    def test_matches_keyword(self):
        keywords = {"unsubscribe", "privacy", "manage"}
        assert _is_admin_text("click to unsubscribe here", keywords) is True

    def test_no_match(self):
        keywords = {"unsubscribe", "privacy", "manage"}
        assert _is_admin_text("breaking news about the economy", keywords) is False

    def test_empty_text(self):
        keywords = {"unsubscribe"}
        assert _is_admin_text("", keywords) is False

    def test_empty_keywords(self):
        assert _is_admin_text("some text", set()) is False


class TestIsJunkSection:
    def test_games_header(self):
        assert _is_junk_section("### Games\nPlay Wordle today!") is True

    def test_play_today_header(self):
        assert (
            _is_junk_section("## Play Today's Games\nAll the puzzles you love.") is True
        )

    def test_time_to_play(self):
        assert _is_junk_section("### Time to Play\nWordle and more.") is True

    def test_dealbook_quiz(self):
        assert _is_junk_section("DealBook Quiz\nQuestion 1: What company...") is True

    def test_footer_with_california_notices(self):
        assert (
            _is_junk_section(
                "Some footer text with california notices and legal stuff."
            )
            is True
        )

    def test_footer_too_long_is_not_junk(self):
        long_text = "california notices " + "x" * 5000
        assert _is_junk_section(long_text) is False

    def test_dense_game_keywords(self):
        text = "Play wordle and crossword today! Only 200 chars here."
        assert _is_junk_section(text) is True

    def test_single_game_keyword_not_junk(self):
        text = "Learn about the wordle phenomenon and how it changed gaming."
        assert _is_junk_section(text) is False

    def test_normal_news_content(self):
        text = (
            "The president signed a new executive order"
            " on trade policy today, affecting imports from China."
        )
        assert _is_junk_section(text) is False


# ===========================================================================
# Constants consistency
# ===========================================================================


class TestConstants:
    def test_level_order_matches_priority_keys(self):
        assert set(LEVEL_ORDER) == set(LEVEL_PRIORITY.keys())

    def test_level_order_sorted_by_priority(self):
        sorted_by_priority = sorted(LEVEL_PRIORITY, key=lambda k: LEVEL_PRIORITY[k])
        assert sorted_by_priority == LEVEL_ORDER

    def test_merge_priority_keys_match(self):
        assert set(_LEVEL_MERGE_PRIORITY.keys()) == set(LEVEL_PRIORITY.keys())

    def test_index_titles_keys_match_level_order(self):
        assert set(_INDEX_TITLES.keys()) == set(LEVEL_ORDER)

    def test_blocked_phrases_non_empty(self):
        assert len(_BLOCKED_PHRASES) > 0

    def test_all_blocked_phrases_lowercase(self):
        for phrase in _BLOCKED_PHRASES:
            assert phrase == phrase.lower(), (
                f"Blocked phrase '{phrase}' should be lowercase"
            )


# ===========================================================================
# Validate config
# ===========================================================================


class TestValidateConfig:
    def test_does_not_raise_when_all_set(self):
        """Should not raise when all env vars are set (they are in conftest)."""
        _validate_config()  # Should pass without error

    def test_raises_when_vars_missing(self, monkeypatch):
        import core.config as cfg

        monkeypatch.setattr(cfg, "EMAIL_ACCOUNT", None)
        monkeypatch.setattr(cfg, "GOOGLE_API_KEY", None)
        with pytest.raises(SystemExit) as exc_info:
            _validate_config()
        assert "EMAIL_ACCOUNT" in str(exc_info.value)
        assert "GOOGLE_API_KEY" in str(exc_info.value)


# ===========================================================================
# Regex patterns (smoke tests)
# ===========================================================================


class TestRegexPatterns:
    def test_noise_phrase_re_matches_unsubscribe(self):
        assert _NOISE_PHRASE_RE.search("Click here to unsubscribe from this list")

    def test_noise_phrase_re_matches_view_in_browser(self):
        assert _NOISE_PHRASE_RE.search("View in browser for a better experience")

    def test_noise_phrase_re_no_match_on_normal_text(self):
        assert _NOISE_PHRASE_RE.search("The weather today is sunny") is None

    def test_noise_line_re_matches_nytimes_footer(self):
        assert _NOISE_LINE_RE.search("nytimes.com")

    def test_table_tag_re_matches_table(self):
        assert _TABLE_TAG_RE.search("<table class='foo'>")
        assert _TABLE_TAG_RE.search("</td>")
        assert _TABLE_TAG_RE.search("<TR>")

    def test_section_split_re_matches_heading(self):
        assert _SECTION_SPLIT_RE.search("## Some Heading")

    def test_section_split_re_matches_horizontal_rule(self):
        assert _SECTION_SPLIT_RE.search("---")

    def test_section_split_re_matches_bold_title(self):
        assert _SECTION_SPLIT_RE.search("**BREAKING NEWS STORY**")

    def test_preloaded_json_re(self):
        html = 'window.__preloadedData = {"key": "val"};'
        match = _PRELOADED_JSON_RE.search(html)
        assert match
        assert '"key"' in match.group(1)
