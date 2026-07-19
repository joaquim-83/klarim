"""KL-84 — Reclassificação retroativa de setores (taxonomia aberta).

Reclassifica alvos usando as **descrições já extraídas** (`site_profile`) — SEM re-scan, SEM
tocar em score/checks. Objetivo: derrubar o 'outro' de ~15-20% para <5% reaproveitando a IA
sobre o texto que já temos. Passa cada alvo pelo mesmo `process_classification` do enrich (que
resolve sinônimo, reusa setor existente ou cria proposta), preservando `manual`/`receita`.

Uso (rodar NA VM, manualmente — nunca em CI):
    docker compose exec api python -m scripts.reclassify_sectors --dry-run
    docker compose exec api python -m scripts.reclassify_sectors --scope outro --limit 2000
    docker compose exec api python -m scripts.reclassify_sectors --scope all

Flags:
    --scope   outro (default) | all
    --dry-run não grava; só imprime o que faria
    --limit   máximo de alvos a processar (default: sem limite)
    --batch   tamanho do lote por página (default 200)

Rate limit: <=500 chamadas de IA por hora (respeita o custo/OpenAI). Sem OPENAI_API_KEY o
script aborta (nada a fazer sem o classificador).
"""

from __future__ import annotations

import argparse
import asyncio
import time
from collections import Counter

_MAX_PER_HOUR = 500
_MIN_INTERVAL = 3600.0 / _MAX_PER_HOUR  # ~7.2s entre chamadas


def _build_text(row: dict) -> str:
    """Monta o texto do negócio a partir do perfil já extraído (sem re-scan)."""
    parts = []
    if row.get("business_type"):
        parts.append(str(row["business_type"]))
    if row.get("description"):
        parts.append(str(row["description"]))
    tags = row.get("tags")
    if isinstance(tags, (list, tuple)) and tags:
        parts.append("Tags: " + ", ".join(str(t) for t in tags))
    return "\n".join(parts).strip()


async def _classify_one(row: dict, known: list) -> dict | None:
    """Chama a IA sobre o texto do perfil → dict do ai_enrich (ou None). Reusa o mesmo
    prompt/parse do enrich, mas sem HTTP de scan (o texto já existe)."""
    from scanner import ai_enrichment as ai

    text = _build_text(row)
    if len(text) < 20:  # pouco sinal → não vale a chamada
        return None
    result = await ai.call_openai(ai.build_system_prompt(known),
                                  ai.build_user_prompt(row["domain"], text), max_tokens=900)
    if not result:
        return None
    # normaliza igual ao ai_enrich (setor novo preserva slug; conhecido normaliza)
    is_new = bool(result.get("is_new_sector"))
    raw = str(result.get("sector_legacy") or result.get("sector") or "outro").strip().lower()
    import re
    if is_new and raw and raw != "outro" and raw not in ai.VALID_SECTORS:
        legacy = re.sub(r"[^a-z0-9_]", "", raw.replace(" ", "_").replace("-", "_"))[:50] or "outro"
        result["is_new_sector"] = legacy != "outro"
    else:
        from discovery.sector_taxonomy import normalize_sector
        legacy = normalize_sector(raw)
        result["is_new_sector"] = False
    result["sector"] = legacy
    try:
        result["sector_confidence"] = float(result.get("sector_confidence") or 0.0)
    except (TypeError, ValueError):
        result["sector_confidence"] = 0.0
    return result


async def run(scope: str, dry_run: bool, limit: int | None, batch: int) -> None:
    from scanner.ai_enrichment import AI_ENRICHMENT_ENABLED
    from discovery.store import get_target_store
    from discovery.sector_classification import process_classification
    from discovery.classifier import PRICE_TIERS

    if not AI_ENRICHMENT_ENABLED:
        print("OPENAI_API_KEY ausente — nada a fazer (o classificador está desligado).")
        return

    store = get_target_store()
    known = [r["slug"] for r in await store.list_sectors(["approved"])]

    stats = Counter()
    after_id, processed, calls, last_call = 0, 0, 0, 0.0
    print(f"[reclassify] scope={scope} dry_run={dry_run} limit={limit or '∞'} batch={batch}")

    while True:
        rows = await store.targets_for_reclassification(scope=scope, limit=batch, after_id=after_id)
        if not rows:
            break
        for row in rows:
            after_id = row["id"]
            if limit and processed >= limit:
                break
            processed += 1

            # rate limit: no máximo 500 chamadas/hora
            wait = _MIN_INTERVAL - (time.monotonic() - last_call)
            if wait > 0:
                await asyncio.sleep(wait)
            ai = await _classify_one(row, known)
            last_call = time.monotonic()
            calls += 1
            if not ai:
                stats["sem_sinal"] += 1
                continue
            conf = ai["sector_confidence"]
            if conf <= 0.7:
                stats["baixa_confianca"] += 1
                continue
            decision = await process_classification(store, ai)
            new_sector = decision["sector"]
            old = row.get("sector") or "outro"
            if new_sector == "outro" or new_sector == old:
                stats["inalterado"] += 1
                continue
            stats[decision["action"]] += 1
            print(f"  {row['domain']}: {old} -> {new_sector} "
                  f"({decision['action']}, conf={conf:.2f})" + (" [dry]" if dry_run else ""))
            if not dry_run:
                tier = PRICE_TIERS.get(new_sector, "standard")
                await store.reclassify_target_sector(row["id"], new_sector, tier, conf)
        if limit and processed >= limit:
            break

    if not dry_run:
        try:
            await store.recompute_sector_counts()
        except Exception as exc:  # noqa: BLE001
            print(f"[reclassify] recompute_sector_counts falhou: {exc!r}")

    print(f"\n[reclassify] processados={processed} chamadas_ia={calls}")
    for k, v in sorted(stats.items(), key=lambda x: -x[1]):
        print(f"  {k}: {v}")


def main() -> None:
    p = argparse.ArgumentParser(description="Reclassificação retroativa de setores (KL-84).")
    p.add_argument("--scope", choices=["outro", "all"], default="outro")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--batch", type=int, default=200)
    args = p.parse_args()
    asyncio.run(run(args.scope, args.dry_run, args.limit, args.batch))


if __name__ == "__main__":
    main()
