"""KL-85 Parte 1 — backfill do `alert_quality_score` para TODOS os alvos com contact_email.

Só cálculo sobre dados existentes (sem re-scan). Batch de 500, com progresso. Ao final,
imprime a distribuição (histograma por faixas) para calibrar o threshold ANTES do worker usar.

Uso (na VM, dentro do container): python -m scripts.backfill_alert_scores
"""

from __future__ import annotations

import asyncio
import os

from discovery.store import get_target_store
from discovery.alert_scoring import calculate_alert_score

BATCH = 500
THRESHOLD = int(os.environ.get("ALERT_SCORE_THRESHOLD", "20"))


async def _domain_bounced(store, cache: dict, domain: str) -> bool:
    dom = (domain or "").strip().lower()
    if not dom:
        return False
    if dom not in cache:
        try:
            cache[dom] = await store.domain_has_bounce(dom)
        except Exception:  # noqa: BLE001
            cache[dom] = False
    return cache[dom]


async def main() -> None:
    store = get_target_store()
    await store.ensure_schema()
    offset, total = 0, 0
    bins = [(-40, -20), (-20, 0), (0, 20), (20, 40), (40, 60), (60, 80), (80, 200)]
    hist = {b: 0 for b in bins}
    qualified = low = disq = 0
    bounce_cache: dict = {}

    while True:
        rows = await store.targets_with_email_for_scoring(offset, BATCH)
        if not rows:
            break
        for t in rows:
            bounced = await _domain_bounced(store, bounce_cache, (t.get("contact_email") or "").rsplit("@", 1)[-1])
            score = calculate_alert_score(t, t.get("contact_email"), bounced)["score"]
            await store.update_target_alert_score(t["id"], score)
            for lo, hi in bins:
                if lo <= score < hi:
                    hist[(lo, hi)] += 1
                    break
            if score >= THRESHOLD:
                qualified += 1
            elif score >= 0:
                low += 1
            else:
                disq += 1
        total += len(rows)
        offset += BATCH
        print(f"[backfill] {total} alvos processados…", flush=True)

    def pct(n):
        return f"{(n / total * 100):.1f}%" if total else "0%"

    print("\n===== KL-85 backfill de alert_quality_score =====")
    print(f"Total targets com email: {total:,}".replace(",", "."))
    print(f"Score >= {THRESHOLD} (enviaria):    {qualified:,} ({pct(qualified)})".replace(",", "."))
    print(f"Score 0-{THRESHOLD - 1} (não enviaria): {low:,} ({pct(low)})".replace(",", "."))
    print(f"Score < 0 (desqualificado):  {disq:,} ({pct(disq)})".replace(",", "."))
    print("\nDistribuição:")
    for (lo, hi), n in hist.items():
        print(f"  [{lo:>3}, {hi:>3}): {n:,}".replace(",", "."))


if __name__ == "__main__":
    asyncio.run(main())
