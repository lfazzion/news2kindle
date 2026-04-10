"""Main pipeline: fetch → cache → route → match → translate → send → cleanup."""

import asyncio
import datetime
import json
import logging
import math
import os
import random
import time
from dataclasses import asdict
from html import escape

from google.genai import errors as genai_errors

from core.config import (
    _FAILED_URLS_CACHE,
    _INDEX_TITLES,
    _LEVEL_MERGE_PRIORITY,
    CACHE_FILE,
    CACHE_UIDS_FILE,
    GENERATOR_MODEL,
    LEVEL_ORDER,
    LEVEL_PRIORITY,
    MAX_TOKENS_PER_CHUNK,
    ROUTER_MODEL,
    SCRAPER_CONCURRENCY,
    SCRAPER_RETRY_DELAY,
    SHORT_NOTES_MODEL,
    CacheDocument,
    EnrichedNews,
    _extract_clean_response,
    _strip_query,
    sanitize_html_for_kindle,
)
from core.email_client import _fetch_emails_sync, cleanup_emails, send_to_kindle
from core.gemini import _generate_content_async, _get_genai_client, _local_count_tokens
from core.prompts import HTML_TRANSLATE_PROMPT, JSON_CATEGORIZE_PROMPT
from core.scraper import _reset_async_session, fetch_article_text_async

logger = logging.getLogger(__name__)

_VALID_LEVELS: frozenset[str] = frozenset({"principal", "secundaria", "notas_curtas"})


def _validate_grouped_item(item: dict, idx: int) -> bool:
    """Valida e corrige in-place um item de grouped_news retornado pelo LLM.

    Retorna False se o item deve ser descartado.
    """
    title = item.get("title")
    level = item.get("level")
    cache_ids = item.get("cache_ids")

    if not isinstance(title, str) or not title.strip():
        logger.warning("Grouped item %d: 'title' inválido ou ausente: %r", idx, title)
        return False
    if level not in _VALID_LEVELS:
        logger.warning(
            "Grouped item %d: 'level' inválido %r, rebaixando para 'secundaria'.",
            idx,
            level,
        )
        item["level"] = "secundaria"
    if not isinstance(cache_ids, list) or not cache_ids:
        logger.warning("Grouped item %d: 'cache_ids' inválido: %r", idx, cache_ids)
        return False
    return True


