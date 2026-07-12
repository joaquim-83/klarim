"""Reprocessa alvos `sem_contato` (KL-50): multi-page crawl + parser expandido.

Para cada alvo sem e-mail: busca páginas internas (/contato, /sobre, …), tenta
extrair um e-mail válido (com MX) e o perfil comercial. Se achar e-mail, o alvo
volta a `discovered` e é enfileirado para scan; sempre grava o `site_profile`.

Uso (na VM):
    docker compose exec api python scripts/enrich_batch.py [--limit 500]

Respeita o rate limit de 1 req/s por domínio (via scanner.checks.base.fetch).
Progresso em `enrichment_batch.log` + stdout. Idempotente: pode rodar em batches
diários (500/dia) até drenar os ~4k `sem_contato`.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys

# Permite `python scripts/enrich_batch.py` (adiciona a raiz do projeto ao path).
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scanner import profiler  # noqa: E402
from scanner.checks import dns_util  # noqa: E402
from scanner.checks.base import fetch, base_url, registrable_domain, domain_of  # noqa: E402
from discovery.store import get_target_store  # noqa: E402
from discovery.contact import (  # noqa: E402
    extract_email, _clean_email, _is_valid_email, _is_junk, email_has_mx)
from discovery.classifier import PRICE_TIERS  # noqa: E402
from scanner.ai_enrichment import (  # noqa: E402
    AI_ENRICHMENT_ENABLED, ai_enrich, merge_ai_into_profile)

SCAN_QUEUE = os.environ.get("KLARIM_SCAN_QUEUE", "klarim:scan_queue")


async def _validate_ai_email(raw: str):
    """E-mail vindo da IA: limpa + valida formato + MX (KL-24) antes de usar."""
    e = _clean_email(raw or "")
    if not e or not _is_valid_email(e) or _is_junk(e):
        return None
    return e if await asyncio.to_thread(email_has_mx, e) else None

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(message)s",
    handlers=[logging.FileHandler("enrichment_batch.log"), logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("enrich")


async def _enqueue(redis, target_id: int, url: str) -> None:
    if redis is None:
        return
    await redis.rpush(SCAN_QUEUE, json.dumps(
        {"target_id": target_id, "url": url, "source": "discovery"}))


async def _process_one(store, redis, target: dict) -> str:
    """Processa um alvo. Retorna: 'email' | 'profile_only' | 'nada' | 'erro'."""
    url = target["url"]
    tid = target["id"]
    try:
        pages = await profiler.crawl_contact_pages(url)
        if not pages:
            return "nada"
        homepage = pages.get("homepage")

        # 1) e-mail (reusa a extração hardened do discovery; MX-validada).
        email = None
        for html in pages.values():
            email = await extract_email(html, url, validate_mx=True)
            if email:
                break

        # 2) perfil comercial (headers + MX/NS).
        headers = {}
        try:
            hp = await fetch(base_url(url) + "/", method="GET", follow_redirects=True)
            headers = dict(hp.headers)
        except Exception:  # noqa: BLE001
            pass
        dom = registrable_domain(domain_of(url))
        mx = await asyncio.to_thread(dns_util.resolve_mx, dom)
        ns = await asyncio.to_thread(dns_util.resolve_ns, dom)
        profile = await profiler.build_profile(
            url, homepage_html=homepage, headers=headers, mx_records=mx, ns_records=ns)

        # 3) IA (KL-47A): refina setor + preenche campos vazios do perfil; se o regex
        #    não achou e-mail, a IA tenta (validado por MX antes de sair de sem_contato).
        ai_email = None
        if AI_ENRICHMENT_ENABLED and homepage:
            try:
                ai = await ai_enrich(dom, homepage, current_profile=profile)
                if ai:
                    merge_ai_into_profile(profile, ai)
                    sector = ai.get("sector")
                    conf = float(ai.get("sector_confidence") or 0.0)
                    if sector and sector != "outro" and conf > 0.7:
                        await store.ai_update_classification(
                            tid, sector, PRICE_TIERS.get(sector, "standard"), conf)
                    if not email:
                        ai_email = await _validate_ai_email((ai.get("contacts_found") or {}).get("email"))
                    log.info("[ai] %s sector=%s conf=%.2f email_ia=%s", url, sector, conf,
                             ai_email or "-")
            except Exception as exc:  # noqa: BLE001
                log.warning("[ai] falha em %s: %r", url, exc)
            await asyncio.sleep(1)  # rate limit OpenAI

        try:
            await store.upsert_site_profile(tid, profile)
        except Exception as exc:  # noqa: BLE001
            log.warning("perfil não gravado para %s: %r", url, exc)

        final_email = email or ai_email
        if final_email:
            await store.update_target_email(tid, final_email)   # sem_contato -> discovered
            await _enqueue(redis, tid, url)
            log.info("✓ %s -> e-mail %s%s (enfileirado)", url, final_email,
                     " (IA)" if ai_email and not email else "")
            return "email"
        return "profile_only"
    except Exception as exc:  # noqa: BLE001
        log.warning("erro em %s: %r", url, exc)
        return "erro"


async def main(limit: int) -> None:
    store = get_target_store()
    try:
        await store.ensure_schema()
    except Exception as exc:  # noqa: BLE001
        log.warning("ensure_schema: %r", exc)

    redis = None
    try:
        import redis.asyncio as aioredis
        redis = aioredis.from_url(os.environ.get("REDIS_URL", "redis://redis:6379/0"),
                                  decode_responses=True)
        await redis.ping()
    except Exception as exc:  # noqa: BLE001
        log.warning("Redis indisponível (%r) — não enfileira scans", exc)
        redis = None

    targets = await store.list_targets(status="sem_contato", limit=limit)
    log.info("=== enrich_batch: %d alvos sem_contato ===", len(targets))
    counts = {"email": 0, "profile_only": 0, "nada": 0, "erro": 0}
    for i, t in enumerate(targets, 1):
        counts[await _process_one(store, redis, t)] += 1
        if i % 50 == 0:
            log.info("progresso: %d/%d — %s", i, len(targets), counts)
    log.info("=== concluído: %s ===", counts)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Enriquecimento de alvos sem_contato (KL-50).")
    ap.add_argument("--limit", type=int, default=500, help="máx. de alvos por execução (padrão 500).")
    args = ap.parse_args()
    asyncio.run(main(args.limit))
