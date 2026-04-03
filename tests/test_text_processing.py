"""Tests for text extraction, HTML parsing, newsletter splitting, and scraping logic."""

import asyncio
from unittest.mock import MagicMock

import pytest

from core.config import _MIN_SECTION_LENGTH, _is_junk_section, _strip_query
from core.extractor import (
    _split_newsletter_sections,
    extract_text_from_html,
)
from core.scraper import (
    _parse_html_to_markdown,
    _safe_legacy_fallback,
)

# ===========================================================================
# extract_text_from_html — real newsletter tests
# ===========================================================================


class TestExtractTextFromHtml:
    """Tests using real NYT newsletter HTML bodies loaded from .eml fixtures."""

    def test_extracts_text_from_morning_newsletter(self, morning_newsletter_html):
        if morning_newsletter_html is None:
            pytest.skip("Morning newsletter .eml not available")
        text, urls = extract_text_from_html(morning_newsletter_html)
        assert len(text) > 200, "Should extract substantial text"
        assert isinstance(urls, list)

    def test_extracts_text_from_dealbook(self, dealbook_newsletter_html):
        if dealbook_newsletter_html is None:
            pytest.skip("DealBook newsletter .eml not available")
        text, urls = extract_text_from_html(dealbook_newsletter_html)
        assert len(text) > 200

    def test_extracts_text_from_opinion(self, opinion_newsletter_html):
        if opinion_newsletter_html is None:
            pytest.skip("Opinion newsletter .eml not available")
        text, urls = extract_text_from_html(opinion_newsletter_html)
        assert len(text) > 200

    def test_extracts_text_from_world(self, world_newsletter_html):
        if world_newsletter_html is None:
            pytest.skip("World newsletter .eml not available")
        text, urls = extract_text_from_html(world_newsletter_html)
        assert len(text) > 200

    def test_extracts_text_from_ontech(self, ontech_newsletter_html):
        if ontech_newsletter_html is None:
            pytest.skip("On Tech newsletter .eml not available")
        text, urls = extract_text_from_html(ontech_newsletter_html)
        assert len(text) > 200

    def test_extracts_text_from_breaking_news(self, breaking_news_newsletter_html):
        if breaking_news_newsletter_html is None:
            pytest.skip("Breaking News newsletter .eml not available")
        text, urls = extract_text_from_html(breaking_news_newsletter_html)
        assert len(text) > 200

    def test_removes_noise_phrases(self, morning_newsletter_html):
        if morning_newsletter_html is None:
            pytest.skip("Morning newsletter .eml not available")
        text, _ = extract_text_from_html(morning_newsletter_html)
        text_lower = text.lower()
        assert "unsubscribe" not in text_lower
        assert "view in browser" not in text_lower
        assert "manage preferences" not in text_lower

    def test_removes_noise_lines(self, morning_newsletter_html):
        if morning_newsletter_html is None:
            pytest.skip("Morning newsletter .eml not available")
        text, _ = extract_text_from_html(morning_newsletter_html)
        for line in text.splitlines():
            line_stripped = line.strip().lower()
            if line_stripped:
                assert line_stripped != "nytimes.com"
                assert not line_stripped.startswith("the new york times company")

    def test_no_excessive_blank_lines(self, morning_newsletter_html):
        if morning_newsletter_html is None:
            pytest.skip("Morning newsletter .eml not available")
        text, _ = extract_text_from_html(morning_newsletter_html)
        assert "\n\n\n" not in text, "Should not have 3+ consecutive newlines"

    def test_collects_nyt_article_links(self, morning_newsletter_html):
        if morning_newsletter_html is None:
            pytest.skip("Morning newsletter .eml not available")
        _, urls = extract_text_from_html(morning_newsletter_html)
        assert len(urls) >= 1, "Should extract at least one article link"

    def test_deduplicates_links_via_seen_urls(self, morning_newsletter_html):
        if morning_newsletter_html is None:
            pytest.skip("Morning newsletter .eml not available")
        _, urls_first = extract_text_from_html(morning_newsletter_html)
        if not urls_first:
            pytest.skip("No URLs found to test deduplication")
        first_base = _strip_query(urls_first[0])
        seen = {first_base}
        _, urls_second = extract_text_from_html(morning_newsletter_html, seen_urls=seen)
        second_bases = [_strip_query(u) for u in urls_second]
        assert first_base not in second_bases

    def test_filters_out_deny_domain_links(self):
        """Links to denied domains should be filtered out."""
        html = """
        <html><body>
            <a href="https://www.wsj.com/economy/article123">
            WSJ article about economy that is long enough</a>
            <a href="https://www.nytimes.com/2026/03/article.html">
            A long NYT article title about the economy today</a>
        </body></html>
        """
        _, urls = extract_text_from_html(html)
        for url in urls:
            assert "wsj.com" not in url, "wsj.com links should be filtered"

    def test_filters_out_deny_path_links(self):
        """Links to cooking, puzzles, etc. on NYT should be filtered."""
        html = """
        <html><body>
            <a href="https://www.nytimes.com/cooking/recipe123">
            A great recipe for pasta that is really long title</a>
            <a href="https://www.nytimes.com/2026/03/article.html">
            A long NYT article about politics in March
            two thousand twenty six</a>
        </body></html>
        """
        _, urls = extract_text_from_html(html)
        for url in urls:
            assert "/cooking/" not in url, "Cooking links should be filtered"

    def test_empty_html_returns_empty(self):
        text, urls = extract_text_from_html("")
        assert text == ""
        assert urls == []

    def test_none_html_returns_empty(self):
        text, urls = extract_text_from_html(None)
        assert text == ""
        assert urls == []


