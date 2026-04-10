"""Tests for email/IMAP operations, Kindle delivery, and the main orchestrator."""

import asyncio
import datetime
import json
import os
from dataclasses import asdict
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.config import CacheDocument, EnrichedNews
from core.email_client import _cleanup_emails_sync, cleanup_emails, send_to_kindle
from core.pipeline import extract_all_content_async, process_newsletters
from core.scraper import _fetch_article_text_async_impl, fetch_article_text_async

# ===========================================================================
# send_to_kindle
# ===========================================================================


class TestSendToKindle:
    @pytest.mark.asyncio
    async def test_successful_send(self, monkeypatch):
        monkeypatch.setattr("core.config.EMAIL_ACCOUNT", "test@example.com")
        monkeypatch.setattr("core.config.EMAIL_PASSWORD", "password")
        monkeypatch.setattr("core.config.KINDLE_EMAIL", "kindle@example.com")
        monkeypatch.setattr("core.config.SMTP_SERVER", "smtp.gmail.com")

        with patch(
            "core.email_client.aiosmtplib.send", new_callable=AsyncMock
        ) as mock_send:
            result = await send_to_kindle("<html>Summary</html>", "2026/03/11")
            assert result is True
            mock_send.assert_called_once()
            call_kwargs = mock_send.call_args
            assert call_kwargs.kwargs["hostname"] == "smtp.gmail.com"
            assert call_kwargs.kwargs["port"] == 465

    @pytest.mark.asyncio
    async def test_returns_false_on_smtp_error(self, monkeypatch):
        monkeypatch.setattr("core.config.EMAIL_ACCOUNT", "test@example.com")
        monkeypatch.setattr("core.config.EMAIL_PASSWORD", "password")
        monkeypatch.setattr("core.config.KINDLE_EMAIL", "kindle@example.com")
        monkeypatch.setattr("core.config.SMTP_SERVER", "smtp.gmail.com")

        import aiosmtplib

        with patch(
            "core.email_client.aiosmtplib.send", new_callable=AsyncMock
        ) as mock_send:
            mock_send.side_effect = aiosmtplib.SMTPException("Connection refused")
            result = await send_to_kindle("<html>Test</html>", "2026/03/11")
            assert result is False


# ===========================================================================
# cleanup_emails
# ===========================================================================


class TestCleanupEmails:
    @pytest.mark.asyncio
    async def test_empty_list_is_noop(self):
        """Calling cleanup with empty list should not raise."""
        await cleanup_emails([])

    @pytest.mark.asyncio
    async def test_calls_delete_and_expunge(self, monkeypatch):
        """Verifies the sync cleanup function calls delete and expunge."""
        mock_mailbox_ctx = MagicMock()
        mock_mailbox_ctx.client = MagicMock()

        with (
            patch("core.email_client.MailBox") as MockMailBox,
            patch("core.email_client.clean_uids", return_value="uid1,uid2"),
        ):
            mock_login = MagicMock()
            mock_login.__enter__ = MagicMock(return_value=mock_mailbox_ctx)
            mock_login.__exit__ = MagicMock(return_value=False)
            MockMailBox.return_value.login.return_value = mock_login

            _cleanup_emails_sync(["uid1", "uid2"])
            mock_mailbox_ctx.delete.assert_called_once_with(["uid1", "uid2"])
            mock_mailbox_ctx.client.expunge.assert_called_once()


# ===========================================================================
# extract_all_content_async
# ===========================================================================


class TestExtractAllContentAsync:
    @pytest.mark.asyncio
    async def test_returns_empty_on_imap_failure(self, monkeypatch):
        def mock_fetch_sync():
            raise ConnectionError("IMAP server down")

        monkeypatch.setattr("core.pipeline._fetch_emails_sync", mock_fetch_sync)

        cache, processed = await extract_all_content_async()
        assert cache == []
        assert processed == []

    @pytest.mark.asyncio
    async def test_fetches_external_links_concurrently(self, monkeypatch):
        """Should create tasks for external URLs and merge results."""
        docs = [CacheDocument(id="doc_1", source="newsletter", text="Newsletter text")]
        urls = ["https://nytimes.com/article1", "https://nytimes.com/article2"]

        def mock_fetch_sync():
            return docs, ["uid_1"], urls, set()

        monkeypatch.setattr("core.pipeline._fetch_emails_sync", mock_fetch_sync)

        async def mock_fetch_article(url, semaphore):
            return (f"Content of {url} " * 20, url)  # > 100 chars

        monkeypatch.setattr(
            "core.pipeline.fetch_article_text_async", mock_fetch_article
        )

        cache, processed = await extract_all_content_async()
        assert len(cache) == 3
        assert len(processed) == 1


# ===========================================================================
# process_newsletters (main orchestrator)
# ===========================================================================


