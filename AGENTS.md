# AGENTS.md

## Quick context

Python 3.13 async pipeline that fetches NYT newsletters via IMAP, scrapes linked articles (stealth-requests → curl_cffi cascade), routes/merges/translates them with Gemini, and sends HTML summaries to Kindle. Runs daily via GitHub Actions cron.

## Commands

```bash
python -m pytest tests/ -v                    # all tests
python -m pytest tests/ -v --cov --cov-report=html  # coverage
ruff check core/ main.py tests/              # lint
ruff format core/ main.py tests/             # format
python main.py                               # run pipeline (needs .env with real creds)
```

Tests use `asyncio_mode = "auto"` in `pyproject.toml` — the `@pytest.mark.asyncio` marker is optional.

## Environment

Env vars are loaded via `python-dotenv` from `.env` (gitignored). Required vars: `EMAIL_ACCOUNT`, `EMAIL_PASSWORD`, `KINDLE_EMAIL`, `GOOGLE_API_KEY`. Optional: `IMAP_SERVER` (default `imap.gmail.com`), `SMTP_SERVER`, `SMTP_PORT`, `SCRAPER_PROXY_LIST` (CSV of proxy URLs).

`config.py` reads env vars at **module import time**. Tests set dummy values in `conftest.py` via `os.environ.setdefault` before any core import — if you add a new required var, add a default there too.

## Architecture

- `main.py` — thin entry point, calls `_validate_config()` then `asyncio.run(process_newsletters())`
- `core/config.py` — env vars, dataclasses (`CacheDocument`, `EnrichedNews`), constants, frozensets for domain/keyword filtering, helper functions
- `core/scraper.py` — two-tier scraper: `stealth_requests.get()` (sync, run in thread) → `curl_cffi.AsyncSession` (async fallback). Session is a module-level singleton; use `_reset_async_session()` between retry passes
- `core/gemini.py` — GenAI client singleton, lazy `AsyncLimiter` instances for RPM/TPM per model, tenacity retry (5 attempts), automatic fallback chain (Gemma 4 → gemini-2.5-flash)
- `core/pipeline.py` — orchestrator: Phase 1 (IMAP + scrape + cache), Phase 2 (Gemini router), Phase 2.5 (Python matcher), Phase 3 (translate + HTML gen), Phase 4 (SMTP to Kindle + cleanup)
- `core/extractor.py` — HTML parsing, section splitting, noise removal
- `core/prompts.py` — Gemini prompt templates
- `core/email_client.py` — IMAP fetch + SMTP send

Pipeline writes `cache_nytt_latest.json` and `cache_nytt_uids.json` to CWD as failover; deletes them on success. If a same-day cache exists, Phase 1 is skipped entirely.

## Testing

Tests live in `tests/`. Fixtures in `conftest.py` provide:
- Newsletter HTML fixtures loaded from a `newsletters/` directory (`.eml` files, gitignored) — fixtures return `None` if files are missing
- `CacheDocument`, `EnrichedNews`, and mock Gemini client fixtures

Tests heavily mock external services (IMAP, SMTP, Gemini). The scraper tests mock `stealth_requests` and `curl_cffi` responses.

## Key conventions

- **No linter/formatter CI** — `ruff` is configured in `pyproject.toml` but there's no CI step for it. Run manually before committing.
- **Python 3.13 target** in CI, but `ruff.target-version` is set to `py312` in pyproject.toml.
- **Portuguese variable/log names** appear in `pipeline.py` (e.g., `converte_bandeja`, `nivel`, `tarefas`). Match existing style in that file.
- **Gemini models**: `gemini-3.1-flash-lite-preview` (router), `gemini-3-flash-preview` (generator), `gemini-2.5-flash` (short notes), `gemma-4-31b-it` (fallback). Free-tier rate limits are enforced by `AsyncLimiter`.
- **Token estimation** uses `len(text) // 3` (no real tokenizer). `MAX_TOKENS_PER_CHUNK = 180_000`.
- **Scraper cascade**: stealth-requests (sync in thread) is primary; curl_cffi (async) is fallback. Both reject responses < 2000 chars or with blocked status codes (403, 429, 451, 503). Failed URLs get a 30s retry with fresh session.
- Cache files (`cache_nytt_latest.json`, `cache_nytt_uids.json`) are gitignored but may appear in working directory during development.
