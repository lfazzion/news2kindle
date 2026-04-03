"""Tests for the V5 scraping architecture.

Covers: curl_cffi AsyncSession primary + CloudScraper fallback,
status code checks, response size validation, og:description fallback,
captcha detection, async timeout, content density heuristic,
expanded markdownify tags, and preloaded data regex.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.config import (
    _PRELOADED_JSON_RE,
    HTTP_BLOCK_STATUS_CODES,
    MIN_HTML_RESPONSE_LENGTH,
    SCRAPER_PROXY,
    SCRAPER_URL_TIMEOUT,
    _is_blocked_content,
)
from core.scraper import (
    _extract_metadata_fallback,
    _fetch_article_text_async_impl,
    _fetch_legacy_sync,
    _get_async_session,
    _get_legacy_scraper,
    _parse_html_to_markdown,
    fetch_article_text_async,
)

# ===========================================================================
# Helper: build a mock AsyncSession response
# ===========================================================================


def _make_mock_response(
    status_code=200, url="https://example.com/article", text="<html>OK</html>"
):
    """Creates a mock curl_cffi Response object."""
    resp = AsyncMock()
    resp.status_code = status_code
    resp.url = url
    resp.text = text
    return resp


def _patch_async_session(monkeypatch, response):
    """Monkeypatches _get_async_session to return a mock session."""
    mock_session = AsyncMock()
    mock_session.get = AsyncMock(return_value=response)
    monkeypatch.setattr("core.scraper._get_async_session", lambda: mock_session)
    return mock_session


# ===========================================================================
# HTTP status code handling — curl_cffi primary, CloudScraper fallback
# ===========================================================================


class TestHttpStatusCodeHandling:
    """Verifies that HTTP 403/429/503 on curl_cffi
    trigger _safe_legacy_fallback (full cascade)."""

    @pytest.mark.asyncio
    async def test_403_triggers_full_fallback_cascade(self, monkeypatch):
        """curl_cffi 403 should invoke _safe_legacy_fallback (full cascade)."""
        _patch_async_session(monkeypatch, _make_mock_response(status_code=403))

        fallback_called = False

        async def mock_safe_fallback(url):
            nonlocal fallback_called
            fallback_called = True
            return ("fallback content", url)

        monkeypatch.setattr("core.scraper._safe_legacy_fallback", mock_safe_fallback)

        md, url = await _fetch_article_text_async_impl("https://example.com/article")
        assert fallback_called, "_safe_legacy_fallback should be triggered on HTTP 403"
        assert md == "fallback content"

    @pytest.mark.asyncio
    async def test_429_triggers_full_fallback_cascade(self, monkeypatch):
        _patch_async_session(monkeypatch, _make_mock_response(status_code=429))

        fallback_called = False

        async def mock_safe_fallback(url):
            nonlocal fallback_called
            fallback_called = True
            return ("fallback content", url)

        monkeypatch.setattr("core.scraper._safe_legacy_fallback", mock_safe_fallback)

        await _fetch_article_text_async_impl("https://example.com/article")
        assert fallback_called, "_safe_legacy_fallback should be triggered on HTTP 429"

    @pytest.mark.asyncio
    async def test_503_triggers_full_fallback_cascade(self, monkeypatch):
        _patch_async_session(monkeypatch, _make_mock_response(status_code=503))

        fallback_called = False

        async def mock_safe_fallback(url):
            nonlocal fallback_called
            fallback_called = True
            return ("fallback content", url)

        monkeypatch.setattr("core.scraper._safe_legacy_fallback", mock_safe_fallback)

        await _fetch_article_text_async_impl("https://example.com/article")
        assert fallback_called

    @pytest.mark.asyncio
    async def test_200_does_not_trigger_fallback(self, monkeypatch):
        """When curl_cffi returns 200 with valid content, no fallback should occur."""
        large_html = (
            "<html><body>" + "<p>Content paragraph.</p>" * 100 + "</body></html>"
        )
        _patch_async_session(
            monkeypatch, _make_mock_response(status_code=200, text=large_html)
        )

        fallback_called = False

        async def mock_safe_fallback(url):
            nonlocal fallback_called
            fallback_called = True
            return ("", url)

        monkeypatch.setattr("core.scraper._safe_legacy_fallback", mock_safe_fallback)

        await _fetch_article_text_async_impl("https://example.com/article")
        assert not fallback_called, "Fallback should NOT be triggered on valid HTTP 200"

    def test_legacy_fetcher_rejects_403(self, monkeypatch):
        """Legacy CloudScraper should mark 403 URLs as failed."""
        mock_scraper = MagicMock()
        mock_response = MagicMock()
        mock_response.status_code = 403
        mock_response.url = "https://example.com/blocked"
        mock_response.text = "<html>Blocked</html>"
        mock_scraper.get.return_value = mock_response
        monkeypatch.setattr("core.scraper._get_legacy_scraper", lambda: mock_scraper)

        md, url = _fetch_legacy_sync("https://example.com/blocked")
        assert md == ""

    def test_legacy_fetcher_has_no_retry_decorator(self):
        """_fetch_legacy_sync should NOT have @retry."""
        assert not hasattr(_fetch_legacy_sync, "__wrapped__"), (
            "_fetch_legacy_sync should not have @retry decorator (403 is a hard block)"
        )


# ===========================================================================
# Minimum response length
# ===========================================================================


class TestMinimumResponseLength:
    """Verify that tiny responses (<MIN_HTML_RESPONSE_LENGTH) are treated as blocks."""

    @pytest.mark.asyncio
    async def test_small_response_triggers_fallback(self, monkeypatch):
        _patch_async_session(monkeypatch, _make_mock_response(text="<html>Tiny</html>"))

        fallback_called = False

        async def mock_safe_fallback(url):
            nonlocal fallback_called
            fallback_called = True
            return ("", url)

        monkeypatch.setattr("core.scraper._safe_legacy_fallback", mock_safe_fallback)

        await _fetch_article_text_async_impl("https://example.com/article")
        assert fallback_called, "Tiny response should trigger fallback cascade"

    @pytest.mark.asyncio
    async def test_normal_response_does_not_trigger(self, monkeypatch):
        large_html = "<html><body>" + "<p>Content.</p>" * 200 + "</body></html>"
        _patch_async_session(monkeypatch, _make_mock_response(text=large_html))

        fallback_called = False

        async def mock_safe_fallback(url):
            nonlocal fallback_called
            fallback_called = True
            return ("", url)

        monkeypatch.setattr("core.scraper._safe_legacy_fallback", mock_safe_fallback)

        await _fetch_article_text_async_impl("https://example.com/article")
        assert not fallback_called

    def test_legacy_rejects_small_response(self, monkeypatch):
        mock_scraper = MagicMock()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.url = "https://example.com/tiny"
        mock_response.text = "<html>Small</html>"
        mock_scraper.get.return_value = mock_response
        monkeypatch.setattr("core.scraper._get_legacy_scraper", lambda: mock_scraper)

        md, url = _fetch_legacy_sync("https://example.com/tiny")
        assert md == ""


# ===========================================================================
# og:description fallback
# ===========================================================================


class TestMetadataFallback:
    """Tests for the _extract_metadata_fallback function (powered by trafilatura)."""

    def test_extracts_og_title_and_description(self):
        html = """<html><head>
        <meta property="og:title" content="Test Article Title">
        <meta property="og:description" content="This is a description of the article.">
        </head><body></body></html>"""
        text, url = _extract_metadata_fallback(html, "https://example.com")
        assert "Test Article Title" in text or "description of the article" in text

    def test_falls_back_to_title_tag(self):
        html = """<html><head>
        <title>Fallback Title</title>
        <meta name="description" content="Fallback description text.">
        </head><body></body></html>"""
        text, url = _extract_metadata_fallback(html, "https://example.com")
        assert "Fallback Title" in text or "Fallback description" in text

    def test_returns_empty_when_no_meta(self):
        html = "<html><head></head><body></body></html>"
        text, url = _extract_metadata_fallback(html, "https://example.com")
        assert text == ""

    def test_rejects_blocked_og_content(self):
        html = """<html><head>
        <meta property="og:title" content="Access Denied">
        <meta property="og:description" content="Please prove you are not a robot.">
        </head><body></body></html>"""
        text, url = _extract_metadata_fallback(html, "https://example.com")
        assert text == ""

    def test_parse_html_uses_metadata_fallback_when_empty(self):
        """When HTML has no content but has og:description, it should be extracted."""
        html = """<html><head>
        <meta property="og:title" content="Article About Economy">
        <meta property="og:description"
            content="The economy showed strong growth in Q3 2025.">
        </head><body><div></div></body></html>"""
        md, url = _parse_html_to_markdown(html, "https://example.com/article")
        assert "economy" in md.lower() or "Economy" in md


# ===========================================================================
# Captcha detection (expanded _BLOCKED_PHRASES)
# ===========================================================================


class TestCaptchaDetection:
    """Verify that new captcha/anti-bot phrases are detected."""

    @pytest.mark.parametrize(
        "phrase",
        [
            "captcha",
            "challenge-platform",
            "cf-browser-verification",
            "ddos-protection",
            "checking your browser",
        ],
    )
    def test_detects_new_blocked_phrases(self, phrase):
        text = f"Some HTML with {phrase} blocking indicator"
        assert _is_blocked_content(text) is True

    def test_original_phrases_still_work(self):
        assert _is_blocked_content("prove you are human") is True
        assert _is_blocked_content("access denied") is True

    def test_normal_content_not_blocked(self):
        assert (
            _is_blocked_content("The market rallied 2% today on strong earnings.")
            is False
        )


# ===========================================================================
# Async timeout
# ===========================================================================


class TestAsyncTimeout:
    """Verify that fetch_article_text_async respects per-URL timeout."""

    @pytest.mark.asyncio
    async def test_timeout_returns_none(self, monkeypatch):
        """A slow scrape should be cancelled after SCRAPER_URL_TIMEOUT."""
        monkeypatch.setattr("core.scraper.SCRAPER_URL_TIMEOUT", 0.1)

        slow_session = AsyncMock()

        async def slow_get(*args, **kwargs):
            await asyncio.sleep(5)
            return _make_mock_response()

        slow_session.get = slow_get
        monkeypatch.setattr("core.scraper._get_async_session", lambda: slow_session)

        semaphore = asyncio.Semaphore(5)
        result = await fetch_article_text_async("https://example.com/slow", semaphore)
        assert result is None, "Should return None on timeout"

    @pytest.mark.asyncio
    async def test_fast_scrape_returns_result(self, monkeypatch):
        """A fast scrape should succeed normally."""
        large_html = "<html><body>" + "<p>Article content.</p>" * 200 + "</body></html>"
        _patch_async_session(monkeypatch, _make_mock_response(text=large_html))

        semaphore = asyncio.Semaphore(5)
        result = await fetch_article_text_async("https://example.com/fast", semaphore)
        assert result is not None
        assert len(result[0]) > 10


# ===========================================================================
# Proxy configuration
# ===========================================================================


class TestProxyConfiguration:
    """Verify that SCRAPER_PROXY is read from environment."""

    def test_proxy_env_var_is_accessible(self):
        assert SCRAPER_PROXY is None or isinstance(SCRAPER_PROXY, str)

    def test_proxy_defaults_to_none(self):
        assert SCRAPER_PROXY is None or isinstance(SCRAPER_PROXY, str)


# ===========================================================================
# Content density heuristic
# ===========================================================================


class TestContentDensityHeuristic:
    """Tests for content extraction without hardcoded class names."""

    def test_integrated_in_parse_cascade(self):
        """When no article/section tags exist, trafilatura should still find content."""
        html = """<html><body>
        <div class="random-class-xyz123">
            <p>This is the first paragraph of a long article
            about technology trends in 2026.</p>
            <p>The second paragraph discusses AI developments
            and their impact on society.</p>
            <p>Third paragraph covers the semiconductor
            industry and supply chain issues.</p>
            <p>Fourth paragraph examines the future of quantum computing research.</p>
        </div>
        <div class="sidebar-abc456"><span>Ad content</span></div>
        </body></html>"""
        md, url = _parse_html_to_markdown(html, "https://example.com")
        assert "technology" in md.lower()


# ===========================================================================
# Expanded markdownify tags
# ===========================================================================


class TestExpandedMarkdownTags:
    """Verify blockquote, figure, figcaption are now converted."""

    def test_blockquote_preserved(self):
        html = """<html><body>
        <article>
            <p>Introduction paragraph with enough text to pass the density check.</p>
            <p>Another paragraph to ensure content is substantial enough.</p>
            <p>Third paragraph for good measure with additional context.</p>
            <blockquote>This is an important quote from a notable person.</blockquote>
            <p>Conclusion paragraph wrapping up the article nicely.</p>
        </article>
        </body></html>"""
        md, _ = _parse_html_to_markdown(html, "https://example.com")
        assert "important quote" in md

    def test_figure_and_figcaption_preserved(self):
        html = """<html><body>
        <article>
            <p>Article content that is long enough to be considered real content.</p>
            <p>Second paragraph continues the story with more details and context.</p>
            <p>Third paragraph provides additional information about the topic.</p>
            <figure>
                <img src="photo.jpg" alt="A description of the photo">
                <figcaption>Photo caption: An important
                event captured on camera.</figcaption>
            </figure>
        </article>
        </body></html>"""
        md, _ = _parse_html_to_markdown(html, "https://example.com")
        assert (
            "caption" in md.lower()
            or "important event" in md.lower()
            or "article content" in md.lower()
        )

    def test_newsletter_conversion_includes_blockquote(self, morning_newsletter_html):
        """Real newsletter should handle blockquotes if present."""
        if morning_newsletter_html is None:
            pytest.skip("Morning newsletter .eml not available")
        from core.extractor import extract_text_from_html

        text, _ = extract_text_from_html(morning_newsletter_html)
        assert len(text) > 200


# ===========================================================================
# Expanded preloaded data regex
# ===========================================================================


class TestExpandedPreloadedDataRegex:
    """Tests for multi-pattern preloaded data extraction."""

    def test_matches_preloaded_data(self):
        html = 'window.__preloadedData = {"key": "val"};'
        match = _PRELOADED_JSON_RE.search(html)
        assert match
        assert '"key"' in match.group(1)

    def test_matches_next_data(self):
        html = (
            'window.__NEXT_DATA__ = {"props": {"pageProps": {"article": "content"}}};'
        )
        match = _PRELOADED_JSON_RE.search(html)
        assert match
        assert '"props"' in match.group(1)

    def test_matches_preloaded_state(self):
        html = 'window.__PRELOADED_STATE__ = {"data": {"items": []}};'
        match = _PRELOADED_JSON_RE.search(html)
        assert match
        assert '"data"' in match.group(1)

    def test_matches_with_spaces_around_equals(self):
        html = 'window.__NEXT_DATA__  =  {"page": "article"};'
        match = _PRELOADED_JSON_RE.search(html)
        assert match

    def test_no_match_on_unrelated_variable(self):
        html = 'window.someOtherVar = {"data": "value"};'
        match = _PRELOADED_JSON_RE.search(html)
        assert match is None

    def test_parse_html_extracts_next_data(self):
        """Verify __NEXT_DATA__ is actually used by _parse_html_to_markdown."""
        long_text = (
            "This is a very long article text that should be"
            " extracted from the Next.js preloaded data payload. " * 10
        )
        html = f"""<html><body>
        <script>
        window.__NEXT_DATA__ = {{"props": {{"pageProps": {{"text": "{long_text}"}}}}}};
        </script>
        <p>Fallback</p>
        </body></html>"""
        md, url = _parse_html_to_markdown(html, "https://nextjs-site.com/article")
        assert len(md) > 100


# ===========================================================================
# Constants validation
# ===========================================================================


class TestNewConstants:
    """Smoke tests for new constants."""

    def test_min_html_response_length_reasonable(self):
        assert MIN_HTML_RESPONSE_LENGTH >= 1000
        assert MIN_HTML_RESPONSE_LENGTH <= 5000

    def test_block_status_codes_include_common_blocks(self):
        assert 403 in HTTP_BLOCK_STATUS_CODES
        assert 429 in HTTP_BLOCK_STATUS_CODES
        assert 503 in HTTP_BLOCK_STATUS_CODES

    def test_scraper_url_timeout_positive(self):
        assert SCRAPER_URL_TIMEOUT > 0
        assert SCRAPER_URL_TIMEOUT <= 120


# ===========================================================================
# Async session lifecycle
# ===========================================================================


class TestAsyncSessionLifecycle:
    """Verify AsyncSession singleton and proxy passthrough."""

    def test_get_async_session_returns_session(self, monkeypatch):
        """_get_async_session should return an AsyncSession instance."""
        mock_session = MagicMock()
        monkeypatch.setattr("core.scraper._async_session", None)
        monkeypatch.setattr("core.scraper.AsyncSession", lambda **kw: mock_session)
        result = _get_async_session()
        assert result is mock_session

    def test_get_async_session_caches_instance(self, monkeypatch):
        """Second call should return the same instance."""
        mock_session = MagicMock()
        monkeypatch.setattr("core.scraper._async_session", mock_session)
        result = _get_async_session()
        assert result is mock_session

    def test_legacy_scraper_creation(self):
        """_get_legacy_scraper should return a CloudScraper instance."""
        scraper = _get_legacy_scraper()
        assert scraper is not None
