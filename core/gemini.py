"""Google GenAI integration with retry and rate limiting."""

import asyncio
import logging

from aiolimiter import AsyncLimiter
from google import genai
from google.genai import errors as genai_errors
from google.genai import types as genai_types
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from core.config import (
    GEMMA_FALLBACK_MODEL,
    GENERATOR_MODEL,
    GOOGLE_API_KEY,
    ROUTER_MODEL,
    SHORT_NOTES_MODEL,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Rate limiters — lazy initialization to avoid binding to a closed event loop
# ---------------------------------------------------------------------------

MODEL_RPM_LIMITERS: dict[str, AsyncLimiter] | None = None
MODEL_TPM_LIMITERS: dict[str, AsyncLimiter] | None = None


def _init_limiters() -> None:
    """Lazily initializes the AsyncLimiter instances for RPM and TPM."""
    global MODEL_RPM_LIMITERS, MODEL_TPM_LIMITERS
    if MODEL_RPM_LIMITERS is None:
        MODEL_RPM_LIMITERS = {
            ROUTER_MODEL: AsyncLimiter(14, 60.0),
            GENERATOR_MODEL: AsyncLimiter(4, 60.0),
            SHORT_NOTES_MODEL: AsyncLimiter(4, 60.0),
            GEMMA_FALLBACK_MODEL: AsyncLimiter(15, 60.0),
        }
    if MODEL_TPM_LIMITERS is None:
        MODEL_TPM_LIMITERS = {
            ROUTER_MODEL: AsyncLimiter(200_000, 60.0),
            GENERATOR_MODEL: AsyncLimiter(200_000, 60.0),
            SHORT_NOTES_MODEL: AsyncLimiter(200_000, 60.0),
            # Gemma 4 31B — no TPM limit, only RPM
        }


# ---------------------------------------------------------------------------
# GenAI client singleton
# ---------------------------------------------------------------------------

_genai_client: genai.Client | None = None


def _get_genai_client() -> genai.Client | None:
    """Returns a cached Google GenAI client singleton, or None if the key is missing."""
    global _genai_client
    if _genai_client is not None:
        return _genai_client
    if not GOOGLE_API_KEY:
        logger.error("GOOGLE_API_KEY not set.")
        return None
    _init_limiters()
    _genai_client = genai.Client(
        api_key=GOOGLE_API_KEY,
        http_options=genai_types.HttpOptions(timeout=90000),
    )
    return _genai_client


# ---------------------------------------------------------------------------
# Token counting (local estimation — no LocalTokenizer in google-genai SDK)
# ---------------------------------------------------------------------------


def _local_count_tokens(model: str, text: str) -> int:
    """Estimativa conservadora: chars/3 + 15% de margem para conteúdo misto."""
    return int(len(text) / 3 * 1.15)


# ---------------------------------------------------------------------------
# Content generation
# ---------------------------------------------------------------------------


@retry(
    stop=stop_after_attempt(5),
    wait=wait_exponential(multiplier=1.5, min=4, max=45),
    retry=retry_if_exception_type(
        (genai_errors.APIError, ConnectionError, TimeoutError)
    ),
    reraise=True,
)
async def _generate_content_retry(
    client: genai.Client,
    prompt: str,
    model: str,
    system_instruction: str | None = None,
) -> genai.types.GenerateContentResponse:
    """Internal retry block. It will fail after 5 attempts."""
    config = (
        genai_types.GenerateContentConfig(
            system_instruction=system_instruction,
            temperature=0.3,
        )
        if system_instruction
        else None
    )
    async with asyncio.timeout(90.0):
        return await client.aio.models.generate_content(
            model=model,
            contents=prompt,
            config=config,
        )


async def _generate_content_async(
    client: genai.Client,
    prompt: str,
    model: str = GENERATOR_MODEL,
    system_instruction: str | None = None,
) -> genai.types.GenerateContentResponse:
    """Calls Gemini with automatic retry, token counting, and rate limiting."""
    _init_limiters()
    if MODEL_RPM_LIMITERS is None or MODEL_TPM_LIMITERS is None:
        raise RuntimeError(
            "Rate limiters not initialized. Call _init_limiters() first."
        )

    rpm_limiter = MODEL_RPM_LIMITERS.get(model, MODEL_RPM_LIMITERS[GENERATOR_MODEL])
    tpm_limiter = MODEL_TPM_LIMITERS.get(model)
    exact_tokens = _local_count_tokens(model, prompt)

    try:
        if tpm_limiter is not None:
            tokens_to_acquire = min(exact_tokens, tpm_limiter.max_rate)
            await tpm_limiter.acquire(tokens_to_acquire)
        async with rpm_limiter:
            return await _generate_content_retry(
                client, prompt, model, system_instruction=system_instruction
            )
    except Exception as e:
        logger.warning(
            "Error during token counting or generation: %s", e, exc_info=True
        )

        # Build fallback chain: Gemma first, then SHORT_NOTES_MODEL (if different)
        fallback_chain: list[str] = []
        if model != GEMMA_FALLBACK_MODEL:
            fallback_chain.append(GEMMA_FALLBACK_MODEL)
        if model != SHORT_NOTES_MODEL and GEMMA_FALLBACK_MODEL != SHORT_NOTES_MODEL:
            fallback_chain.append(SHORT_NOTES_MODEL)

        if not fallback_chain:
            logger.error("No fallback available for %s.", model)
            raise

        for fallback_model in fallback_chain:
            logger.warning(
                "Model %s failed. Falling back to: %s",
                model,
                fallback_model,
            )
            try:
                fb_rpm = MODEL_RPM_LIMITERS.get(
                    fallback_model, MODEL_RPM_LIMITERS[GENERATOR_MODEL]
                )
                fb_tpm = MODEL_TPM_LIMITERS.get(fallback_model)
                if fb_tpm is not None:
                    tokens_to_acquire = min(exact_tokens, fb_tpm.max_rate)
                    await fb_tpm.acquire(tokens_to_acquire)
                async with fb_rpm:
                    return await _generate_content_retry(
                        client,
                        prompt,
                        fallback_model,
                        system_instruction=system_instruction,
                    )
            except Exception:
                logger.warning("Fallback model %s also failed.", fallback_model)

        logger.error("All fallback models exhausted.")
        raise
