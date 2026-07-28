"""KL-110 — limpeza retroativa do backlog de e-mails não verificados.

Roda em 2 fases sobre os alvos com `contact_email` ainda NÃO verificado:
  • **Fase 0 (local, custo zero):** sintaxe + descartável + MX (`verify_local`). Blocklista os
    `invalid`/`disposable`, grava `email_verify_status`, flag role-based. Corta a maior parte.
  • **Fase 1 (API Reoon, pago):** verifica em LOTE os sobreviventes (bulk task da Reoon).
    Blocklista `invalid`/`disabled`/`disposable`/`spamtrap`; grava o status dos demais. Só roda
    se `REOON_API_KEY` estiver configurada; limitada por `--api-limit` (controle de custo).

NÃO re-escaneia nada; só verifica deliverability. Idempotente (re-rodar pula os já verificados).

Uso (na VM, dentro do container `api`):
    docker compose exec api python -m scripts.cleanup_email_backlog                # fase 0 + 1
    docker compose exec api python -m scripts.cleanup_email_backlog --local-only   # só fase 0
    docker compose exec api python -m scripts.cleanup_email_backlog --api-limit 2000
    docker compose exec api python -m scripts.cleanup_email_backlog --dry-run
"""
from __future__ import annotations

import argparse
import asyncio
import os
from collections import Counter

import httpx

from discovery.store import get_target_store
from notifier import email_verifier

BATCH = 500
REOON_BASE = email_verifier.REOON_BASE_URL


async def _redis():
    try:
        import redis.asyncio as aioredis
        return aioredis.from_url(
            os.environ.get("REDIS_URL", "redis://redis:6379/0"), decode_responses=True)
    except Exception:  # noqa: BLE001
        return None


async def phase0_local(store, redis, dry_run: bool) -> tuple[Counter, list]:
    """Fase 0: verificação local de TODO o backlog. Devolve (contadores, sobreviventes)."""
    counts: Counter = Counter()
    survivors: list = []
    offset = 0
    while True:
        rows = await store.targets_needing_email_verification(limit=BATCH, offset=offset)
        if not rows:
            break
        for t in rows:
            email = (t.get("contact_email") or "").strip().lower()
            if not email:
                continue
            res = await email_verifier.verify_local(email, redis)
            counts[f"local_{res.status}"] += 1
            if res.is_role_based:
                counts["role_based"] += 1
            if res.status in email_verifier.BLOCK_STATUSES:
                counts["blocked"] += 1
                if not dry_run:
                    await store.block_email(email, reason=f"verify_{res.status}")
                    await store.update_status(t["id"], "descartado")
                    await store.update_target_email_verification(
                        t["id"], res.status, res.is_role_based, source="local")  # KL-125
                continue
            # 'valid' → sobrevive para a Fase 1 (API). Grava o status local por ora.
            if not dry_run:
                await store.update_target_email_verification(
                    t["id"], res.status, res.is_role_based, verified=False, source="local")  # KL-125
            survivors.append({"id": t["id"], "email": email,
                              "role": res.is_role_based})
        offset += BATCH
        print(f"[cleanup] fase 0: {offset} avaliados, {counts['blocked']} bloqueados, "
              f"{len(survivors)} sobreviventes…", flush=True)
    return counts, survivors


async def _reoon_bulk(emails: list[str], api_key: str) -> dict:
    """Cria uma bulk task na Reoon, faz poll até completar e devolve {email: status}."""
    out: dict = {}
    async with httpx.AsyncClient(timeout=60.0) as c:
        resp = await c.post(f"{REOON_BASE}/create-bulk-verification-task/", json={
            "name": "klarim-backlog-cleanup", "emails": emails, "key": api_key})
        resp.raise_for_status()
        task_id = resp.json().get("task_id") or resp.json().get("id")
        if not task_id:
            raise RuntimeError(f"bulk task sem id: {resp.text[:200]}")
        # Poll (a Reoon processa assincronamente; power pode levar minutos p/ milhares).
        for _ in range(180):  # ~30 min máx (10s por poll)
            await asyncio.sleep(10)
            r = await c.get(f"{REOON_BASE}/get-result-bulk-verification-task/",
                            params={"key": api_key, "task_id": task_id})
            r.raise_for_status()
            data = r.json()
            if (data.get("status") or "").lower() in ("completed", "complete", "finished"):
                results = data.get("results") or data.get("emails") or {}
                if isinstance(results, dict):
                    for email, entry in results.items():
                        st = entry.get("status") if isinstance(entry, dict) else entry
                        out[email.lower()] = email_verifier._map_reoon_status(st)
                elif isinstance(results, list):
                    for entry in results:
                        email = (entry.get("email") or "").lower()
                        if email:
                            out[email] = email_verifier._map_reoon_status(entry.get("status"))
                return out
    return out


