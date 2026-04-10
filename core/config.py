"""Shared constants, dataclasses, and helper functions for the newsletter pipeline."""

import itertools
import json
import logging
import os
import re
from collections import OrderedDict
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Environment & configuration
# ---------------------------------------------------------------------------

EMAIL_ACCOUNT: str | None = os.environ.get("EMAIL_ACCOUNT")
EMAIL_PASSWORD: str | None = os.environ.get("EMAIL_PASSWORD")
IMAP_SERVER: str = os.environ.get("IMAP_SERVER") or "imap.gmail.com"
SMTP_SERVER: str = os.environ.get("SMTP_SERVER") or "smtp.gmail.com"


def _parse_smtp_port() -> int:
    """Parses SMTP_PORT env var with a descriptive error on invalid input."""
    raw = os.environ.get("SMTP_PORT", "465")
    try:
        port = int(raw)
    except ValueError:
        raise ValueError(f"SMTP_PORT must be a valid integer, got: {raw!r}") from None
    if not (1 <= port <= 65535):
        raise ValueError(f"SMTP_PORT must be between 1 and 65535, got: {port}")
    return port


SMTP_PORT: int = _parse_smtp_port()
KINDLE_EMAIL: str | None = os.environ.get("KINDLE_EMAIL")
GOOGLE_API_KEY: str | None = os.environ.get("GOOGLE_API_KEY")

_RAW_PROXY_LIST: str | None = os.environ.get("SCRAPER_PROXY_LIST")

SCRAPER_PROXY_LIST: list[str] = [
    p.strip() for p in (_RAW_PROXY_LIST or "").split(",") if p.strip()
]

NYT_SENDERS: list[str] = [
    "nytdirect@nytimes.com",
    "breakingnews@nytimes.com",
    "yourplaces-globalupdate-noreply@nytimes.com",
]

# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class CacheDocument:
    """A single cached document extracted from a newsletter or external link."""

    id: str
    source: str
    text: str


@dataclass
class EnrichedNews:
    """A fully enriched news item with merged content ready for translation."""

    title: str
    level: str
    content: str
    anchor_id: str = ""


# ---------------------------------------------------------------------------
# Named constants
# ---------------------------------------------------------------------------

MAX_TOKENS_PER_CHUNK = 180_000
MIN_JSON_EXTRACT_LENGTH = 500
JUNK_FOOTER_MAX_LENGTH = 3000
JUNK_GAMES_MAX_LENGTH = 1500
_MIN_SECTION_LENGTH = 100
SCRAPER_CONCURRENCY = int(os.getenv("SCRAPER_CONCURRENCY", "3"))
SCRAPER_BATCH_DELAY = 8.0
SCRAPER_RETRY_DELAY = 30.0
MIN_HTML_RESPONSE_LENGTH = 2000
HTTP_BLOCK_STATUS_CODES: frozenset[int] = frozenset({403, 429, 451, 503})
SCRAPER_URL_TIMEOUT = 30.0


class BoundedSet:
    """Set com tamanho máximo (LRU eviction).

    Evita memory leak em processos longos.
    """

    def __init__(self, maxsize: int = 500) -> None:
        self._data: OrderedDict[str, None] = OrderedDict()
        self._maxsize = maxsize

    def add(self, item: str) -> None:
        if item in self._data:
            self._data.move_to_end(item)
            return
        if len(self._data) >= self._maxsize:
            self._data.popitem(last=False)
        self._data[item] = None

    def __contains__(self, item: object) -> bool:
        return item in self._data

    def __len__(self) -> int:
        return len(self._data)

    def discard(self, item: str) -> None:
        self._data.pop(item, None)


_FAILED_URLS_CACHE: BoundedSet = BoundedSet(maxsize=500)

LEVEL_ORDER: list[str] = ["principal", "secundaria", "notas_curtas"]
LEVEL_PRIORITY: dict[str, int] = {"principal": 0, "secundaria": 1, "notas_curtas": 2}
_LEVEL_MERGE_PRIORITY: dict[str, int] = {
    "principal": 3,
    "secundaria": 2,
    "notas_curtas": 1,
}

_INDEX_TITLES: dict[str, str] = {
    "principal": "Editoriais Principais",
    "secundaria": "Giro de Notícias",
    "notas_curtas": "Notas Curtas",
}

ROUTER_MODEL = "gemini-3.1-flash-lite-preview"
GENERATOR_MODEL = "gemini-3-flash-preview"
SHORT_NOTES_MODEL = "gemini-2.5-flash"
GEMMA_FALLBACK_MODEL = "gemma-4-31b-it"

CACHE_FILE = "cache_nytt_latest.json"
CACHE_UIDS_FILE = "cache_nytt_uids.json"

# ---------------------------------------------------------------------------
# Frozensets
# ---------------------------------------------------------------------------

