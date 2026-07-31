"""KL-130 — limpeza única: aposenta os alvos `unknown`+`power` do pool de elegíveis.

O Power verificou e NÃO confirmou a caixa (`unknown`) → o e-mail é irrecuperável (o KL-128
bloqueia sempre). Enquanto ficam `status='scanned'` eles voltam ao pool todo ciclo e entopem
o fetch (ordenado por `last_scan_at ASC`), consumindo as vagas dos e-mails NOVOS. Este script
marca-os `sem_contato` de uma vez (o filtro SQL do `_ALERT_ELIGIBLE_WHERE` é a defesa contínua;
isto é a limpeza retroativa dos ~173 já existentes). Idempotente.

Uso (na VM, dentro do container `api` ou `discovery`):
    docker compose exec api python -m scripts.retire_unknown_power
    docker compose exec api python -m scripts.retire_unknown_power --dry-run
"""
from __future__ import annotations

import argparse
import asyncio

from discovery.store import get_target_store


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="só conta, não altera")
    args = ap.parse_args()

    store = get_target_store()
    await store.ensure_schema()

    if args.dry_run:
        # Conta sem alterar (mesmo critério do UPDATE).
        def _fn(cur):
            cur.execute(
                "SELECT COUNT(*) FROM targets WHERE email_verify_status = 'unknown' "
                "AND email_verify_source = 'power' "
                "AND status NOT IN ('sem_contato', 'descartado', 'unsubscribed')")
            return int(cur.fetchone()[0])

        n = await asyncio.to_thread(store._run, _fn)
        print(f"[retire] (dry-run) {n} alvos unknown+power seriam marcados sem_contato.")
        return

    n = await store.retire_unknown_power_targets()
    print(f"[retire] {n} alvos unknown+power → sem_contato (fora do pool de elegíveis).")


if __name__ == "__main__":
    asyncio.run(main())