# ===========================================================================
# _parse_html_to_markdown
# ===========================================================================


class TestParseHtmlToMarkdown:
    def test_simple_html(self):
        html = "<html><body><p>Hello World</p></body></html>"
        md, url = _parse_html_to_markdown(html, "https://example.com")
        assert "Hello World" in md
        assert url == "https://example.com"

    def test_preloaded_data_extraction(self):
        long_text = (
            "This is a long test article that should be"
            " extracted from the preloaded data. It has more"
            " than fifty characters to pass the length check."
            " And it also has more than five hundred characters"
            " in total when we combine all the text elements"
            " together. Let us add even more text to make sure"
            " it passes the minimum length threshold."
            " The economy is growing. Markets are up."
            " The president signed new legislation."
            " Congress debated the bill for hours."
            " The Supreme Court issued a ruling on privacy"
            " rights. Climate change activists protested"
            " outside the Capitol building. Scientists"
            " discovered a new species in the Amazon"
            " rainforest."
        )
        html = f"""<html><body>
        <script>
        window.__preloadedData = {{"article": {{"body": {{"text": "{long_text}"}}}}}};
        </script>
        <p>Fallback content</p>
        </body></html>"""
        md, url = _parse_html_to_markdown(html, "https://nytimes.com/article")
        assert len(md) > 0

    def test_blocked_content_returns_empty(self):
        html = (
            "<html><body><p>access denied - you are not a"
            " robot but we blocked you</p></body></html>"
        )
        md, url = _parse_html_to_markdown(html, "https://example.com")
        assert md == ""

    def test_story_body_companion_column(self):
        html = """<html><body>
        <div class="StoryBodyCompanionColumn"><p>First paragraph of the story.</p></div>
        <div class="StoryBodyCompanionColumn"><p>Second paragraph continues.</p></div>
        <div class="unrelated">Junk content</div>
        </body></html>"""
        md, _ = _parse_html_to_markdown(html, "https://nytimes.com")
        assert "First paragraph" in md
        assert "Second paragraph" in md

    def test_article_body_fallback(self):
        html = """<html><body>
        <section name="articleBody"><p>Article body content here.</p></section>
        <footer>Footer noise</footer>
        </body></html>"""
        md, _ = _parse_html_to_markdown(html, "https://example.com")
        assert "Article body content" in md


