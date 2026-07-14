"""Enfileira o backlog de alvos `sem_contato` para scan (KL-60).

Antes do KL-60 o discovery só enfileirava sites COM e-mail, então ~7.8k alvos
`sem_contato` nunca foram escaneados. Agora o scan é desacoplado do e-mail (todo
site acessível gera perfil/landing/ranking). Este script drena esse backlog em
BATCHES — nunca enfileira tudo de uma vez (a 50-100 scans/hora, milhares levariam
dias; encher a fila de uma vez não ajuda e infla o queue depth).

Seleciona alvos `status='sem_contato'` com `last_scan_id IS NULL` (nunca escaneados),
ordenados por id, e faz `rpush` na fila Redis `klarim:scan_queue` (mesmo formato do
discovery worker: `{target_id, url, source: "discovery"}` → tier gratuito, 15 checks).
O scan worker promove o alvo a `scanned` ao completar (`update_scan_result`); se o
enrich achar e-mail depois, o alvo é atualizado. Idempotente: um alvo já enfileirado
que ainda não escaneou continua `sem_contato`/`last_scan_id NULL` e pode reaparecer —
por isso rode em cadência (ex.: 1×/dia) e deixe o worker drenar entre execuções.

Uso (na VM):
    docker compose exec -T api python scripts/enqueue_unscanned.py [--limit 500]
    docker compose exec -T api python scripts/enqueue_unscanned.py --dry-run
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys

# Permite `python scripts/enqueue_unscanned.py` (adiciona a raiz do projeto ao path).
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from discovery.store import get_target_store  # noqa: E402

SCAN_QUEUE = os.environ.get("KLARIM_SCAN_QUEUE", "klarim:scan_queue")

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(message)s",
    handlers=[logging.FileHandler("enqueue_unscanned.log"), logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("enqueue")


async def _make_redis():
    try:
        import redis.asyncio as aioredis
        r = aioredis.from_url(os.environ.get("REDIS_URL", "redis://redis:6379/0"),
                              decode_responses=True)
        await r.ping()
        return r
    except Exception as exc:  # noqa: BLE001
        log.warning("Redis indisponível (%r) — nada enfileirado", exc)
        return None


async def run(limit: int, status: str, dry_run: bool) -> None:
    store = get_target_store()
    try:
        await store.ensure_schema()
    except Exception as exc:  # noqa: BLE001
        log.warning("ensure_schema: %r (seguindo)", exc)

    remaining = await store.count_unscanned_targets(status)
    targets = await store.list_unscanned_targets(limit=limit, status=status)
    log.info("backlog '%s' sem scan: %d total; processando %d neste batch%s",
             status, remaining, len(targets), " (dry-run)" if dry_run else "")
    if not targets:
        log.info("nada a enfileirar.")
        return

    redis = None if dry_run else await _make_redis()
    if not dry_run and redis is None:
        log.error("sem Redis — abortando (nada enfileirado).")
        return

    enqueued = 0
    try:
        for t in targets:
            tid, url = t["id"], t["url"]
            if dry_run:
                log.info("[dry-run] enfileiraria target %s (%s)", tid, url)
                enqueued += 1
                continue
            try:
                await redis.rpush(SCAN_QUEUE, json.dumps(
                    {"target_id": tid, "url": url, "source": "discovery"}))
                enqueued += 1
            except Exception as exc:  # noqa: BLE001 - um item ruim não derruba o batch
                log.warning("falha ao enfileirar target %s (%r)", tid, exc)
            if enqueued % 100 == 0:
                log.info("… %d/%d enfileirados", enqueued, len(targets))
    finally:
        if redis is not None:
            try:
                await redis.aclose()
            except Exception:  # noqa: BLE001
                pass

    log.info("concluído: %d enfileirados (backlog restante ~%d). "
             "Rode de novo após o worker drenar a fila.", enqueued, max(0, remaining - enqueued))


def _parse_args(argv=None):
    ap = argparse.ArgumentParser(
        description="Enfileira o backlog de alvos sem scan para a fila Redis (KL-60).")
    ap.add_argument("--limit", type=int, default=500,
                    help="máx. de alvos por execução (padrão 500 — não encha a fila de uma vez).")
    ap.add_argument("--status", default="sem_contato",
                    help="status alvo (padrão sem_contato).")
    ap.add_argument("--dry-run", action="store_true",
                    help="mostra o que faria, sem enfileirar nada.")
    return ap.parse_args(argv)


def main() -> None:
    args = _parse_args()
    asyncio.run(run(args.limit, args.status, args.dry_run))


if __name__ == "__main__":
    main()
