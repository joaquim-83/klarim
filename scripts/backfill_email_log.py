"""Popula o `email_log` com o histórico de `alert_log` + `rescan_log` (KL-62).

O `email_log` (log unificado de e-mails) passa a ser a fonte de contabilidade da
página Sistema. Este script migra os envios já registrados nas tabelas antigas para
que as métricas não zerem após o deploy. **Idempotente** — deduplica por
(source, to_email, sent_at, email_id); rodar 2× não duplica.

Uso (na VM):
    docker compose exec -T api python scripts/backfill_email_log.py [--dry-run]
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from discovery.store import get_target_store  # noqa: E402

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(message)s",
    handlers=[logging.FileHandler("backfill_email_log.log"), logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("backfill_email_log")


async def run(dry_run: bool) -> None:
    store = get_target_store()
    try:
        await store.ensure_schema()
    except Exception as exc:  # noqa: BLE001
        log.warning("ensure_schema: %r (seguindo)", exc)

    if dry_run:
        metrics = await store.email_metrics()
        log.info("[dry-run] email_log atual: enviados hoje=%s semana=%s mês=%s",
                 metrics.get("sent_today"), metrics.get("sent_week"), metrics.get("sent_month"))
        log.info("[dry-run] a migração copiaria alert_log + rescan_log (idempotente) — nada gravado.")
        return

    result = await store.migrate_email_log()
    metrics = await store.email_metrics()
    log.info("migração concluída: %d de alert_log + %d de rescan_log",
             result.get("alert_log", 0), result.get("rescan_log", 0))
    log.info("email_log agora: enviados hoje=%s semana=%s mês=%s (por tipo hoje=%s)",
             metrics.get("sent_today"), metrics.get("sent_week"),
             metrics.get("sent_month"), metrics.get("by_type"))


def main() -> None:
    ap = argparse.ArgumentParser(description="Backfill do email_log (KL-62).")
    ap.add_argument("--dry-run", action="store_true", help="mostra o panorama sem gravar.")
    args = ap.parse_args()
    asyncio.run(run(args.dry_run))


if __name__ == "__main__":
    main()
