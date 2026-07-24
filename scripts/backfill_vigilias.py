"""KL-106 — backfill de vigílias: para TODO site monitorado de conta ativa, cria as vigílias que
o plano permite (ATIVAS) e reativa as que estejam desabilitadas. Idempotente; roda 1x no deploy.
Respeita o plano (não cria vigília Pro/Agency para conta Free).

    docker compose exec api python -m scripts.backfill_vigilias           # aplica
    docker compose exec api python -m scripts.backfill_vigilias --dry-run # só conta
"""

from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timezone

from api import plans
from api.vigilias import VIGILIA_TYPES
from discovery.store import get_target_store


async def _allowed_types(user_id: int) -> list:
    try:
        sub = await plans.get_subscription(user_id)
    except Exception as exc:  # noqa: BLE001
        print(f"  plano indisponível u={user_id}: {exc!r}")
        return []
    plan = sub.get("plan") or {}
    return [t for t in VIGILIA_TYPES if plan.get(f"vigilia_{t}")]


async def main(dry: bool = False) -> None:
    store = get_target_store()

    def _q(cur):
        cur.execute(
            "SELECT DISTINCT us.user_id, t.domain FROM user_sites us "
            "JOIN targets t ON t.id = us.target_id "
            "JOIN users u ON u.id = us.user_id "
            "WHERE u.is_active = TRUE AND t.domain IS NOT NULL")
        return cur.fetchall()

    pairs = await asyncio.to_thread(store._run, _q)
    now = datetime.now(timezone.utc)
    created = reactivated = 0
    for uid, domain in pairs:
        allowed = set(await _allowed_types(uid))
        existing = {v["tipo"]: v for v in await store.list_site_vigilias(uid, domain)}
        for tipo in allowed:
            v = existing.get(tipo)
            if v is None:
                if not dry:
                    await store.set_vigilia_enabled(uid, domain, tipo, True, next_check_at=now)
                created += 1
            elif not v.get("enabled"):
                if not dry:
                    await store.set_vigilia_enabled(uid, domain, tipo, True, next_check_at=now)
                reactivated += 1
    tag = " (dry-run)" if dry else ""
    print(f"[backfill-vigilias] sites={len(pairs)} criadas={created} reativadas={reactivated}{tag}")


if __name__ == "__main__":
    asyncio.run(main("--dry-run" in sys.argv))
