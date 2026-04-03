"""HTML parsing and text extraction for newsletters."""

import logging

import markdownify
from bs4 import BeautifulSoup

from core.config import (
    _ADMIN_KEYWORDS,
    _DENY_DOMAINS,
    _DENY_PATHS,
    _EXACT_IGNORED_TEXTS,
    _EXCESS_NEWLINE_RE,
    _IGNORED_AUTHORS,
    _MARKDOWNIFY_TAGS,
    _MIN_SECTION_LENGTH,
    _NOISE_LINE_RE,
    _NOISE_PHRASE_RE,
    _SECTION_SPLIT_RE,
    _TABLE_TAG_RE,
    _is_admin_text,
    _strip_query,
)

logger = logging.getLogger(__name__)


def extract_text_from_html(
    html_content: str | None, seen_urls: set[str] | None = None
) -> tuple[str, list[str]]:
    """Extracts visible text from HTML and collects linked article URLs."""
    if not html_content:
        return "", []

    soup = BeautifulSoup(html_content, "lxml")

    for tag in soup(["script", "style", "footer", "nav", "header"]):
        tag.extract()

    urls_to_fetch: list[str] = []
    if seen_urls is None:
        seen_urls = set()

    for a_tag in soup.find_all("a", href=True):
        link_text = a_tag.get_text(strip=True).lower()
        url = a_tag["href"]

        if not link_text:
            continue

        cleaned_text = link_text.replace("→", "").strip()

        if not url.startswith("http") and not url.startswith("mailto"):
            continue

        if url.startswith("mailto:") or cleaned_text in _EXACT_IGNORED_TEXTS:
            continue

        if (
            cleaned_text.startswith("@")
            or cleaned_text.startswith("by ")
            or cleaned_text in _IGNORED_AUTHORS
        ):
            continue

        if cleaned_text.endswith("for the new york times"):
            continue

        base_url = _strip_query(url)
        if base_url in seen_urls:
            continue

        is_valid_news = False
        is_read_link = (
            "read now" in link_text
            or "read more" in link_text
            or "read about" in link_text
            or "read the" in link_text
            or link_text.startswith("leia ")
            or link_text.startswith("read ")
        )

        if any(d in url for d in _DENY_DOMAINS) or any(p in url for p in _DENY_PATHS):
            continue

        if "nl.nytimes.com/f/" in url:
            if "/f/a/" in url:
                is_valid_news = is_read_link
            else:
                if (
                    is_read_link
                    or len(cleaned_text) > 30
                    and not _is_admin_text(cleaned_text, _ADMIN_KEYWORDS)
                ):
                    is_valid_news = True
        else:
            is_nyt_link = "nytimes.com" in url
            is_rich_external = len(cleaned_text) > 40 and url.startswith("http")
            is_partner_domain = "theathletic.com" in url

            if is_nyt_link and not url.startswith("https://nl.nytimes.com/"):
                continue

            if (
                is_read_link
                or is_rich_external
                and not _is_admin_text(cleaned_text, _ADMIN_KEYWORDS)
                or (
                    is_partner_domain
                    and len(cleaned_text) > 20
                    and not _is_admin_text(cleaned_text, _ADMIN_KEYWORDS)
                )
            ):
                is_valid_news = True

        if is_valid_news:
            seen_urls.add(base_url)
            logger.info("Queuing embedded link: %s -> %s…", link_text[:30], url[:50])
            urls_to_fetch.append(url)

    html_content_flat = _TABLE_TAG_RE.sub("\n", str(soup.body or soup))

    text_md = markdownify.markdownify(
        html_content_flat,
        convert=_MARKDOWNIFY_TAGS,
        heading_style="ATX",
    )

    lines = (line.strip() for line in text_md.splitlines())
    text = "\n".join(line for line in lines if line)
    text = _NOISE_PHRASE_RE.sub("", text)
    text = _NOISE_LINE_RE.sub("", text)
    text = _EXCESS_NEWLINE_RE.sub("\n\n", text)

    return text, urls_to_fetch


def _split_newsletter_sections(text_md: str) -> list[str]:
    """Splits a multi-topic newsletter into individual section strings."""
    raw_parts = _SECTION_SPLIT_RE.split(text_md)

    raw_parts = [p.strip() for p in raw_parts if p and p.strip()]

    if not raw_parts:
        return [text_md] if text_md.strip() else []

    merged: list[str] = []
    for part in raw_parts:
        if len(part) < _MIN_SECTION_LENGTH:
            if merged:
                merged[-1] = merged[-1] + "\n\n" + part
            else:
                merged.append(part)
        else:
            if merged and len(merged[-1]) < _MIN_SECTION_LENGTH:
                merged[-1] = merged[-1] + "\n\n" + part
            else:
                merged.append(part)

    return merged
