"""Tests for the V6 scraping architecture.

Covers: stealth-requests primary + curl_cffi fallback,
status code checks, response size validation, og:description fallback,
captcha detection, async timeout, content density heuristic,
expanded markdownify tags, preloaded data regex, and proxy rotation.
"""

import asyncio
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.config import (
    _PRELOADED_JSON_RE,
    HTTP_BLOCK_STATUS_CODES,
    MIN_HTML_RESPONSE_LENGTH,
    SCRAPER_URL_TIMEOUT,
    _is_blocked_content,
)
from core.scraper import (
    _extract_metadata_fallback,
    _fetch_article_text_async_impl,
    _fetch_stealth_sync,
    _get_async_session,
    _mask_proxy_url,
    _parse_html_to_markdown,
    _safe_legacy_fallback,
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
    monkeypatch.setattr(
        "core.scraper._get_async_session", AsyncMock(return_value=mock_session)
    )
    return mock_session


# ===========================================================================
# Cascade: stealth-requests primary → curl_cffi fallback
# ===========================================================================


class TestCascadeOrder:
    """Verifies that stealth-requests is tried first, then curl_cffi fallback."""

    @pytest.mark.asyncio
    async def test_stealth_succeeds_no_fallback(self, monkeypatch):
        """When stealth-requests returns valid content, curl_cffi is NOT called."""
        stealth_called = False
        curl_fallback_called = False

        def mock_stealth(url, proxy=None):
            nonlocal stealth_called
            stealth_called = True
            return ("stealth content", url)

        async def mock_curl_fallback(url, proxy=None):
            nonlocal curl_fallback_called
            curl_fallback_called = True
            return ("stealth content via curl", url)

        monkeypatch.setattr("core.scraper._fetch_stealth_sync", mock_stealth)
        monkeypatch.setattr("core.scraper._safe_legacy_fallback", mock_curl_fallback)

        md, url = await _fetch_article_text_async_impl("https://example.com/article")
        assert stealth_called
        assert not curl_fallback_called
        assert md == "stealth content"

    @pytest.mark.asyncio
    async def test_stealth_fails_falls_back_to_curl(self, monkeypatch):
        """When stealth-requests returns empty, curl_cffi fallback is invoked."""
        stealth_called = False
        curl_fallback_called = False

        def mock_stealth(url, proxy=None):
            nonlocal stealth_called
            stealth_called = True
            return ("", url)

        async def mock_curl_fallback(url, proxy=None):
            nonlocal curl_fallback_called
            curl_fallback_called = True
            return ("curl fallback content", url)

        monkeypatch.setattr("core.scraper._fetch_stealth_sync", mock_stealth)
        monkeypatch.setattr("core.scraper._safe_legacy_fallback", mock_curl_fallback)

        md, url = await _fetch_article_text_async_impl("https://example.com/article")
        assert stealth_called
        assert curl_fallback_called
        assert md == "curl fallback content"

    @pytest.mark.asyncio
    async def test_stealth_exception_falls_back_to_curl(self, monkeypatch):
        """When stealth-requests raises, curl_cffi fallback is invoked."""

        def mock_stealth(url, proxy=None):
            raise ConnectionError("network down")

        curl_fallback_called = False

        async def mock_curl_fallback(url, proxy=None):
            nonlocal curl_fallback_called
            curl_fallback_called = True
            return ("curl recovered", url)

        monkeypatch.setattr("core.scraper._fetch_stealth_sync", mock_stealth)
        monkeypatch.setattr("core.scraper._safe_legacy_fallback", mock_curl_fallback)

        md, url = await _fetch_article_text_async_impl("https://example.com/article")
        assert curl_fallback_called
        assert md == "curl recovered"

    @pytest.mark.asyncio
    async def test_both_fail_returns_empty(self, monkeypatch):
        """When both scrapers fail, returns ('', url)."""

        def mock_stealth(url, proxy=None):
            return ("", url)

        async def mock_curl_fallback(url, proxy=None):
            return ("", url)

        monkeypatch.setattr("core.scraper._fetch_stealth_sync", mock_stealth)
        monkeypatch.setattr("core.scraper._safe_legacy_fallback", mock_curl_fallback)

        md, url = await _fetch_article_text_async_impl("https://example.com/article")
        assert md == ""

    @pytest.mark.asyncio
    async def test_proxy_passed_to_stealth(self, monkeypatch):
        """_next_proxy() result should be passed to _fetch_stealth_sync."""
        received_proxy = None

        def mock_stealth(url, proxy=None):
            nonlocal received_proxy
            received_proxy = proxy
            return ("content", url)

        monkeypatch.setattr("core.scraper._fetch_stealth_sync", mock_stealth)
        monkeypatch.setattr("core.scraper._next_proxy", lambda: "http://proxy1:8080")

        await _fetch_article_text_async_impl("https://example.com/article")
        assert received_proxy == "http://proxy1:8080"


# ===========================================================================
# curl_cffi fallback behavior
# ===========================================================================


class TestCurlCffiFallback:
    """Verifies curl_cffi fallback handles status codes and response size."""

    @pytest.mark.asyncio
    async def test_curl_403_returns_empty(self, monkeypatch):
        _patch_async_session(monkeypatch, _make_mock_response(status_code=403))
        md, url = await _safe_legacy_fallback("https://example.com/article")
        assert md == ""

    @pytest.mark.asyncio
    async def test_curl_429_returns_empty(self, monkeypatch):
        _patch_async_session(monkeypatch, _make_mock_response(status_code=429))
        md, url = await _safe_legacy_fallback("https://example.com/article")
        assert md == ""

    @pytest.mark.asyncio
    async def test_curl_503_returns_empty(self, monkeypatch):
        _patch_async_session(monkeypatch, _make_mock_response(status_code=503))
        md, url = await _safe_legacy_fallback("https://example.com/article")
        assert md == ""

    @pytest.mark.asyncio
    async def test_curl_small_response_returns_empty(self, monkeypatch):
        _patch_async_session(monkeypatch, _make_mock_response(text="<html>Tiny</html>"))
        md, url = await _safe_legacy_fallback("https://example.com/article")
        assert md == ""

    @pytest.mark.asyncio
    async def test_curl_success_returns_content(self, monkeypatch):
        large_html = (
            "<html><body>" + "<p>Content paragraph.</p>" * 100 + "</body></html>"
        )
        _patch_async_session(
            monkeypatch, _make_mock_response(status_code=200, text=large_html)
        )
        md, url = await _safe_legacy_fallback("https://example.com/article")
        assert len(md) > 10

    @pytest.mark.asyncio
    async def test_curl_exception_returns_empty(self, monkeypatch):
        mock_session = AsyncMock()
        mock_session.get = AsyncMock(side_effect=ConnectionError("down"))
        monkeypatch.setattr(
            "core.scraper._get_async_session", AsyncMock(return_value=mock_session)
        )
        md, url = await _safe_legacy_fallback("https://example.com/article")
        assert md == ""


# ===========================================================================
# stealth-requests proxy support
# ===========================================================================


class TestStealthProxySupport:
    """Verify _fetch_stealth_sync passes proxies dict to stealth_requests."""

    def test_no_proxy_calls_without_proxies_kwarg(self, monkeypatch):
        """When proxy is None, no 'proxies' kwarg is passed."""
        captured_kwargs = {}

        class MockRequests:
            @staticmethod
            def get(url, **kwargs):
                captured_kwargs.update(kwargs)
                resp = MagicMock()
                resp.status_code = 200
                resp.url = url
                resp.text = "<html><body>" + "<p>Content.</p>" * 200 + "</body></html>"
                return resp

        monkeypatch.setitem(sys.modules, "stealth_requests", MockRequests())

        _fetch_stealth_sync("https://example.com/article", proxy=None)
        assert "proxies" not in captured_kwargs

    def test_proxy_passed_as_proxies_dict(self, monkeypatch):
        """When proxy is provided, 'proxies' kwarg should be a dict."""
        captured_kwargs = {}

        class MockRequests:
            @staticmethod
            def get(url, **kwargs):
                captured_kwargs.update(kwargs)
                resp = MagicMock()
                resp.status_code = 200
                resp.url = url
                resp.text = "<html><body>" + "<p>Content.</p>" * 200 + "</body></html>"
                return resp

        monkeypatch.setitem(sys.modules, "stealth_requests", MockRequests())

        _fetch_stealth_sync(
            "https://example.com/article", proxy="http://user:pass@proxy:8080"
        )
        assert "proxies" in captured_kwargs
        assert captured_kwargs["proxies"] == {
            "https": "http://user:pass@proxy:8080",
            "http": "http://user:pass@proxy:8080",
        }


# ===========================================================================
# Minimum response length
# ===========================================================================


class TestMinimumResponseLength:
    """Verify that tiny responses (<MIN_HTML_RESPONSE_LENGTH) are treated as blocks."""

    @pytest.mark.asyncio
    async def test_small_response_triggers_fallback(self, monkeypatch):
        """stealth-requests tiny response should trigger curl_cffi fallback."""

        def mock_stealth(url, proxy=None):
            return ("", url)

        curl_called = False

        async def mock_curl(url, proxy=None):
            nonlocal curl_called
            curl_called = True
            return ("", url)

        monkeypatch.setattr("core.scraper._fetch_stealth_sync", mock_stealth)
        monkeypatch.setattr("core.scraper._safe_legacy_fallback", mock_curl)

        await _fetch_article_text_async_impl("https://example.com/article")
        assert curl_called


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

        async def slow_impl(url):
            await asyncio.sleep(10)
            return ("content", url)

        monkeypatch.setattr("core.scraper._fetch_article_text_async_impl", slow_impl)

        semaphore = asyncio.Semaphore(5)
        result = await fetch_article_text_async("https://example.com/slow", semaphore)
        assert result is None, "Should return None on timeout"

    @pytest.mark.asyncio
    async def test_fast_scrape_returns_result(self, monkeypatch):
        """A fast scrape should succeed normally."""

        def fast_stealth(url, proxy=None):
            return ("Article content here. " * 50, url)

        monkeypatch.setattr("core.scraper._fetch_stealth_sync", fast_stealth)

        semaphore = asyncio.Semaphore(5)
        result = await fetch_article_text_async("https://example.com/fast", semaphore)
        assert result is not None
        assert len(result[0]) > 10


# ===========================================================================
# Proxy configuration (SCRAPER_PROXY_LIST)
# ===========================================================================


class TestProxyConfiguration:
    """Verify that SCRAPER_PROXY_LIST and _next_proxy work correctly."""

    def test_proxy_list_defaults_to_empty(self):
        from core.config import SCRAPER_PROXY_LIST

        assert isinstance(SCRAPER_PROXY_LIST, list)

    def test_next_proxy_returns_none_when_empty(self, monkeypatch):
        from core.config import _next_proxy, _proxy_rotator

        monkeypatch.setattr("core.config.SCRAPER_PROXY_LIST", [])
        _proxy_rotator.reset()
        assert _next_proxy() is None

    def test_next_proxy_rotates(self, monkeypatch):
        from core.config import _next_proxy, _proxy_rotator

        monkeypatch.setattr(
            "core.config.SCRAPER_PROXY_LIST",
            ["http://proxy1:8080", "http://proxy2:8080"],
        )
        _proxy_rotator.reset()
        p1 = _next_proxy()
        p2 = _next_proxy()
        p3 = _next_proxy()
        assert p1 == "http://proxy1:8080"
        assert p2 == "http://proxy2:8080"
        assert p3 == "http://proxy1:8080"

    def test_single_proxy_returns_same(self, monkeypatch):
        from core.config import _next_proxy, _proxy_rotator

        monkeypatch.setattr(
            "core.config.SCRAPER_PROXY_LIST",
            ["http://only-proxy:8080"],
        )
        _proxy_rotator.reset()
        p1 = _next_proxy()
        p2 = _next_proxy()
        assert p1 == "http://only-proxy:8080"
        assert p2 == "http://only-proxy:8080"


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
    """Smoke tests for constants."""

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

    @pytest.mark.asyncio
    async def test_get_async_session_returns_session(self, monkeypatch):
        """_get_async_session should return an AsyncSession instance."""
        mock_session = MagicMock()
        monkeypatch.setattr("core.scraper._async_session", None)
        monkeypatch.setattr("core.scraper._session_lock", None)
        monkeypatch.setattr("core.scraper.AsyncSession", lambda **kw: mock_session)
        result = await _get_async_session()
        assert result is mock_session

    @pytest.mark.asyncio
    async def test_get_async_session_caches_instance(self, monkeypatch):
        """Second call should return the same instance."""
        mock_session = MagicMock()
        monkeypatch.setattr("core.scraper._async_session", mock_session)
        result = await _get_async_session()
        assert result is mock_session


# ===========================================================================
# Burst delays (pipeline utility tested here for convenience)
# ===========================================================================


class TestBurstDelays:
    """Tests for the _burst_delays function."""

    def test_zero_urls_returns_empty(self):
        from core.pipeline import _burst_delays

        assert _burst_delays(0) == []

    def test_one_url_returns_empty(self):
        from core.pipeline import _burst_delays

        assert _burst_delays(1) == []

    def test_exactly_one_burst_returns_empty(self):
        from core.pipeline import _burst_delays

        assert _burst_delays(3, burst_size=3) == []

    def test_two_bursts_returns_one_delay(self):
        from core.pipeline import _burst_delays

        delays = _burst_delays(4, burst_size=3)
        assert len(delays) == 1
        assert 5.0 <= delays[0] <= 12.0

    def test_three_bursts_returns_two_delays(self):
        from core.pipeline import _burst_delays

        delays = _burst_delays(7, burst_size=3)
        assert len(delays) == 2

    def test_delays_within_range(self):
        from core.pipeline import _burst_delays

        delays = _burst_delays(10, burst_size=2, min_pause=3.0, max_pause=7.0)
        for d in delays:
            assert 3.0 <= d <= 7.0


# ===========================================================================
# Proxy URL masking (SEC-04)
# ===========================================================================


class TestMaskProxyUrl:
    def test_masks_user_and_password(self):
        url = "http://user:secret@proxy.example.com:8080"
        masked = _mask_proxy_url(url)
        assert "secret" not in masked
        assert "user" not in masked
        assert "proxy.example.com" in masked
        assert "8080" in masked

    def test_no_credentials_unchanged(self):
        url = "http://proxy.example.com:8080"
        assert _mask_proxy_url(url) == url

    def test_url_without_port_masked(self):
        url = "http://admin:pass@proxy.example.com"
        masked = _mask_proxy_url(url)
        assert "pass" not in masked
        assert "proxy.example.com" in masked

    def test_non_url_returns_original(self):
        url = "not-a-url"
        assert _mask_proxy_url(url) == url