# ===========================================================================
# _split_newsletter_sections
# ===========================================================================


class TestSplitNewsletterSections:
    def test_split_by_heading(self):
        part1 = (
            "## First Topic\n" + "Content about first topic with plenty of"
            " detail to exceed one hundred chars easily. " * 2
        )
        part2 = (
            "## Second Topic\n" + "Content about second topic with more details"
            " to also exceed the minimum length threshold. " * 2
        )
        text = part1 + "\n\n" + part2
        sections = _split_newsletter_sections(text)
        assert len(sections) >= 2

    def test_split_by_horizontal_rule(self):
        part1 = (
            "First part with enough content to be a valid"
            " section that exceeds the minimum length"
            " threshold. " * 2
        )
        part2 = (
            "Second part with enough content to be a valid"
            " section that also exceeds the minimum length"
            " threshold. " * 2
        )
        text = part1 + "\n\n---\n\n" + part2
        sections = _split_newsletter_sections(text)
        assert len(sections) >= 2

    def test_split_by_bold_title(self):
        part1 = (
            "**BREAKING NEWS STORY**\n" + "Content of the story goes here with details"
            " about the economy and global trade policy"
            " impacts. " * 2
        )
        part2 = (
            "**ANOTHER MAJOR STORY**\n" + "More content about a different topic with"
            " lots of additional details about science"
            " discoveries. " * 2
        )
        text = part1 + "\n\n" + part2
        sections = _split_newsletter_sections(text)
        assert len(sections) >= 2

    def test_short_fragment_merged(self):
        text = "## A\nShort\n\n## Real Section\n" + "Content " * 50
        sections = _split_newsletter_sections(text)
        for section in sections:
            assert len(section) >= _MIN_SECTION_LENGTH or len(sections) == 1

    def test_single_section_returns_original(self):
        text = "Just a single paragraph of text without any section markers."
        sections = _split_newsletter_sections(text)
        assert len(sections) == 1
        assert sections[0] == text

    def test_empty_text(self):
        sections = _split_newsletter_sections("")
        assert sections == []

    def test_whitespace_only(self):
        sections = _split_newsletter_sections("   \n\n   ")
        assert sections == []

    def test_real_newsletter_splitting(self, morning_newsletter_html):
        if morning_newsletter_html is None:
            pytest.skip("Morning newsletter .eml not available")
        text, _ = extract_text_from_html(morning_newsletter_html)
        sections = _split_newsletter_sections(text)
        assert len(sections) >= 1
        for section in sections:
            assert len(section.strip()) > 0

    def test_junk_sections_detected_in_real_newsletter(self, morning_newsletter_html):
        if morning_newsletter_html is None:
            pytest.skip("Morning newsletter .eml not available")
        text, _ = extract_text_from_html(morning_newsletter_html)
        sections = _split_newsletter_sections(text)
        non_junk = [s for s in sections if not _is_junk_section(s)]
        assert len(non_junk) >= 1, "At least one non-junk section should exist"


# ===========================================================================
# _fetch_legacy_sync — CloudScraper fallback (unit-level, mocked)
# ===========================================================================


class TestFetchLegacySync:
    def test_returns_empty_on_exception(self, monkeypatch):
        """Should return ('', url) on any exception from CloudScraper."""
        mock_scraper = MagicMock()
        mock_scraper.get.side_effect = ConnectionError("Simulated connection failure")
        monkeypatch.setattr("core.scraper._get_legacy_scraper", lambda: mock_scraper)

        md, url = asyncio.run(_safe_legacy_fallback("https://example.com/article"))
        assert md == ""
        assert url == "https://example.com/article"