async def categorize_news(cache_global: list[CacheDocument]) -> dict | None:
    """Sends the cache to Flash-Lite to group document IDs into news categories."""
    client = _get_genai_client()
    if not client:
        return None

    def get_item_tokens(item: dict) -> tuple[dict, int]:
        prompt_segment = json.dumps(item, ensure_ascii=False) + ","
        tokens = _local_count_tokens(ROUTER_MODEL, prompt_segment)
        return item, tokens

    logger.info(
        "Phase 2: Pre-calculating tokens for %d cached document(s) (local)...",
        len(cache_global),
    )
    items_for_prompt = [
        get_item_tokens({"id": doc.id, "text": doc.text}) for doc in cache_global
    ]

    chunks_json_str: list[str] = []
    current_chunk: list[dict] = []
    current_tokens = 2

    for item, item_tokens in items_for_prompt:
        if current_tokens + item_tokens > MAX_TOKENS_PER_CHUNK:
            if current_chunk:
                chunks_json_str.append(json.dumps(current_chunk, ensure_ascii=False))

            current_chunk = [item]
            current_tokens = item_tokens + 2
        else:
            current_chunk.append(item)
            current_tokens += item_tokens

    if current_chunk:
        chunks_json_str.append(json.dumps(current_chunk, ensure_ascii=False))

    all_grouped_news: list[dict] = []

    try:
        for i, chunk_str in enumerate(chunks_json_str):
            if i > 0:
                logger.info("Sending routing chunk %d...", i + 1)

            response = await _generate_content_async(
                client,
                JSON_CATEGORIZE_PROMPT.format(text=chunk_str),
                model=ROUTER_MODEL,
            )
            json_str = _extract_clean_response(response.text or "")
            data = json.loads(json_str)
            if not isinstance(data.get("grouped_news"), list):
                logger.error(
                    "Invalid JSON schema in chunk %d: "
                    "missing or invalid 'grouped_news' key.",
                    i + 1,
                )
                continue

            validated = [
                g
                for item_idx, g in enumerate(data["grouped_news"])
                if _validate_grouped_item(g, item_idx)
            ]
            all_grouped_news.extend(validated)

        if not all_grouped_news:
            return None

        merged_groups: dict[str, dict] = {}

        for group in all_grouped_news:
            raw_title = group.get("title", "Notícia").strip()
            norm_title = raw_title.lower()
            lvl = group.get("level", "secundaria")

            if norm_title not in merged_groups:
                merged_groups[norm_title] = {
                    "title": raw_title,
                    "level": lvl,
                    "cache_ids": set(),
                }

            current_lvl = merged_groups[norm_title]["level"]
            if _LEVEL_MERGE_PRIORITY.get(lvl, 2) > _LEVEL_MERGE_PRIORITY.get(
                current_lvl, 2
            ):
                merged_groups[norm_title]["level"] = lvl

            for c_id in group.get("cache_ids", []):
                merged_groups[norm_title]["cache_ids"].add(c_id)

        deduplicated_news = [
            {
                "title": d["title"],
                "level": d["level"],
                "cache_ids": list(d["cache_ids"]),
            }
            for d in merged_groups.values()
        ]

        logger.info(
            "Router returned %d raw news group(s), merged into "
            "%d deduplicated group(s) from %d cached document(s).",
            len(all_grouped_news),
            len(deduplicated_news),
            len(cache_global),
        )
        return {"grouped_news": deduplicated_news}
    except (
        genai_errors.APIError,
        ValueError,
        ConnectionError,
        json.JSONDecodeError,
    ) as e:
        logger.error(
            "Error generating news categories (JSON) gracefully falling back: %s", e
        )
        if not all_grouped_news:
            all_ids = [doc.id for doc in cache_global]
            logger.warning(
                "Phase 2 Router Fallback: Creating default "
                "'Recortes Adicionais' bucket for %d links.",
                len(all_ids),
            )
            all_grouped_news = [
                {
                    "title": "Recortes Adicionais",
                    "level": "secundaria",
                    "cache_ids": all_ids,
                }
            ]
        return {"grouped_news": all_grouped_news}


def match_cache_to_groups(
    grouped_news: list[dict],
    cache_global: list[CacheDocument],
) -> list[EnrichedNews]:
    """Maps grouped cache_ids back to the local cache, merging texts."""
    cache_lookup: dict[str, str] = {doc.id: doc.text for doc in cache_global}

    sorted_groups = sorted(
        grouped_news,
        key=lambda g: LEVEL_PRIORITY.get(g.get("level", "secundaria"), 1),
    )

    enriched: list[EnrichedNews] = []
    claimed_ids: set[str] = set()
    skipped_count = 0

    for group in sorted_groups:
        title = group.get("title", "Notícia")
        level = group.get("level", "secundaria")
        cache_ids = group.get("cache_ids", [])

        texts: list[str] = []
        for doc_id in cache_ids:
            if doc_id in claimed_ids:
                skipped_count += 1
                continue
            text = cache_lookup.get(doc_id)
            if text:
                texts.append(text)
                claimed_ids.add(doc_id)
            else:
                logger.warning("Matcher: cache_id '%s' not found in cache.", doc_id)

        if texts:
            enriched.append(
                EnrichedNews(
                    title=title,
                    level=level,
                    content="\n\n---\n\n".join(texts),
                )
            )

    if skipped_count:
        logger.info(
            "Matcher: skipped %d duplicate doc_id reference(s) "
            "already claimed by higher-priority groups.",
            skipped_count,
        )

    all_ids = {doc.id for doc in cache_global}
    orphans = all_ids - claimed_ids
    if orphans:
        orphan_sizes: list[tuple[str, int]] = []
        for doc in cache_global:
            if doc.id in orphans:
                orphan_sizes.append((doc.id, len(doc.text)))
        size_summary = ", ".join(
            f"{oid}({sz}c)" for oid, sz in sorted(orphan_sizes, key=lambda x: x[1])
        )
        logger.warning(
            "Matcher: %d document(s) not referenced by any group (Discarded). "
            "Orphan sizes: %s",
            len(orphans),
            size_summary,
        )

    logger.info(
        "Matcher: enriched %d group(s) from %d cached "
        "document(s) (%d orphan(s) discarded).",
        len(enriched),
        len(cache_global),
        len(orphans),
    )
    return enriched