_ADMIN_KEYWORDS: frozenset[str] = frozenset(
    {
        "unsubscribe",
        "privacy",
        "manage",
        "browser",
        "preference",
        "contact",
        "california",
        "subscribe",
        "nytimes.com",
        "app",
        "help",
        "here",
        "wordle",
        "connections",
        "strands",
        "spelling bee",
        "crossword",
        "mini",
        "email",
        "opt out",
        "sudoku",
        "flashback",
        "the daily",
        "podcast",
        "audio",
        "wirecutter",
        "play the",
        "games",
        "recipe",
        "review",
        "guest essay",
        "interesting times",
        "i have some questions for you",
        "introductory offer",
        "keep them coming",
        "tell us what you want",
        "take a tour",
        "sign up",
    }
)
_EXACT_IGNORED_TEXTS: frozenset[str] = frozenset(
    {
        "newsletter help page",
        "go to home page",
        "see more technology news",
        "letters to the editor",
        "the new york times",
        "the financial times",
        "combine moves",
        "take this advice for a spin",
        "the numbers",
        "here is today's recipe",
        "mini crossword",
    }
)
_IGNORED_AUTHORS: frozenset[str] = frozenset(
    {
        "katrin bennhold",
        "andrew ross sorkin",
        "bernhard warner",
        "sarah kessler",
        "michael j. de la merced",
        "niko gallogly",
        "lauren hirsch",
        "brian o'keefe",
        "sam sifton",
        "sean dong",
        "sisi yu",
        "diego patiño",
        "jessica grose",
        "david wallace-wells",
    }
)
_DENY_DOMAINS: frozenset[str] = frozenset(
    {
        "wsj.com",
        "washingtonpost.com",
        "bloomberg.com",
        "ft.com",
        "cnbc.com",
        "finance.yahoo.com",
        "cato.org",
        "anthropic.com",
        "axios.com",
        "businessinsider.com",
        "reuters.com",
        "x.com",
        "youtube.com",
        "instagram.com",
        "tiktok.com",
        "msn.com",
    }
)
_DENY_PATHS: frozenset[str] = frozenset(
    {
        "/cooking/",
        "/wirecutter/",
        "/interactive/",
        "/live/",
        "/video/",
        "/puzzles/",
        "/crosswords/",
    }
)

_BLOCKED_PHRASES: frozenset[str] = frozenset(
    {
        "atividade incomum na rede",
        "not a robot",
        "prove you are human",
        "unusual activity",
        "access denied",
        "javascript is disabled",
        "enable js",
        "server error",
        "captcha",
        "challenge-platform",
        "cf-browser-verification",
        "ddos-protection",
        "checking your browser",
    }
)

_JUNK_BAD_HEADERS: frozenset[str] = frozenset(
    {
        "### games",
        "## play today's games",
        "### time to play",
        "### now time to play",
        "dealbook quiz",
        "time to play",
    }
)
_JUNK_FOOTER_SIGNALS: frozenset[str] = frozenset(
    {
        "california notices",
        "newsletter help page",
        "opt out of other promotional emails",
        "change your emailcontact us",
    }
)
_JUNK_GAMES_KEYWORDS: frozenset[str] = frozenset(
    {
        "wordle",
        "sudoku",
        "crossword",
        "spelling bee",
        "connections",
        "strands",
        "mini",
    }
)

# ---------------------------------------------------------------------------
# Regex patterns
# ---------------------------------------------------------------------------

_NOISE_PHRASE_RE = re.compile(
    r"(?:unsubscribe|view in browser|manage preferences"
    r"|copyright ©|all rights reserved|privacy policy"
    r"|terms of service|manage your\s*email\s*settings"
    r"|was this newsletter forwarded to you\?\s*sign up here\.?"
    r"|you received this email because[^.]*\."
    r"|if you received this newsletter from someone else[^.]*\.)",
    re.IGNORECASE,
)

_NOISE_LINE_RE = re.compile(
    r"^\s*(?:nytimes\.com|the new york times company\.?\s*\d.*|connect with us on:?"
    r"|\d+ eighth avenue.*)\s*$",
    re.IGNORECASE | re.MULTILINE,
)

_PRELOADED_JSON_RE = re.compile(
    r"window\.(?:__preloadedData|__NEXT_DATA__|__PRELOADED_STATE__)\s*=\s*(\{.*?\});",
    re.DOTALL,
)

_TABLE_TAG_RE = re.compile(
    r"</?(?:table|tbody|thead|tfoot|tr|td|th)[^>]*>",
    re.IGNORECASE,
)

_SECTION_SPLIT_RE = re.compile(
    r"(?=^#{2,3}\s)"
    r"|(?:^-{3,}$|^\*{3,}$)"
    r"|(?=^\*\*[A-Z][^*]{5,}\*\*\s*$)",
    re.MULTILINE,
)

_CODE_FENCE_RE = re.compile(r"```(?:json|html)?\s*(.*?)```", re.DOTALL)

