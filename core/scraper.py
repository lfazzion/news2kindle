"""HTTP scraping: curl_cffi AsyncSession primary + CloudScraper fallback."""

import asyncio
import json
import logging
import threading as _threading

import cloudscraper
import markdownify
import trafilatura
from curl_cffi.requests import AsyncSession

from core.config import (
    _DENY_DOMAINS,
    _MARKDOWNIFY_TAGS,
    _PRELOADED_JSON_RE,
    HTTP_BLOCK_STATUS_CODES,
    MIN_HTML_RESPONSE_LENGTH,
    MIN_JSON_EXTRACT_LENGTH,
    SCRAPER_PROXY,
    SCRAPER_URL_TIMEOUT,
    _is_blocked_content,
    _strip_query,
)

logger = logging.getLogger(__name__)

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
# Async session singleton
# ---------------------------------------------------------------------------

_async_session: AsyncSession | None = None


def _get_async_session() -> AsyncSession:
    """Returns a lazily-initialised AsyncSession with Chrome TLS impersonation."""
    global _async_session
    if _async_session is None:
        _async_session = AsyncSession(
            impersonate="chrome",
            proxy=SCRAPER_PROXY,
            headers=_BROWSER_HEADERS,
        )
        if SCRAPER_PROXY:
            logger.info("Scraper proxy configured: %s…", SCRAPER_PROXY[:30])
        else:
            logger.info("No scraper proxy configured — using direct datacenter IP.")
    return _async_session


async def _reset_async_session() -> None:
    """Closes the current AsyncSession and creates a fresh one."""
    global _async_session
    if _async_session:
        import contextlib

        with contextlib.suppress(Exception):
            await _async_session.close()
    _async_session = None
    _get_async_session()


# ---------------------------------------------------------------------------
# Legacy CloudScraper session
# ---------------------------------------------------------------------------

_legacy_thread_local = _threading.local()


def _get_legacy_scraper() -> cloudscraper.CloudScraper:
    """Returns a thread-local CloudScraper session (fallback only)."""
    if not hasattr(_legacy_thread_local, "scraper"):
        _legacy_thread_local.scraper = cloudscraper.create_scraper(
            browser={"browser": "chrome", "platform": "windows", "desktop": True}
        )
        if SCRAPER_PROXY:
            _legacy_thread_local.scraper.proxies = {
                "http": SCRAPER_PROXY,
                "https": SCRAPER_PROXY,
            }
    return _legacy_thread_local.scraper


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
    match = _PRELOADED_JSON_RE.search(html)
    if match:
        try:
            data = json.loads(match.group(1))
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
                    stack.extend(
                        item for item in current if isinstance(item, (dict, list))
                    )

            text = "\n".join(texts)
            if len(text) > MIN_JSON_EXTRACT_LENGTH:
                if _is_blocked_content(text):
                    logger.debug("Bypass hit anti-bot/error wall via JSON.")
                    return "", target_url
                logger.debug("Paywall bypass via JSON succeeded (%d chars).", len(text))
                return text, target_url
        except json.JSONDecodeError:
            logger.debug(
                "Failed to parse preloadedData JSON; falling back to HTML parsing."
            )
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
# Fallback cascade
# ---------------------------------------------------------------------------


async def _fetch_article_text_async_impl(url: str) -> tuple[str, str]:
    """Primary scraper: curl_cffi AsyncSession with Chrome TLS impersonation."""
    session = _get_async_session()
    try:
        req = await session.get(url, timeout=SCRAPER_URL_TIMEOUT)
        resolved_url = _strip_query(req.url)
        html = req.text

        if any(d in resolved_url for d in _DENY_DOMAINS):
            logger.debug(
                "Resolved URL %s is in _DENY_DOMAINS. Skipping.", resolved_url[:80]
            )
            return "", resolved_url

        if req.status_code in HTTP_BLOCK_STATUS_CODES:
            logger.warning(
                "HTTP %d on %s via curl_cffi. Engaging fallback cascade...",
                req.status_code,
                resolved_url,
            )
            return await _safe_legacy_fallback(url)

        if len(html) < MIN_HTML_RESPONSE_LENGTH:
            logger.warning(
                "curl_cffi response too small (%d bytes) for %s. "
                "Engaging fallback cascade...",
                len(html),
                resolved_url,
            )
            return await _safe_legacy_fallback(url)

        md_text, resolved_url = _parse_html_to_markdown(html, resolved_url)

        if not md_text:
            logger.warning(
                "curl_cffi parsing failed for %s. Engaging fallback cascade...",
                resolved_url,
            )
            return await _safe_legacy_fallback(url)

        return md_text, resolved_url

    except (TimeoutError, ConnectionError, OSError) as e:
        logger.warning(
            "curl_cffi error on %s: %s — trying CloudScraper fallback...", url[:60], e
        )
        return await _safe_legacy_fallback(url)
    except Exception as e:
        logger.warning(
            "curl_cffi unexpected error on %s: %s — trying CloudScraper fallback...",
            url[:60],
            e,
        )
        return await _safe_legacy_fallback(url)


async def _safe_legacy_fallback(url: str) -> tuple[str, str]:
    """Runs fallback cascade: CloudScraper → stealth-requests. Never raises."""
    try:
        result = await asyncio.to_thread(_fetch_legacy_sync, url)
        if result[0]:
            return result
    except Exception as e:
        logger.warning("CloudScraper fallback failed for %s: %s", url[:60], e)

    try:
        result = await asyncio.to_thread(_fetch_stealth_sync, url)
        if result[0]:
            return result
    except Exception as e:
        logger.warning("stealth-requests fallback failed for %s: %s", url[:60], e)

    logger.error("All scrapers failed for %s", url[:60])
    return "", url


def _fetch_stealth_sync(url: str) -> tuple[str, str]:
    """Tertiary fallback: stealth_requests with UA rotation and Referer tracking."""
    import stealth_requests as requests

    resp = requests.get(url, timeout=15.0)
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


def _fetch_legacy_sync(url: str) -> tuple[str, str]:
    """Legacy CloudScraper fallback. Used only when curl_cffi fails or is blocked."""
    scraper = _get_legacy_scraper()
    req = scraper.get(url, timeout=(4.0, 15.0))
    resolved_url = _strip_query(req.url)
    html = req.text

    if any(d in resolved_url for d in _DENY_DOMAINS):
        logger.debug(
            "Legacy: resolved URL %s is in _DENY_DOMAINS. Skipping.", resolved_url[:80]
        )
        return "", resolved_url

    if req.status_code in HTTP_BLOCK_STATUS_CODES:
        logger.error(
            "CloudScraper also got HTTP %d for %s", req.status_code, resolved_url
        )
        return "", resolved_url

    if len(html) < MIN_HTML_RESPONSE_LENGTH:
        logger.error(
            "CloudScraper response too small (%d bytes) for %s", len(html), resolved_url
        )
        return "", resolved_url

    md_text, resolved_url = _parse_html_to_markdown(html, resolved_url)

    if not md_text:
        logger.error("CloudScraper also failed to extract content for %s", resolved_url)
        return "", resolved_url

    logger.info("CloudScraper fallback succeeded for %s", url[:60])
    return md_text, resolved_url


async def fetch_article_text_async(
    url: str,
    semaphore: asyncio.Semaphore,
) -> tuple[str, str] | None:
    """Fetches an article URL using curl_cffi (primary) with CloudScraper fallback.

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