async def summarize_text(enriched_news: list[EnrichedNews]) -> str | None:
    """Groups enriched news by level and translates each batch concurrently."""
    client = _get_genai_client()
    if not client:
        return None

    if not enriched_news:
        logger.warning("No enriched news items to translate.")
        return None

    today_str = datetime.date.today().strftime("%d/%m/%Y")

    html_parts: list[str] = [
        "<html><head><meta charset='utf-8'>",
        "<style>",
        "  body { font-family: Georgia, serif; line-height: 1.6; color: #111; }",
        "  h1 { font-size: 1.8em; border-bottom: 2px solid #222;"
        " margin-top: 1.5em; padding-bottom: 0.2em; font-weight: bold; }",
        "  h2 { font-size: 1.5em; color: #333;"
        " font-style: italic; margin-top: 1.2em; }",
        "  h3 { font-size: 1.2em; color: #444; margin-top: 1em; }",
        "  h4 { font-size: 1.1em; font-weight: bold; margin-top: 1em; color: #222; }",
        "  hr { border: 0; border-top: 1px dashed #aaa; margin: 30px 0; }",
        "  a { color: #0056b3; text-decoration: none; }",
        "  a:hover { text-decoration: underline; }",
        "  ul { margin-top: 0.5em; margin-bottom: 1em; padding-left: 20px; }",
        "  li { margin-bottom: 0.3em; }",
        "  p { margin-bottom: 1em; }",
        "  .indice-secao { font-weight: bold;"
        " margin-top: 1.2em; border-bottom: 1px solid #ddd;"
        " padding-bottom: 5px; }",
        "  .indice-lista { list-style-type: none; padding-left: 0; }",
        "  .indice-lista li { margin-bottom: 8px; font-size: 1.1em; }",
        "  .page-break { page-break-before: always; }",
        "</style>",
        "</head><body>",
        "<h1 style='text-align: center;"
        f" border-bottom: none;'>Newsletter Summary - {today_str}</h1>",
    ]

    grupos: dict[str, list[EnrichedNews]] = {}
    for idx, item in enumerate(enriched_news):
        lvl = item.level
        if lvl not in grupos:
            grupos[lvl] = []
        item.anchor_id = f"noticia-{idx}"
        grupos[lvl].append(item)

    async def _converte_bandeja(nivel: str, lista_noticias: list[EnrichedNews]) -> str:
        items_as_dicts = [asdict(n) for n in lista_noticias]
        conteudo_junto = json.dumps(items_as_dicts, ensure_ascii=False)

        model_to_use = SHORT_NOTES_MODEL if nivel == "notas_curtas" else GENERATOR_MODEL
        full_prompt = HTML_TRANSLATE_PROMPT.format(level=nivel, content=conteudo_junto)
        total_tokens = _local_count_tokens(model_to_use, full_prompt)

        if total_tokens <= MAX_TOKENS_PER_CHUNK:
            response = await _generate_content_async(
                client,
                HTML_TRANSLATE_PROMPT.format(level=nivel, content=conteudo_junto),
                model=model_to_use,
            )
            raw_html = _extract_clean_response(response.text or "")
            return sanitize_html_for_kindle(raw_html)

        logger.warning(
            "Bandeja '%s' exigiria %d tokens, o que excede o "
            "limite estrito de %d TPM. Dividindo lote ao meio...",
            nivel,
            total_tokens,
            MAX_TOKENS_PER_CHUNK,
        )
        if len(lista_noticias) <= 1:
            logger.error(
                "A single news article in '%s' is too massive "
                "to split. Attempting risky direct translation.",
                nivel,
            )
            response = await _generate_content_async(
                client,
                HTML_TRANSLATE_PROMPT.format(level=nivel, content=conteudo_junto),
                model=model_to_use,
            )
            raw_html = _extract_clean_response(response.text or "")
            return sanitize_html_for_kindle(raw_html)

        mid = len(lista_noticias) // 2
        esquerda = lista_noticias[:mid]
        direita = lista_noticias[mid:]

        html_esq = await _converte_bandeja(nivel, esquerda)
        html_dir = await _converte_bandeja(nivel, direita)
        return html_esq + "\n" + html_dir

    tarefas: list[asyncio.Task[str]] = []
    niveis_disparados: list[str] = []

    logger.info(
        "Disparando conversões em %d bandeja(s) de categorias: %s...",
        len(grupos),
        list(grupos.keys()),
    )
    for nivel, lista in grupos.items():
        niveis_disparados.append(nivel)
        tarefas.append(asyncio.create_task(_converte_bandeja(nivel, lista)))

    resultados_iniciais = await asyncio.gather(*tarefas, return_exceptions=True)

    falhas: list[str] = []
    html_resultado_por_nivel: dict[str, str] = {}

    for nivel, resultado in zip(niveis_disparados, resultados_iniciais, strict=True):
        if isinstance(resultado, BaseException):
            logger.warning(
                "Bandeja '%s' falhou na primeira tentativa: %s", nivel, resultado
            )
            falhas.append(nivel)
        else:
            html_resultado_por_nivel[nivel] = resultado

    if falhas:
        logger.info(
            "%d bandeja(s) falharam. Retentando (Rate Limiter cuidará do delay)...",
            len(falhas),
        )
        for nivel in falhas:
            try:
                html_resultado_por_nivel[nivel] = await _converte_bandeja(
                    nivel, grupos[nivel]
                )
                logger.info("Sucesso na retentativa da bandeja '%s'.", nivel)
            except Exception as e:
                logger.error(
                    "Falha definitiva na bandeja '%s' após repescagem: %s", nivel, e
                )

    toc_parts: list[str] = ["<h2>Índice de Hoje</h2>"]
    for nivel_esperado in LEVEL_ORDER:
        if nivel_esperado in html_resultado_por_nivel and nivel_esperado in grupos:
            secao_titulo = _INDEX_TITLES.get(
                nivel_esperado, nivel_esperado.capitalize()
            )
            toc_parts.append(f"<h3 class='indice-secao'>{secao_titulo}</h3>")
            toc_parts.append("<ul class='indice-lista'>")
            for item in grupos[nivel_esperado]:
                toc_parts.append(
                    f"<li><a href='#{item.anchor_id}'>"
                    f"&bull; {escape(item.title)}</a></li>"
                )
            toc_parts.append("</ul>")
    html_parts.extend(toc_parts)
    html_parts.append("<div class='page-break'></div>")

    for nivel_esperado in LEVEL_ORDER:
        if nivel_esperado in html_resultado_por_nivel:
            html_parts.append(html_resultado_por_nivel[nivel_esperado])

    for nivel, html_conteudo in html_resultado_por_nivel.items():
        if nivel not in LEVEL_ORDER:
            html_parts.append(html_conteudo)

    html_parts.append("</body></html>")
    return "\n".join(html_parts)


