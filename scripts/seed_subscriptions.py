"""Seed KL-44 (Guardião Digital, P1): assinaturas para contas existentes + backfill
de leads a partir das contas.

- Toda conta (users) sem assinatura recebe **Pro trial** (se criada há < 30 dias,
  trial_ends_at = created_at + 30d) ou **Free** (se >= 30 dias). Idempotente.
- Cria `scan_leads` para contas que entraram via alerta→signup **sem scan público**
  (o backfill do KL-61 só cobria `scans.scanned_by_email`).

Uso: docker compose exec -T api python scripts/seed_subscriptions.py [--dry-run]
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from discovery.store import get_target_store  # noqa: E402
from api import plans  # noqa: E402


async def run(dry_run: bool) -> None:
    store = get_target_store()
    await store.ensure_schema()  # garante que plans/subscriptions existem + seed dos 3 planos
    if dry_run:
        rows = await store.users_without_subscription()
        print(f"[seed][dry-run] {len(rows)} conta(s) sem assinatura seriam processadas.", flush=True)
        return
    sub = await plans.seed_existing_accounts()
    print(f"[seed] assinaturas criadas: {sub}", flush=True)
    leads = await store.backfill_leads_from_accounts()
    print(f"[seed] leads de contas criados: {leads}", flush=True)


def main() -> None:
    ap = argparse.ArgumentParser(description="Seed de assinaturas + backfill de leads (KL-44).")
    ap.add_argument("--dry-run", action="store_true", help="Só conta, não grava.")
    args = ap.parse_args()
    asyncio.run(run(args.dry_run))


if __name__ == "__main__":
    main()