_EXCESS_NEWLINE_RE = re.compile(r"\n{3,}")

_MARKDOWNIFY_TAGS: list[str] = [
    "p",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "ul",
    "ol",
    "li",
    "strong",
    "b",
    "em",
    "i",
    "blockquote",
    "figure",
    "figcaption",
]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _extract_preloaded_json(html: str) -> dict | None:
    """Extrai JSON de window.__preloadedData/etc. usando depth counting.

    Evita o backtracking O(n²) do _PRELOADED_JSON_RE em HTMLs grandes.
    """
    prefixes = (
        "window.__preloadedData",
        "window.__NEXT_DATA__",
        "window.__PRELOADED_STATE__",
    )
    for prefix in prefixes:
        idx = html.find(prefix)
        if idx == -1:
            continue
        # Verify there is an '=' between the prefix and the opening brace
        # (handles both `=` and ` = ` forms while rejecting unrelated vars).
        after_prefix = html[idx + len(prefix) : idx + len(prefix) + 10].lstrip()
        if not after_prefix.startswith("="):
            continue
        brace_start = html.find("{", idx + len(prefix))
        if brace_start == -1:
            continue
        depth, i = 0, brace_start
        while i < len(html):
            ch = html[i]
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(html[brace_start : i + 1])
                    except json.JSONDecodeError:
                        break
            i += 1
    return None


def _is_blocked_content(text: str) -> bool:
    """Returns True if *text* contains anti-bot or error-wall markers."""
    text_lower = text.lower()
    return any(phrase in text_lower for phrase in _BLOCKED_PHRASES)


def _strip_query(url: str) -> str:
    """Removes query string from a URL."""
    return url.split("?")[0]


def _extract_clean_response(text: str) -> str:
    """Extrai conteúdo de code fences; concatena múltiplos blocos se houver."""
    matches = _CODE_FENCE_RE.findall(text)
    if matches:
        return "\n".join(m.strip() for m in matches)
    return text.strip()


def _is_admin_text(text: str, admin_keywords: set[str] | frozenset[str]) -> bool:
    """Returns True if *text* matches any administrative keyword."""
    return any(kw in text for kw in admin_keywords)


def _is_junk_section(text: str) -> bool:
    """Detects if a markdown text chunk is purely administrative or games/quiz."""
    text_lower = text.lower()

    first_lines = [line.strip() for line in text_lower.split("\n")[:3]]
    for line in first_lines:
        if (
            any(line.startswith(bh) for bh in _JUNK_BAD_HEADERS)
            or line in _JUNK_BAD_HEADERS
        ):
            return True

    if (
        any(sig in text_lower for sig in _JUNK_FOOTER_SIGNALS)
        and len(text) < JUNK_FOOTER_MAX_LENGTH
    ):
        return True

    matches = sum(1 for kw in _JUNK_GAMES_KEYWORDS if kw in text_lower)
    return matches >= 2 and len(text) < JUNK_GAMES_MAX_LENGTH


def _validate_config() -> None:
    """Validates required environment variables at startup (fail-fast)."""
    if os.environ.get("SCRAPER_PROXY") and not os.environ.get("SCRAPER_PROXY_LIST"):
        logger.warning(
            "SCRAPER_PROXY is deprecated. Use SCRAPER_PROXY_LIST instead "
            "(comma-separated list of proxy URLs). "
            "The old variable will be ignored."
        )
    missing: list[str] = []
    if not EMAIL_ACCOUNT:
        missing.append("EMAIL_ACCOUNT")
    if not EMAIL_PASSWORD:
        missing.append("EMAIL_PASSWORD")
    if not KINDLE_EMAIL:
        missing.append("KINDLE_EMAIL")
    if not GOOGLE_API_KEY:
        missing.append("GOOGLE_API_KEY")
    if missing:
        raise SystemExit(
            f"Missing required environment variable(s): {', '.join(missing)}. "
            "Please check your .env file."
        )


class _ProxyRotator:
    """Encapsulates round-robin proxy rotation over SCRAPER_PROXY_LIST.

    Thread-safe for the call pattern used here (each URL calls _next_proxy once
    before the request, so no concurrent access to the same cycle).
    """

    def __init__(self) -> None:
        self._cycle: itertools.cycle[str] | None = None

    def reset(self) -> None:
        """Resets the cycle iterator. Use in tests to isolate state."""
        self._cycle = None

    def get_next(self) -> str | None:
        """Returns the next proxy from the rotation, or None if list is empty."""
        if not SCRAPER_PROXY_LIST:
            return None
        if self._cycle is None:
            self._cycle = itertools.cycle(SCRAPER_PROXY_LIST)
        return next(self._cycle)


_proxy_rotator = _ProxyRotator()


def _next_proxy() -> str | None:
    """Returns the next proxy from the rotation, or None if no proxies configured."""
    return _proxy_rotator.get_next()
