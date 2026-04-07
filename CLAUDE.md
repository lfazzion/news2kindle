# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Setup
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Run pipeline
python main.py

# Tests
python -m pytest tests/ -v
python -m pytest tests/test_pipeline.py -v          # single module
python -m pytest tests/ -v --cov --cov-report=html  # with coverage

# Lint/format
ruff check .
ruff format .
```

Tests require Python >= 3.13. `asyncio_mode = "auto"` is set in `pyproject.toml` — `@pytest.mark.asyncio` is optional on async tests.

## Architecture

The pipeline runs as a 5-phase async flow orchestrated by `core/pipeline.py:process_newsletters()`:

1. **Phase 0 — Cache check**: If `cache_nytt_latest.json` exists and was created today, skip IMAP + scraping entirely.
2. **Phase 1 — Extraction** (`extract_all_content_async`): IMAP fetch via `email_client._fetch_emails_sync` (run in thread), HTML→Markdown conversion in `extractor.py`, then async scraping of external links in bursts of 3 with jitter (5-12s), retry pass after 30s.
3. **Phase 2 — Routing** (`categorize_news`): Sends all `CacheDocument` IDs + texts to `gemini-3.1-flash-lite-preview` as JSON; gets back grouped news with `level` = `principal` | `secundaria` | `notas_curtas`.
4. **Phase 2.5 — Matching** (`match_cache_to_groups`): Pure Python — maps `cache_ids` from the router back to cached text. Each doc ID claimed once, highest-priority level wins.
5. **Phase 3 — Translation** (`summarize_text`): Sends each level bucket concurrently to Gemini (`gemini-3-flash-preview` for principal/secundaria, `gemini-2.5-flash` for notas_curtas). Recursively splits if >180k tokens. Assembles final Kindle-optimized HTML.
6. **Phase 4 — Delivery**: SMTP send to Kindle email, IMAP trash move, cache file cleanup.

### Key files

| File | Role |
|---|---|
| `core/config.py` | All constants, env vars, dataclasses (`CacheDocument`, `EnrichedNews`), helper functions, deny-lists (domains, paths, admin keywords) |
| `core/gemini.py` | GenAI client singleton, lazy rate limiter init (RPM + TPM per model), retry decorator, fallback chain (Gemma → SHORT_NOTES_MODEL) |
| `core/prompts.py` | `JSON_CATEGORIZE_PROMPT` (router) and `HTML_TRANSLATE_PROMPT` (generator) — both in Portuguese |
| `core/scraper.py` | stealth-requests (primary) → curl_cffi (fallback); proxy rotation; `_FAILED_URLS_CACHE` skips known-blocked URLs |
| `core/extractor.py` | HTML parsing, `StoryBodyCompanionColumn` extraction, trafilatura, markdown section splitting |
| `core/email_client.py` | IMAP fetch with `imap-tools`, SMTP send via `aiosmtplib` |

### Scraper fallback cascade

`stealth-requests` (UA rotation + TLS Chrome) → `curl_cffi` AsyncSession (Chrome 145/146 fingerprint) → discard.  
Fallback triggers on HTTP 403/429/503 or response size < `MIN_HTML_RESPONSE_LENGTH` (2000 chars).

### Gemini model/rate limits (free tier)

| Constant | Model | RPM limiter |
|---|---|---|
| `ROUTER_MODEL` | `gemini-3.1-flash-lite-preview` | 14 RPM |
| `GENERATOR_MODEL` | `gemini-3-flash-preview` | 4 RPM |
| `SHORT_NOTES_MODEL` | `gemini-2.5-flash` | 4 RPM |
| `GEMMA_FALLBACK_MODEL` | `gemma-4-31b-it` | 15 RPM |

Rate limiters are lazily initialized in `_init_limiters()` to avoid binding to a closed event loop.

## Environment variables

Required: `EMAIL_ACCOUNT`, `EMAIL_PASSWORD`, `KINDLE_EMAIL`, `GOOGLE_API_KEY`.  
Optional: `IMAP_SERVER` (default `imap.gmail.com`), `SMTP_SERVER`, `SMTP_PORT` (587), `SCRAPER_PROXY_LIST` (CSV of proxy URLs for bypassing Cloudflare on GitHub Actions datacenter IPs).

Validated at startup by `config._validate_config()`. Use a `.env` file locally (`python-dotenv` is loaded in `config.py`).

## Tests

Fixtures in `tests/conftest.py` set dummy env vars before any imports. Real `.eml` newsletter files go in a `newsletters/` directory (gitignored) — tests that load them skip gracefully if absent. Mock the GenAI client via `mock_genai_client` fixture (`MagicMock(spec=genai.Client)` with `AsyncMock` on `client.aio.models`).
