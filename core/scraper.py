"""HTTP scraping: stealth-requests primary + curl_cffi fallback."""

import asyncio
import logging
from urllib.parse import urlparse, urlunparse

import markdownify
import trafilatura
from curl_cffi.requests import AsyncSession

from core.config import (
    _DENY_DOMAINS,
    _MARKDOWNIFY_TAGS,
    HTTP_BLOCK_STATUS_CODES,
    MIN_HTML_RESPONSE_LENGTH,
    MIN_JSON_EXTRACT_LENGTH,
    SCRAPER_PROXY_LIST,
    SCRAPER_URL_TIMEOUT,
    _extract_preloaded_json,
    _is_blocked_content,
    _next_proxy,
    _strip_query,
)

logger = logging.getLogger(__name__)


def _mask_proxy_url(url: str) -> str:
    """Mascara user:pass de URLs de proxy antes de logar."""
    try:
        parsed = urlparse(url)
        if parsed.username or parsed.password:
            netloc = parsed.hostname or ""
            if parsed.port:
                netloc += f":{parsed.port}"
            masked = parsed._replace(netloc=f"***:***@{netloc}")
            return urlunparse(masked)
    except Exception:
        pass
    return url


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_BROWSER_HEADERS: dict[str, str] = {
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,image/apng,*/*;q=0.8"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
    "Referer": "https://www.google.com/",
}

# ---------------------------------------------------------------------------
# Async session singleton (curl_cffi fallback)
# ---------------------------------------------------------------------------

_async_session: AsyncSession | None = None
_session_lock: asyncio.Lock = asyncio.Lock()


async def _get_async_session() -> AsyncSession:
    """Lazy init thread-safe com double-checked locking (BUG-03)."""
    global _async_session
    if _async_session is None:
        async with _session_lock:
            if _async_session is None:
                _async_session = AsyncSession(
                    impersonate="chrome",
                    headers=_BROWSER_HEADERS,
                )
                logger.debug("curl_cffi AsyncSession created (no proxy on session).")
    return _async_session


async def _reset_async_session() -> None:
    """Closes the current AsyncSession so a fresh one is created on next use."""
    global _async_session
    if _async_session:
        import contextlib

        with contextlib.suppress(Exception):
            await _async_session.close()
    _async_session = None


# ---------------------------------------------------------------------------
# Metadata fallback
# ---------------------------------------------------------------------------


def _extract_metadata_fallback(html: str, url: str) -> tuple[str, str]:
    """Last-resort fallback: extracts title + description from meta tags.

    Returns (text, url) where text is the combined title + description,
    or empty string if neither is found.
    """
    try:
        metadata = trafilatura.extract_metadata(html, default_url=url)
    except Exception:
        logger.debug("trafilatura.extract_metadata failed for %s.", url)
        return "", url
    if metadata:
        parts: list[str] = []
        if metadata.title:
            parts.append(metadata.title.strip())
        if metadata.description:
            parts.append(metadata.description.strip())
        text = "\n\n".join(parts)
        if text and not _is_blocked_content(text):
            return text, url
    return "", url


# ---------------------------------------------------------------------------
# HTML to markdown parsing
# ---------------------------------------------------------------------------


def _parse_html_to_markdown(html: str, target_url: str) -> tuple[str, str]:
    """Parses an HTML string into clean markdown text.

    Priority order:
    1. window.__preloadedData / __NEXT_DATA__ / __PRELOADED_STATE__ JSON
    2. div.StoryBodyCompanionColumn (NYT-specific, most granular)
    3. trafilatura.extract() — structural content extraction
    4. trafilatura metadata fallback (title + og:description)
    """
    # 1. Try extracting from JS state variables
    data = _extract_preloaded_json(html)
    if data is not None:
        texts: list[str] = []
        stack: list[dict | list] = [data]
        while stack:
            current = stack.pop()
            if isinstance(current, dict):
                for k, v in current.items():
                    if (
                        k == "text"
                        and isinstance(v, str)
                        and len(v) > 50
                        and "<" not in v
                    ):
                        texts.append(v)
                    elif isinstance(v, (dict, list)):
                        stack.append(v)
            elif isinstance(current, list):
                stack.extend(item for item in current if isinstance(item, (dict, list)))

        text = "\n".join(texts)
        if len(text) > MIN_JSON_EXTRACT_LENGTH:
            if _is_blocked_content(text):
                logger.debug("Bypass hit anti-bot/error wall via JSON.")
                return "", target_url
            logger.debug("Paywall bypass via JSON succeeded (%d chars).", len(text))
            return text, target_url
    else:
        logger.debug(
            "No preloaded JS data found "
            "(__preloadedData/__NEXT_DATA__/__PRELOADED_STATE__) in %s.",
            target_url,
        )

    # 2. NYT-specific: StoryBodyCompanionColumn
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "lxml")
    companion_cols = soup.find_all("div", class_="StoryBodyCompanionColumn")
    if companion_cols:
        html_fragment = "".join(str(col) for col in companion_cols)
        md_text = markdownify.markdownify(
            html_fragment,
            convert=_MARKDOWNIFY_TAGS,
            heading_style="ATX",
        )
        if md_text and md_text.strip() and not _is_blocked_content(md_text):
            logger.debug(
                "StoryBodyCompanionColumn extraction succeeded for %s.", target_url
            )
            return md_text, target_url

    # 3. trafilatura: structural extraction
    traf_text = trafilatura.extract(
        html,
        url=target_url,
        output_format="markdown",
        include_links=True,
        include_formatting=True,
        include_images=False,
        favor_recall=True,
        no_fallback=False,
    )
    if traf_text and not _is_blocked_content(traf_text):
        logger.debug(
            "trafilatura extraction succeeded (%d chars) for %s.",
            len(traf_text),
            target_url,
        )
        return traf_text, target_url

    # 4. Last resort: trafilatura metadata
    logger.debug(
        "trafilatura found no content for %s. Trying metadata fallback.", target_url
    )
    return _extract_metadata_fallback(html, target_url)


# ---------------------------------------------------------------------------
# Primary scraper: stealth-requests (sync, in thread)
# ---------------------------------------------------------------------------


def _fetch_stealth_sync(url: str, proxy: str | None = None) -> tuple[str, str]:
    """Primary scraper: stealth_requests with UA rotation and optional proxy."""
    import stealth_requests as requests

    kwargs: dict = {"timeout": SCRAPER_URL_TIMEOUT}
    if proxy:
        kwargs["proxies"] = {"https": proxy, "http": proxy}

    resp = requests.get(url, **kwargs)
    resolved_url = _strip_query(resp.url)
    html = resp.text

    if resp.status_code in HTTP_BLOCK_STATUS_CODES:
        logger.warning(
            "stealth-requests got HTTP %d for %s", resp.status_code, url[:60]
        )
        return "", resolved_url

    if len(html) < MIN_HTML_RESPONSE_LENGTH:
        logger.warning("stealth-requests response too small for %s", url[:60])
        return "", resolved_url

    md_text, resolved_url = _parse_html_to_markdown(html, resolved_url)
    if not md_text:
        return "", resolved_url

    logger.info("stealth-requests succeeded for %s", url[:60])
    return md_text, resolved_url


# ---------------------------------------------------------------------------
# Fallback: curl_cffi async
# ---------------------------------------------------------------------------


async def _safe_legacy_fallback(
    url: str,
    proxy: str | None = None,
) -> tuple[str, str]:
    """Fallback: curl_cffi AsyncSession with per-request proxy rotation."""
    session = await _get_async_session()
    try:
        req = await session.get(url, proxy=proxy, timeout=SCRAPER_URL_TIMEOUT)
        resolved_url = _strip_query(req.url)
        html = req.text

        if any(d in resolved_url for d in _DENY_DOMAINS):
            return "", resolved_url

        if req.status_code in HTTP_BLOCK_STATUS_CODES:
            logger.warning(
                "curl_cffi fallback got HTTP %d for %s",
                req.status_code,
                resolved_url,
            )
            return "", resolved_url

        if len(html) < MIN_HTML_RESPONSE_LENGTH:
            logger.warning("curl_cffi fallback response too small for %s", resolved_url)
            return "", resolved_url

        md_text, resolved_url = _parse_html_to_markdown(html, resolved_url)
        if not md_text:
            return "", resolved_url

        logger.info("curl_cffi fallback succeeded for %s", url[:60])
        return md_text, resolved_url
    except Exception as e:
        safe_msg = str(e)
        for known_proxy in SCRAPER_PROXY_LIST:
            safe_msg = safe_msg.replace(known_proxy, _mask_proxy_url(known_proxy))
        logger.warning("curl_cffi fallback failed for %s: %s", url[:60], safe_msg)
        return "", url


# ---------------------------------------------------------------------------
# Main cascade: stealth-requests → curl_cffi
# ---------------------------------------------------------------------------


async def _fetch_article_text_async_impl(url: str) -> tuple[str, str]:
    """Primary: stealth-requests (sync, in thread) with curl_cffi fallback."""
    proxy = _next_proxy()

    # Step 1: stealth-requests (primary, sync in thread)
    try:
        result = await asyncio.to_thread(_fetch_stealth_sync, url, proxy)
        if result[0]:
            return result
    except Exception as e:
        logger.warning(
            "stealth-requests error on %s: %s — trying curl_cffi fallback...",
            url[:60],
            e,
        )

    # Step 2: curl_cffi (fallback, async) — proxy rotativo por request
    return await _safe_legacy_fallback(url, proxy)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def fetch_article_text_async(
    url: str,
    semaphore: asyncio.Semaphore,
) -> tuple[str, str] | None:
    """Fetches an article URL using stealth-requests (primary) with curl_cffi fallback.

    Enforces a per-URL timeout (SCRAPER_URL_TIMEOUT) to prevent a single slow
    URL from holding a semaphore slot indefinitely.
    """
    async with semaphore:
        try:
            return await asyncio.wait_for(
                _fetch_article_text_async_impl(url),
                timeout=SCRAPER_URL_TIMEOUT,
            )
        except TimeoutError:
            logger.error(
                "Scraping timed out after %.0fs for %s", SCRAPER_URL_TIMEOUT, url[:80]
            )
            return None
        except Exception as exc:
            logger.error("Scraping falhou para %s. Falha mapeada: %s", url[:50], exc)
            return None