def _burst_delays(
    num_urls: int,
    burst_size: int = SCRAPER_CONCURRENCY,
    min_pause: float = 5.0,
    max_pause: float = 12.0,
) -> list[float]:
    """Generate inter-burst delays with jitter (uniform random).

    Returns a list of delays, one per burst boundary
    (len = ceil(num_urls/burst_size) - 1). The last burst has no delay after it.
    """
    num_bursts = math.ceil(num_urls / burst_size)
    num_delays = max(num_bursts - 1, 0)
    return [random.uniform(min_pause, max_pause) for _ in range(num_delays)]


async def extract_all_content_async() -> tuple[list[CacheDocument], list[str]]:
    """Downloads emails, converts to Markdown, and builds a local document cache."""
    try:
        (
            cache_global,
            processed,
            global_urls_to_fetch,
            seen_urls,
        ) = await asyncio.to_thread(_fetch_emails_sync)
    except Exception as e:
        logger.error("Failed to fetch/parse emails via imap_tools: %s", e)
        return [], []

    doc_counter = len(cache_global)

    deduped_urls: list[str] = []
    retry_candidates: list[str] = []
    retry_still_failed: list[str] = []

    if global_urls_to_fetch:
        _seen_base: set[str] = set()
        for u in global_urls_to_fetch:
            base = _strip_query(u)
            if base not in _seen_base:
                _seen_base.add(base)
                deduped_urls.append(u)
        if len(deduped_urls) < len(global_urls_to_fetch):
            logger.info(
                "Deduplicated %d URLs to %d (removed %d duplicates).",
                len(global_urls_to_fetch),
                len(deduped_urls),
                len(global_urls_to_fetch) - len(deduped_urls),
            )
        global_urls_to_fetch = deduped_urls

        logger.info(
            "Fetching %d external links in bursts of %d (jittered pause: 5-12s)...",
            len(global_urls_to_fetch),
            SCRAPER_CONCURRENCY,
        )
        semaphore = asyncio.Semaphore(SCRAPER_CONCURRENCY)
        all_results: list[tuple[str, str] | None] = []
        failed_urls: list[str] = []

        delays = _burst_delays(len(global_urls_to_fetch))
        delay_iter = iter(delays)

        for i in range(0, len(global_urls_to_fetch), SCRAPER_CONCURRENCY):
            batch = global_urls_to_fetch[i : i + SCRAPER_CONCURRENCY]
            batch_results = await asyncio.gather(
                *[fetch_article_text_async(u, semaphore) for u in batch]
            )
            for url, result in zip(batch, batch_results, strict=True):
                all_results.append(result)
                if result is None or not result[0]:
                    failed_urls.append(url)
            delay = next(delay_iter, None)
            if delay is not None:
                logger.info("Burst complete. Pausing %.1fs before next burst...", delay)
                await asyncio.sleep(delay)

        retry_candidates = failed_urls
        retry_still_failed: list[str] = []
        if retry_candidates:
            logger.info(
                "Retry pass: waiting %.0fs before retrying "
                "%d failed URL(s) with fresh session...",
                SCRAPER_RETRY_DELAY,
                len(retry_candidates),
            )
            await asyncio.sleep(SCRAPER_RETRY_DELAY)
            await _reset_async_session()
            retry_delays = _burst_delays(len(retry_candidates))
            retry_delay_iter = iter(retry_delays)
            for j in range(0, len(retry_candidates), SCRAPER_CONCURRENCY):
                retry_batch = retry_candidates[j : j + SCRAPER_CONCURRENCY]
                retry_batch_results = await asyncio.gather(
                    *[fetch_article_text_async(u, semaphore) for u in retry_batch]
                )
                for url, result in zip(retry_batch, retry_batch_results, strict=True):
                    if result and result[0]:
                        all_results.append(result)
                    else:
                        retry_still_failed.append(url)
                delay = next(retry_delay_iter, None)
                if delay is not None:
                    await asyncio.sleep(delay)
            for u in retry_still_failed:
                _FAILED_URLS_CACHE.add(_strip_query(u))
            if retry_still_failed:
                logger.warning(
                    "Retry pass complete: %d URL(s) still failed (marked as blocked).",
                    len(retry_still_failed),
                )
            else:
                logger.info("Retry pass complete: all URLs recovered.")

        for result in all_results:
            if result:
                content_text, resolved_url = result
                resolved_base = _strip_query(resolved_url)
                if (
                    content_text
                    and len(content_text) > 100
                    and resolved_base not in seen_urls
                ):
                    seen_urls.add(resolved_base)
                    doc_counter += 1
                    cache_global.append(
                        CacheDocument(
                            id=f"doc_{doc_counter}",
                            source="link_externo",
                            text=content_text,
                        )
                    )
                elif resolved_base in seen_urls:
                    logger.debug(
                        "Skipping duplicate resolved URL: %s", resolved_base[:80]
                    )

    logger.info(
        "Phase 1 complete: built cache with %d document(s) "
        "(%d newsletters, %d external links).",
        len(cache_global),
        sum(1 for d in cache_global if d.source == "newsletter"),
        sum(1 for d in cache_global if d.source == "link_externo"),
    )
    if global_urls_to_fetch:
        total_queued = len(deduped_urls)
        total_success = sum(1 for d in cache_global if d.source == "link_externo")
        total_failed = len(retry_still_failed) if retry_candidates else 0
        logger.info(
            "Scraping summary: %d queued → %d succeeded, %d failed.",
            total_queued,
            total_success,
            total_failed,
        )
    return cache_global, processed


