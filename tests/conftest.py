"""Shared pytest fixtures used across all test modules."""

import email as email_stdlib
import os
import sys
from email import policy
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

# Ensure project root is importable
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# Set dummy env vars BEFORE importing newsletter modules
# (config reads them at module level)
os.environ.setdefault("EMAIL_ACCOUNT", "test@example.com")
os.environ.setdefault("EMAIL_PASSWORD", "testpassword")
os.environ.setdefault("IMAP_SERVER", "imap.gmail.com")
os.environ.setdefault("SMTP_SERVER", "smtp.gmail.com")
os.environ.setdefault("SMTP_PORT", "465")
os.environ.setdefault("KINDLE_EMAIL", "kindle@example.com")
os.environ.setdefault("GOOGLE_API_KEY", "fake-api-key-for-testing")

from google import genai  # noqa: E402

from core.config import CacheDocument, EnrichedNews  # noqa: E402

# ---------------------------------------------------------------------------
# Newsletter example fixtures
# ---------------------------------------------------------------------------
NEWSLETTER_EXAMPLES_DIR = PROJECT_ROOT / "newsletters"


def _load_eml_html(filename: str) -> str | None:
    """Loads an .eml file and returns its HTML body."""
    filepath = NEWSLETTER_EXAMPLES_DIR / filename
    if not filepath.exists():
        return None
    with open(filepath, "rb") as f:
        msg = email_stdlib.message_from_binary_file(f, policy=policy.default)
    for part in msg.walk():
        if part.get_content_type() == "text/html":
            return part.get_content()
    return None


@pytest.fixture
def morning_newsletter_html() -> str | None:
    """The Morning newsletter HTML body."""
    return _load_eml_html("The Morning_ Primetime Trump.eml")


@pytest.fixture
def dealbook_newsletter_html() -> str | None:
    """DealBook newsletter HTML body."""
    return _load_eml_html("DealBook_ The market's rebuttal.eml")


@pytest.fixture
def opinion_newsletter_html() -> str | None:
    """Opinion Today newsletter HTML body."""
    return _load_eml_html("Opinion Today_ About Trump's speech last night ….eml")


@pytest.fixture
def world_newsletter_html() -> str | None:
    """The World newsletter HTML body."""
    return _load_eml_html("The World_ Trump's Iran endgame_.eml")


@pytest.fixture
def ontech_newsletter_html() -> str | None:
    """On Tech newsletter HTML body."""
    return _load_eml_html("On Tech_ Chromebook remorse.eml")


@pytest.fixture
def breaking_news_newsletter_html() -> str | None:
    """Breaking News newsletter HTML body."""
    return _load_eml_html(
        "Breaking news_ Judge halts construction on Trump's White House ballroom .eml"
    )


@pytest.fixture
def global_update_newsletter_html() -> str | None:
    """Global Update newsletter HTML body."""
    return _load_eml_html(
        "Global Update_ When Racism Is a Crime_"
        " Brazil Puts a Tourist on Trial for Word and Gesture.eml"
    )


@pytest.fixture
def in_speech_newsletter_html() -> str | None:
    """In Speech newsletter HTML body."""
    return _load_eml_html(
        "In speech, Trump claims success in Iran war"
        " but offers no clear timeline for its end.eml"
    )


@pytest.fixture
def all_newsletter_htmls(
    morning_newsletter_html,
    dealbook_newsletter_html,
    opinion_newsletter_html,
    world_newsletter_html,
    ontech_newsletter_html,
    breaking_news_newsletter_html,
    global_update_newsletter_html,
    in_speech_newsletter_html,
) -> list[tuple[str, str | None]]:
    """All newsletters as (name, html) pairs."""
    return [
        ("Morning", morning_newsletter_html),
        ("DealBook", dealbook_newsletter_html),
        ("Opinion", opinion_newsletter_html),
        ("World", world_newsletter_html),
        ("OnTech", ontech_newsletter_html),
        ("BreakingNews", breaking_news_newsletter_html),
        ("GlobalUpdate", global_update_newsletter_html),
        ("InSpeech", in_speech_newsletter_html),
    ]


# ---------------------------------------------------------------------------
# Dataclass fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def sample_cache_docs() -> list[CacheDocument]:
    """A minimal set of CacheDocument instances for testing."""
    return [
        CacheDocument(
            id="doc_1",
            source="newsletter",
            text="The economy showed signs of recovery today as markets rallied.",
        ),
        CacheDocument(
            id="doc_2",
            source="newsletter",
            text="War continues to affect global supply chains and energy prices.",
        ),
        CacheDocument(
            id="doc_3",
            source="link_externo",
            text="A new space telescope has captured images of distant galaxies.",
        ),
        CacheDocument(
            id="doc_4",
            source="newsletter",
            text="Climate change policies are being debated in Congress.",
        ),
        CacheDocument(
            id="doc_5",
            source="link_externo",
            text="Tech companies report strong quarterly earnings.",
        ),
    ]


@pytest.fixture
def sample_grouped_news() -> list[dict]:
    """Router output — grouped news dicts as returned by the LLM."""
    return [
        {
            "title": "Economia em Recuperação",
            "level": "principal",
            "cache_ids": ["doc_1"],
        },
        {
            "title": "Guerra e Cadeia de Suprimentos",
            "level": "principal",
            "cache_ids": ["doc_2"],
        },
        {
            "title": "Novas Descobertas Espaciais",
            "level": "secundaria",
            "cache_ids": ["doc_3"],
        },
        {
            "title": "Debate Climático no Congresso",
            "level": "secundaria",
            "cache_ids": ["doc_4"],
        },
        {
            "title": "Resultados de Tecnologia",
            "level": "notas_curtas",
            "cache_ids": ["doc_5"],
        },
    ]


@pytest.fixture
def sample_enriched_news() -> list[EnrichedNews]:
    """Enriched news items as output by the matcher."""
    return [
        EnrichedNews(
            title="Economia em Recuperação",
            level="principal",
            content="The economy showed signs of recovery.",
            anchor_id="noticia-0",
        ),
        EnrichedNews(
            title="Guerra e Cadeia",
            level="secundaria",
            content="War continues to affect supply chains.",
            anchor_id="noticia-1",
        ),
        EnrichedNews(
            title="Notas de Tecnologia",
            level="notas_curtas",
            content="Tech companies report earnings.",
            anchor_id="noticia-2",
        ),
    ]


# ---------------------------------------------------------------------------
# Mock fixtures for external services
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_genai_client():
    """A mocked Google GenAI client."""
    client = MagicMock(spec=genai.Client)
    aio_models = AsyncMock()
    client.aio.models = aio_models
    return client
