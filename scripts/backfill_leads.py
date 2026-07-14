"""Popula a tabela `scan_leads` a partir dos scans existentes (KL-61).

Agrega `scans.scanned_by_email` (não nulo) → 1 lead por e-mail, cruza com `users`
(has_account) e `user_sites` (has_monitoring), calcula `is_corporate_email` e o
`lead_score`/`classification` (via `api.lead_scoring`), e faz UPSERT idempotente
(ON CONFLICT (email) DO UPDATE) — rodar 2× não duplica e preserva tags/notes/opted_out.

Uso (na VM):
    docker compose exec -T api python scripts/backfill_leads.py [--dry-run]
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
    handlers=[logging.FileHandler("backfill_leads.log"), logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("backfill_leads")


async def run(dry_run: bool) -> None:
    store = get_target_store()
    try:
        await store.ensure_schema()
    except Exception as exc:  # noqa: BLE001
        log.warning("ensure_schema: %r (seguindo)", exc)

    if dry_run:
        stats = await store.lead_stats()
        log.info("[dry-run] leads atuais: total=%d por classificação=%s",
                 stats.get("total", 0), stats.get("by_classification"))
        log.info("[dry-run] o backfill agregaria os scans com scanned_by_email e faria "
                 "UPSERT idempotente — nada gravado.")
        return

    n = await store.backfill_leads()
    stats = await store.lead_stats()
    log.info("backfill concluído: %d leads processados", n)
    log.info("  total=%d por classificação=%s com conta=%d com monitoramento=%d "
             "corporativos=%d multi-scan=%d",
             stats.get("total", 0), stats.get("by_classification"),
             stats.get("with_account", 0), stats.get("with_monitoring", 0),
             stats.get("corporate_emails", 0), stats.get("multi_scan", 0))


def main() -> None:
    ap = argparse.ArgumentParser(description="Backfill de leads a partir dos scans (KL-61).")
    ap.add_argument("--dry-run", action="store_true", help="mostra o panorama sem gravar.")
    args = ap.parse_args()
    asyncio.run(run(args.dry_run))


if __name__ == "__main__":
    main()