async def process_newsletters() -> None:
    """Main pipeline V3: fetch → cache → route → match → translate → send → cleanup."""
    t_total = time.monotonic()
    logger.info("Starting process_newsletters (V3 Cache Routing)...")
    metrics: dict[str, float | int | str] = {
        "phase1_s": 0.0,
        "phase2_s": 0.0,
        "phase3_s": 0.0,
    }

    cache_global: list[CacheDocument] = []
    emails_to_delete: list[str] = []
    use_local_cache = False

    if os.path.exists(CACHE_FILE):
        try:
            file_mod_time = datetime.datetime.fromtimestamp(
                os.path.getmtime(CACHE_FILE)
            )
            if file_mod_time.date() == datetime.date.today():
                with open(CACHE_FILE, encoding="utf-8") as f:
                    raw_cache = json.load(f)
                cache_global = [CacheDocument(**doc) for doc in raw_cache]
                if cache_global:
                    logger.info(
                        "Found valid local cache built today. "
                        "Skipping IMAP fetching and Web Scraping."
                    )
                    logger.info(
                        "Loaded %d documents from local cache.", len(cache_global)
                    )
                    use_local_cache = True
                    if os.path.exists(CACHE_UIDS_FILE):
                        try:
                            with open(CACHE_UIDS_FILE, encoding="utf-8") as uf:
                                emails_to_delete = json.load(uf)
                            logger.info(
                                "Loaded %d email UID(s) from local cache for cleanup.",
                                len(emails_to_delete),
                            )
                        except Exception as ue:
                            logger.warning("Failed to load cached email UIDs: %s", ue)
        except Exception as e:
            logger.warning("Failed to load local cache file: %s", e)
            cache_global = []

    if not use_local_cache:
        try:
            logger.info("Phase 1: Extracting content and building local cache…")
            t1 = time.monotonic()
            cache_global, emails_to_delete = await extract_all_content_async()
            metrics["phase1_s"] = round(time.monotonic() - t1, 1)

            if cache_global:
                try:
                    with open(CACHE_FILE, "w", encoding="utf-8") as f:
                        json.dump(
                            [asdict(doc) for doc in cache_global],
                            f,
                            ensure_ascii=False,
                            indent=2,
                        )
                    if emails_to_delete:
                        with open(CACHE_UIDS_FILE, "w", encoding="utf-8") as uf:
                            json.dump(emails_to_delete, uf)
                    logger.info(
                        "Local cache saved to %s for failover recovery.", CACHE_FILE
                    )
                except Exception as e:
                    logger.warning("Could not persist local cache file: %s", e)

        except Exception as e:
            logger.error("Error during extraction: %s", e)
            return

    if not cache_global:
        logger.info("No usable content extracted from newsletters.")
        return

    logger.info("Phase 2: Routing content with %s…", ROUTER_MODEL)
    t2 = time.monotonic()
    routing_json = await categorize_news(cache_global)
    metrics["phase2_s"] = round(time.monotonic() - t2, 1)
    if not routing_json:
        logger.error("Failed to route/categorize news into JSON.")
        return

    logger.info("Phase 2.5: Matching cache IDs to document text (Python Matcher)…")
    enriched_news = match_cache_to_groups(
        routing_json["grouped_news"],
        cache_global,
    )
    if not enriched_news:
        logger.error("Matcher produced no enriched news items.")
        return

    logger.info("Phase 3: Generating final HTML content with %s…", GENERATOR_MODEL)
    t3 = time.monotonic()
    summary = await summarize_text(enriched_news)
    metrics["phase3_s"] = round(time.monotonic() - t3, 1)
    if not summary:
        logger.error("Failed to generate final HTML summary.")
        return

    logger.info("Summary generated. Sending to Kindle…")
    today_str = datetime.date.today().strftime("%Y/%m/%d")

    kindle_sent = await send_to_kindle(summary, today_str)
    if not kindle_sent:
        logger.error("Failed to send to Kindle.")

    try:
        if kindle_sent and emails_to_delete:
            await cleanup_emails(emails_to_delete)
    finally:
        for cache_path in (CACHE_FILE, CACHE_UIDS_FILE):
            if os.path.exists(cache_path):
                try:
                    os.remove(cache_path)
                except Exception as e:
                    logger.warning("Failed to remove cache file %s: %s", cache_path, e)
        logger.info("Local failover cache files purged.")

    if not kindle_sent:
        return

    metrics["total_s"] = round(time.monotonic() - t_total, 1)
    metrics["docs"] = len(cache_global)
    metrics["groups"] = len(enriched_news)
    logger.info(
        "=== Pipeline Summary ===  "
        "docs=%(docs)d  groups=%(groups)d  "
        "p1=%(phase1_s).1fs  p2=%(phase2_s).1fs  p3=%(phase3_s).1fs  "
        "total=%(total_s).1fs",
        metrics,
    )