async def phase1_api(store, survivors: list, api_limit: int, dry_run: bool) -> Counter:
    """Fase 1: verificação em lote (Reoon) dos sobreviventes da Fase 0."""
    counts: Counter = Counter()
    api_key = os.environ.get("REOON_API_KEY")
    if not api_key:
        print("[cleanup] REOON_API_KEY ausente — pulando a Fase 1 (API).", flush=True)
        return counts
    batch = survivors[:api_limit]
    if not batch:
        return counts
    by_email = {s["email"]: s for s in batch}
    print(f"[cleanup] fase 1: enviando {len(batch)} e-mails para a Reoon (bulk)…", flush=True)
    try:
        results = await _reoon_bulk([s["email"] for s in batch], api_key)
    except Exception as exc:  # noqa: BLE001
        print(f"[cleanup] fase 1 falhou (segue): {exc!r}", flush=True)
        return counts
    for email, status in results.items():
        s = by_email.get(email)
        if not s:
            continue
        counts[f"api_{status}"] += 1
        if status in email_verifier.BLOCK_STATUSES:
            counts["blocked"] += 1
            if not dry_run:
                await store.block_email(email, reason=f"verify_{status}")
                await store.update_status(s["id"], "descartado")
        if not dry_run:
            # KL-125: source='bulk' — a Bulk API é menos precisa p/ servidores BR; o alert
            # worker reverifica os `unknown` via Power antes de enviar.
            await store.update_target_email_verification(s["id"], status, s["role"], source="bulk")
    print(f"[cleanup] fase 1: {len(results)} verificados, {counts['blocked']} bloqueados.",
          flush=True)
    return counts


def _report(total0: int, c0: Counter, c1: Counter, survivors: int) -> None:
    print("\n" + "=" * 60)
    print("RELATÓRIO — limpeza do backlog de e-mails (KL-110)")
    print("=" * 60)
    print(f"Backlog avaliado (Fase 0 local): {total0}")
    for k in sorted(c0):
        if k.startswith("local_"):
            print(f"  {k.replace('local_', 'local '):<22} {c0[k]}")
    print(f"  {'role-based (flag)':<22} {c0.get('role_based', 0)}")
    print(f"Bloqueados na Fase 0: {c0.get('blocked', 0)}")
    print(f"Sobreviventes → Fase 1: {survivors}")
    if c1:
        print("\nFase 1 (API Reoon):")
        for k in sorted(c1):
            if k.startswith("api_"):
                print(f"  {k.replace('api_', 'api '):<22} {c1[k]}")
        print(f"Bloqueados na Fase 1: {c1.get('blocked', 0)}")
    total_blocked = c0.get("blocked", 0) + c1.get("blocked", 0)
    print(f"\nTOTAL bloqueados: {total_blocked}")
    print("=" * 60)


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--local-only", action="store_true", help="só a Fase 0 (sem API)")
    ap.add_argument("--api-limit", type=int, default=5000, help="máx de e-mails na Fase 1")
    ap.add_argument("--dry-run", action="store_true", help="não grava nada, só conta")
    args = ap.parse_args()

    store = get_target_store()
    await store.ensure_schema()
    redis = await _redis()

    c0, survivors = await phase0_local(store, redis, args.dry_run)
    total0 = sum(v for k, v in c0.items() if k.startswith("local_"))
    c1: Counter = Counter()
    if not args.local_only:
        c1 = await phase1_api(store, survivors, args.api_limit, args.dry_run)
    _report(total0, c0, c1, len(survivors))
    if args.dry_run:
        print("(dry-run — nada foi gravado)")


if __name__ == "__main__":
    asyncio.run(main())