class TestProcessNewsletters:
    @pytest.mark.asyncio
    async def test_aborts_on_empty_cache(self, monkeypatch):
        monkeypatch.setattr("core.config.EMAIL_ACCOUNT", "test@example.com")
        monkeypatch.setattr("core.config.EMAIL_PASSWORD", "password")
        monkeypatch.setattr("core.config.KINDLE_EMAIL", "kindle@example.com")

        monkeypatch.setattr(os.path, "exists", lambda p: False)

        async def mock_extract():
            return [], []

        monkeypatch.setattr("core.pipeline.extract_all_content_async", mock_extract)

        await process_newsletters()

    @pytest.mark.asyncio
    async def test_loads_local_cache_when_valid(self, monkeypatch, tmp_path):
        monkeypatch.setattr("core.config.EMAIL_ACCOUNT", "test@example.com")
        monkeypatch.setattr("core.config.EMAIL_PASSWORD", "password")
        monkeypatch.setattr("core.config.KINDLE_EMAIL", "kindle@example.com")

        cache_file = tmp_path / "cache_nytt_latest.json"
        cache_data = [
            asdict(CacheDocument(id="doc_1", source="newsletter", text="Test"))
        ]
        cache_file.write_text(json.dumps(cache_data))
        monkeypatch.setattr("core.config.CACHE_FILE", str(cache_file))

        today = datetime.date.today()
        mock_mod_time = datetime.datetime.combine(
            today, datetime.time(10, 0)
        ).timestamp()
        monkeypatch.setattr(os.path, "getmtime", lambda p: mock_mod_time)

        async def mock_categorize(cache):
            return {
                "grouped_news": [
                    {"title": "Test", "level": "principal", "cache_ids": ["doc_1"]}
                ]
            }

        monkeypatch.setattr("core.pipeline.categorize_news", mock_categorize)

        def mock_match(groups, cache):
            return [
                EnrichedNews(
                    title="Test", level="principal", content="Test", anchor_id="n-0"
                )
            ]

        monkeypatch.setattr("core.pipeline.match_cache_to_groups", mock_match)

        async def mock_summarize(enriched):
            return "<html><body>Summary</body></html>"

        monkeypatch.setattr("core.pipeline.summarize_text", mock_summarize)

        async def mock_send(text, date_str):
            return True

        monkeypatch.setattr("core.pipeline.send_to_kindle", mock_send)

        await process_newsletters()


# ===========================================================================
# GenAI client singleton
# ===========================================================================


class TestGetGenaiClient:
    def test_returns_none_without_key(self, monkeypatch):
        from core.gemini import _get_genai_client

        monkeypatch.setattr("core.gemini._genai_client", None)
        monkeypatch.setattr("core.gemini.GOOGLE_API_KEY", None)
        assert _get_genai_client() is None

    def test_caches_client(self, monkeypatch):
        from core.gemini import _get_genai_client

        monkeypatch.setattr("core.gemini._genai_client", None)
        monkeypatch.setattr("core.config.GOOGLE_API_KEY", "fake-key")
        with patch("core.gemini.genai.Client") as MockClient:
            client1 = _get_genai_client()
            client2 = _get_genai_client()
            assert client1 is client2
            MockClient.assert_called_once()  # Only created once

    def test_returns_existing_client(self, monkeypatch):
        from core.gemini import _get_genai_client

        sentinel = MagicMock()
        monkeypatch.setattr("core.gemini._genai_client", sentinel)
        assert _get_genai_client() is sentinel


# ===========================================================================
# fetch_article_text_async
# ===========================================================================


class TestFetchArticleTextAsync:
    @pytest.mark.asyncio
    async def test_returns_empty_tuple_on_all_failures(self, monkeypatch):
        """When both stealth-requests and curl_cffi fail, should return ('', url)."""

        def mock_stealth_fail(url, proxy=None):
            return ("", url)

        mock_session = AsyncMock()
        mock_resp = AsyncMock()
        mock_resp.status_code = 403
        mock_resp.url = "https://example.com"
        mock_resp.text = "Blocked"
        mock_session.get = AsyncMock(return_value=mock_resp)
        monkeypatch.setattr("core.scraper._fetch_stealth_sync", mock_stealth_fail)
        monkeypatch.setattr(
            "core.scraper._get_async_session", AsyncMock(return_value=mock_session)
        )

        semaphore = asyncio.Semaphore(5)
        result = await fetch_article_text_async("https://example.com", semaphore)
        assert result == ("", "https://example.com")

    @pytest.mark.asyncio
    async def test_returns_result_on_success(self, monkeypatch):
        """When stealth-requests returns valid content, should return it directly."""

        def mock_stealth_success(url, proxy=None):
            return ("Article content here. " * 50, url)

        monkeypatch.setattr("core.scraper._fetch_stealth_sync", mock_stealth_success)

        semaphore = asyncio.Semaphore(5)
        result = await fetch_article_text_async(
            "https://example.com/article", semaphore
        )
        assert result is not None
        assert len(result[0]) > 10

    @pytest.mark.asyncio
    async def test_respects_semaphore_concurrency(self, monkeypatch):
        """Semaphore should limit concurrent operations."""

        def mock_stealth_success(url, proxy=None):
            return ("Content paragraph. " * 50, url)

        monkeypatch.setattr("core.scraper._fetch_stealth_sync", mock_stealth_success)

        entry_count = 0
        max_concurrent = 0

        original_impl = _fetch_article_text_async_impl

        async def counting_impl(url):
            nonlocal entry_count, max_concurrent
            entry_count += 1
            max_concurrent = max(max_concurrent, entry_count)
            try:
                return await original_impl(url)
            finally:
                entry_count -= 1

        monkeypatch.setattr(
            "core.pipeline.fetch_article_text_async",
            lambda url, sem: counting_impl(url),
        )

        semaphore = asyncio.Semaphore(2)
        tasks = [
            fetch_article_text_async(f"https://example.com/{i}", semaphore)
            for i in range(5)
        ]
        await asyncio.gather(*tasks)
        assert max_concurrent <= 2, (
            f"Max concurrency was {max_concurrent}, expected <= 2"
        )
