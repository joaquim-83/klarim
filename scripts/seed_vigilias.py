"""Seed KL-44 P2: cria as vigílias core para os sites já monitorados.

Para cada (usuário, site monitorado), cria as vigílias (ssl/domain/score/email/
reputation) que o **plano** da conta permite, com `next_check_at=now` (verificação no
próximo ciclo do worker). Idempotente (`upsert_vigilia`).

**Começa PAUSADO:** antes de semear, este script grava a pausa do worker `vigilia` no
`worker_control` — assim, mesmo que o container discovery já esteja rodando, o worker de
vigília não dispara e-mails antes da verificação visual. O dono retoma via MCP
(`resume_worker vigilia`) após conferir o painel.

Uso: docker compose exec -T api python scripts/seed_vigilias.py [--dry-run] [--no-pause]
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from discovery.store import get_target_store  # noqa: E402
from discovery import worker_control  # noqa: E402
from api import plans  # noqa: E402
from api.vigilias import VIGILIA_TYPES  # noqa: E402


async def _allowed_types(user_id: int, cache: dict) -> list:
    if user_id in cache:
        return cache[user_id]
    try:
        sub = await plans.get_subscription(user_id)
        plan = sub.get("plan") or {}
        allowed = [t for t in VIGILIA_TYPES if plan.get(f"vigilia_{t}")]
    except Exception as exc:  # noqa: BLE001
        print(f"[seed-vigilia] plano indisponível user={user_id}: {exc!r}", flush=True)
        allowed = []
    cache[user_id] = allowed
    return allowed


async def run(dry_run: bool, pause: bool) -> None:
    store = get_target_store()
    await store.ensure_schema()  # garante as tabelas vigilias/vigilia_alerts

    if pause and not dry_run:
        try:
            worker_control.pause("vigilia", by="seed")
            print("[seed-vigilia] worker 'vigilia' PAUSADO (worker_control) — "
                  "retome via MCP após a verificação.", flush=True)
        except Exception as exc:  # noqa: BLE001
            print(f"[seed-vigilia] falha ao pausar (seguindo): {exc!r}", flush=True)

    sites = await store.get_all_monitored_sites()
    now = datetime.now(timezone.utc)
    cache: dict = {}
    created = 0
    skipped_no_plan = 0
    for row in sites:
        user_id = row["user_id"]
        domain = (row.get("site_domain") or "").strip()
        if not domain:
            continue
        allowed = await _allowed_types(user_id, cache)
        if not allowed:
            skipped_no_plan += 1
            continue
        if dry_run:
            created += len(allowed)
            continue
        for tipo in allowed:
            await store.upsert_vigilia(user_id, domain, tipo, next_check_at=now)
            created += 1

    tag = "[dry-run] " if dry_run else ""
    print(f"[seed-vigilia] {tag}sites monitorados: {len(sites)} · vigílias "
          f"{'seriam criadas' if dry_run else 'criadas/atualizadas'}: {created} · "
          f"pares sem plano com vigília: {skipped_no_plan}", flush=True)


def main() -> None:
    ap = argparse.ArgumentParser(description="Seed de vigílias core (KL-44 P2).")
    ap.add_argument("--dry-run", action="store_true", help="Só conta, não grava.")
    ap.add_argument("--no-pause", action="store_true",
                    help="Não pausa o worker de vigília antes de semear.")
    args = ap.parse_args()
    asyncio.run(run(args.dry_run, pause=not args.no_pause))


if __name__ == "__main__":
    main()
