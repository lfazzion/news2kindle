"""Pacote newsletter — pipeline de processamento de newsletters."""

from core.config import CacheDocument, EnrichedNews
from core.email_client import _fetch_emails_sync, cleanup_emails, send_to_kindle
from core.extractor import extract_text_from_html
from core.gemini import _generate_content_async, _get_genai_client
from core.pipeline import (
    categorize_news,
    extract_all_content_async,
    match_cache_to_groups,
    process_newsletters,
    summarize_text,
)
from core.scraper import fetch_article_text_async

__all__ = [
    "CacheDocument",
    "EnrichedNews",
    "fetch_article_text_async",
    "extract_text_from_html",
    "_get_genai_client",
    "_generate_content_async",
    "_fetch_emails_sync",
    "send_to_kindle",
    "cleanup_emails",
    "categorize_news",
    "match_cache_to_groups",
    "summarize_text",
    "extract_all_content_async",
    "process_newsletters",
]
