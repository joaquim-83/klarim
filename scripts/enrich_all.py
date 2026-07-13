#!/usr/bin/env python3
"""Reprocessamento COMPLETO de perfis com IA (extensão do KL-50 + KL-47A).

Diferente do `enrich_batch.py` (que só cobre `sem_contato`), este script cobre
**todos** os alvos acessíveis (não `descartado`) que ainda precisam de
enriquecimento, em 3 grupos disjuntos, do mais para o menos prioritário:

  • **Grupo 1 — sem perfil:** nunca passaram pelo profiler (KL-50). Prioridade
    interna: `alerted` > `scanned` > `sem_contato` > `discovered`.
  • **Grupo 2 — sem IA:** têm perfil mas classificação fraca (`outro`/confiança
    baixa) e não vieram da IA — a IA pode acertar o setor (inclui cauda longa).
  • **Grupo 3 — sem descrição:** têm perfil + setor por IA mas sem descrição — a
    IA gera a descrição do negócio.

Para cada alvo: multi-page crawl (profiler) → build_profile → IA (setor +
descrição + contatos). Se achar e-mail (com MX) num alvo `sem_contato`, ele volta
a `discovered` e é enfileirado para scan.

**Idempotente** (pode rodar N vezes; a seleção nunca traz um alvo já completo),
**fail-open** (IA opcional; erro num alvo não derruba o batch) e **controla custo**
(rate limit configurável entre chamadas OpenAI).

Uso (na VM, dentro do container `api`):

    # 500 alvos (padrão), todos os grupos
    docker compose exec api python scripts/enrich_all.py

    # todos, sem limite
    docker compose exec api python scripts/enrich_all.py --no-limit

    # só sem_contato (comportamento do enrich_batch antigo)
    docker compose exec api python scripts/enrich_all.py --only-sem-contato

    # só IA (assume perfil já existe — pula o crawl multi-page)
    docker compose exec api python scripts/enrich_all.py --only-ai

    # simula sem gravar
    docker compose exec api python scripts/enrich_all.py --dry-run

    # 1000 alvos, 2s entre chamadas IA
    docker compose exec api python scripts/enrich_all.py --limit 1000 --ai-delay 2

Progresso em `enrichment_all.log` (append) + stdout.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import time
from typing import Any, Dict, Optional

# Permite `python scripts/enrich_all.py` (adiciona a raiz do projeto ao path).
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scanner import profiler  # noqa: E402
from scanner.checks import dns_util  # noqa: E402
from scanner.checks.base import fetch, base_url, registrable_domain, domain_of  # noqa: E402
from discovery.store import get_target_store  # noqa: E402
from discovery.contact import (  # noqa: E402
    extract_email, _clean_email, _is_valid_email, _is_junk, email_has_mx)
from discovery.classifier import PRICE_TIERS  # noqa: E402
from discovery.cnpj import enrich_from_cnpj  # noqa: E402
from scanner.ai_enrichment import (  # noqa: E402
    AI_ENRICHMENT_ENABLED, ai_enrich, merge_ai_into_profile)

SCAN_QUEUE = os.environ.get("KLARIM_SCAN_QUEUE", "klarim:scan_queue")
AI_COST_PER_CALL = 0.001  # ~US$0,001/site (GPT-4o mini, ~3,5k tokens)
_SKIP_STATUSES = {"descartado"}

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(message)s",
    handlers=[logging.FileHandler("enrichment_all.log"), logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("enrich_all")


# --------------------------------------------------------------------------- #
# Helpers puros de decisão (testáveis offline; espelham o SQL da seleção)
# --------------------------------------------------------------------------- #

def enrichment_group(row: Dict[str, Any]) -> Optional[int]:
    """Grupo do alvo (1..4) ou ``None`` se não precisa de enriquecimento.

    Espelha o WHERE de `store.list_enrichment_candidates` para taggear cada linha
    (contagem por grupo) sem uma nova ida ao banco. KL-55: G4 = perfil + IA/manual +
    descrição mas SEM classificação CNAE (reclassificação CNAE do banco)."""
    if row.get("status") in _SKIP_STATUSES:
        return None
    if row.get("profile_id") is None:
        return 1
    source = row.get("classification_source")
    # KL-54: toda classificação por regex é revista pela IA — só preserva ai/manual.
    if source not in ("ai", "manual"):   # None/auto/domain → reclassificar
        return 2
    if source == "ai" and not (row.get("profile_description") or "").strip():
        return 3
    if not row.get("has_cnae") and (row.get("profile_description") or "").strip():
        return 4  # KL-55: completo pelo KL-54 mas sem CNAE
    return None


def needs_crawl(row: Dict[str, Any], only_ai: bool = False) -> bool:
    """Precisa de crawl se não tem perfil ou o perfil está incompleto (sem páginas
    de origem). Em `--only-ai` nunca faz crawl (assume o perfil existente)."""
    if only_ai:
        return False
    if row.get("profile_id") is None:
        return True
    if not row.get("profile_sources"):   # perfil sem extraction_sources → incompleto
        return True
    return False


def needs_ai(row: Dict[str, Any], profile: Optional[Dict[str, Any]]) -> bool:
    """Precisa de IA se a classificação veio do **regex** (não-IA, não-manual — KL-54:
    toda regex é revista), ou se o perfil (já IA/manual) **não tem descrição**. Sem
    `OPENAI_API_KEY`, a IA está desligada (retorna False)."""
    if not AI_ENRICHMENT_ENABLED:
        return False
    if row.get("classification_source") not in ("ai", "manual"):
        return True
    if profile is not None and not (profile.get("description") or "").strip():
        return True
    if not row.get("has_cnae"):   # KL-55: sem CNAE → a IA gera os códigos CNAE
        return True
    return False


def should_update_sector(row: Dict[str, Any], ai: Dict[str, Any]) -> bool:
    """A IA reclassifica **toda** classificação por regex (KL-54), desde que volte com
    setor real (≠ `outro`) e confiança ≥ 0.7. **Preserva** `manual` e `ai`."""
    if row.get("classification_source") in ("manual", "ai"):
        return False
    sector = ai.get("sector")
    if not sector or sector == "outro":
        return False
    try:
        conf = float(ai.get("sector_confidence") or 0.0)
    except (TypeError, ValueError):
        conf = 0.0
    return conf >= 0.7


# --------------------------------------------------------------------------- #
# Helpers de rede / fila
# --------------------------------------------------------------------------- #

async def _validate_ai_email(raw: Optional[str]) -> Optional[str]:
    """E-mail vindo da IA: limpa + valida formato + MX (KL-24) antes de usar."""
    e = _clean_email(raw or "")
    if not e or not _is_valid_email(e) or _is_junk(e):
        return None
    return e if await asyncio.to_thread(email_has_mx, e) else None


async def _fetch_home(url: str):
    """Baixa a homepage uma vez → (html|None, headers). Nunca levanta."""
    try:
        r = await fetch(base_url(url) + "/", method="GET", follow_redirects=True)
        html = r.text if r.status_code == 200 else None
        return html, dict(r.headers)
    except Exception:  # noqa: BLE001
        return None, {}


async def _enqueue(redis, target_id: int, url: str) -> None:
    if redis is None:
        return
    await redis.rpush(SCAN_QUEUE, json.dumps(
        {"target_id": target_id, "url": url, "source": "discovery"}))


async def _make_redis():
    try:
        import redis.asyncio as aioredis
        r = aioredis.from_url(os.environ.get("REDIS_URL", "redis://redis:6379/0"),
                              decode_responses=True)
        await r.ping()
        return r
    except Exception as exc:  # noqa: BLE001
        log.warning("Redis indisponível (%r) — scans não serão enfileirados", exc)
        return None


# --------------------------------------------------------------------------- #
# Processamento de um alvo
# --------------------------------------------------------------------------- #

async def process_target(store, redis, row: Dict[str, Any], args, stats: Dict[str, int]) -> str:
    """Enriquece um alvo: crawl + profiler + IA. Retorna um resumo legível da linha.
    Erros de I/O em cada passo são logados e não abortam o alvo."""
    tid, url = row["id"], row["url"]
    domain = row.get("domain") or registrable_domain(domain_of(url))
    parts = [f"grupo={enrichment_group(row)}"]

    homepage = None
    headers: Dict[str, str] = {}
    profile: Optional[Dict[str, Any]] = None
    found_email: Optional[str] = None

    # ---- Passo 1: crawl multi-page + profiler ----
    if needs_crawl(row, only_ai=args.only_ai):
        homepage, headers = await _fetch_home(url)
        try:
            pages = await profiler.crawl_contact_pages(
                url, homepage_html=homepage if homepage else None)
        except Exception as exc:  # noqa: BLE001
            pages = {}
            log.debug("crawl erro %s: %r", url, exc)
        if pages:
            homepage = pages.get("homepage") or homepage
            for html in pages.values():
                found_email = await extract_email(html, url, validate_mx=True)
                if found_email:
                    break
            mx = await asyncio.to_thread(dns_util.resolve_mx, domain)
            ns = await asyncio.to_thread(dns_util.resolve_ns, domain)
            try:
                profile = await profiler.build_profile(
                    url, homepage_html=homepage, headers=headers,
                    mx_records=mx, ns_records=ns)
            except Exception as exc:  # noqa: BLE001
                log.warning("build_profile erro %s: %r", url, exc)
            parts.append(f"crawl OK ({len(pages)}p)")
            stats["crawled"] += 1
        else:
            parts.append("crawl FAIL")
            stats["crawl_err"] += 1

    # ---- Perfil existente (only_ai, ou crawl não rodou/falhou) ----
    if profile is None:
        try:
            profile = await store.get_site_profile(tid)
        except Exception as exc:  # noqa: BLE001
            log.warning("get_site_profile erro %s: %r", url, exc)

    # ---- Passo 2: IA (setor + descrição + contatos) ----
    ai_email = None
    if needs_ai(row, profile):
        if homepage is None:                 # only_ai / sem crawl → precisa do texto
            homepage, _ = await _fetch_home(url)
        if homepage:
            try:
                ai = await ai_enrich(domain, homepage, current_profile=profile or {})
            except Exception as exc:  # noqa: BLE001
                ai = None
                log.warning("ai_enrich erro %s: %r", url, exc)
            stats["ai_calls"] += 1
            if ai:
                sector = ai.get("sector")
                conf = float(ai.get("sector_confidence") or 0.0)
                if profile is not None:
                    merge_ai_into_profile(profile, ai)
                if should_update_sector(row, ai):
                    try:
                        await store.ai_update_classification(
                            tid, sector, PRICE_TIERS.get(sector, "standard"), conf)
                        stats["reclassified"] += 1
                        parts.append(f"IA setor={sector} conf={conf:.2f}")
                    except Exception as exc:  # noqa: BLE001
                        log.warning("ai_update erro %s: %r", url, exc)
                else:
                    parts.append(f"IA setor={sector} conf={conf:.2f} (mantido)")
                if not found_email:
                    ai_email = await _validate_ai_email(
                        (ai.get("contacts_found") or {}).get("email"))
                    if ai_email:
                        parts.append("e-mail via IA")
                # KL-55: grava as classificações CNAE da IA (source='ai'; a Receita,
                # se rodar depois, tem precedência e não é sobrescrita).
                cnaes = ai.get("cnaes") or []
                if cnaes:
                    try:
                        await store.upsert_target_classifications(tid, [
                            {"cnae_code": c.get("code"), "cnae_description": c.get("description"),
                             "cnae_section": c.get("section"), "cnae_division": c.get("division"),
                             "confidence": c.get("confidence", 0.0), "source": "ai", "rank": i + 1}
                            for i, c in enumerate(cnaes)])
                        stats["cnae_ai"] += 1
                        parts.append(f"CNAE IA ({len(cnaes)})")
                    except Exception as exc:  # noqa: BLE001
                        log.warning("cnae ai erro %s: %r", url, exc)
            await asyncio.sleep(args.ai_delay)  # rate limit OpenAI
        else:
            parts.append("IA skip (sem HTML)")

    # ---- Passo 2b: CNPJ → Receita Federal (CNAEs oficiais, KL-55) ----
    cnpj = (profile or {}).get("cnpj")
    if cnpj:
        try:
            if not await store.has_receita_cnae(tid):
                n = await enrich_from_cnpj(cnpj, store, tid)
                if n:
                    stats["cnae_receita"] += 1
                    parts.append(f"CNAE Receita ({n})")
                    await asyncio.sleep(args.cnpj_delay)  # rate limit Receita
        except Exception as exc:  # noqa: BLE001
            log.warning("cnpj/receita erro %s: %r", url, exc)

    # ---- Passo 3: gravar o perfil ----
    if profile:
        try:
            await store.upsert_site_profile(tid, profile)
            stats["profiles"] += 1
            parts.append("perfil salvo")
        except Exception as exc:  # noqa: BLE001
            log.warning("upsert perfil erro %s: %r", url, exc)

    # ---- Passo 4: e-mail novo → reativa o sem_contato ----
    final_email = found_email or ai_email
    if final_email and row.get("status") == "sem_contato":
        try:
            await store.update_target_email(tid, final_email)   # sem_contato -> discovered
            await _enqueue(redis, tid, url)
            stats["emails"] += 1
            parts.append(f"e-mail {final_email} → discovered (enfileirado)")
        except Exception as exc:  # noqa: BLE001
            log.warning("update_target_email erro %s: %r", url, exc)

    return " | ".join(parts)


# --------------------------------------------------------------------------- #
# Orquestração
# --------------------------------------------------------------------------- #

def _print_summary(stats: Dict[str, int], groups: Dict[str, int],
                   elapsed: float, dry_run: bool) -> None:
    mins, secs = divmod(int(elapsed), 60)
    log.info("---")
    log.info("Resumo:")
    log.info("  Processados:                %d", stats["processed"])
    log.info("  Grupos (G1/G2/G3/G4):       %d / %d / %d / %d",
             stats["group1"], stats["group2"], stats["group3"], stats["group4"])
    log.info("  Crawls OK:                  %d", stats["crawled"])
    log.info("  Erros de crawl:             %d", stats["crawl_err"])
    log.info("  Perfis gravados:            %d", stats["profiles"])
    log.info("  Chamadas IA:                %d", stats["ai_calls"])
    log.info("  Setores reclassif. (IA):    %d", stats["reclassified"])
    log.info("  CNAE via IA:                %d", stats["cnae_ai"])
    log.info("  CNAE via Receita:           %d", stats["cnae_receita"])
    log.info("  E-mails novos → discovered: %d", stats["emails"])
    log.info("  Erros:                      %d", stats["erros"])
    log.info("  Custo IA estimado:          ~US$%.2f", stats["ai_calls"] * AI_COST_PER_CALL)
    log.info("  Tempo:                      %dmin %ds", mins, secs)
    if dry_run:
        log.info("  (dry-run — nada foi gravado)")


async def main(args) -> None:
    store = get_target_store()
    try:
        await store.ensure_schema()
    except Exception as exc:  # noqa: BLE001
        log.warning("ensure_schema: %r", exc)

    redis = None if args.dry_run else await _make_redis()

    mode = ("sem_contato" if args.only_sem_contato
            else "only_ai" if args.only_ai else "all")
    limit = None if args.no_limit else args.limit

    try:
        groups = await store.count_enrichment_groups(mode)
    except Exception as exc:  # noqa: BLE001
        log.warning("count_enrichment_groups: %r", exc)
        groups = {"group1": 0, "group2": 0, "group3": 0, "group4": 0, "total": 0}

    log.info("=== enrich_all (mode=%s, limit=%s) ===",
             mode, "sem limite" if limit is None else limit)
    log.info("Grupo 1 (sem perfil):       %d", groups["group1"])
    log.info("Grupo 2 (sem IA):           %d", groups["group2"])
    log.info("Grupo 3 (sem descrição):    %d", groups["group3"])
    log.info("Grupo 4 (sem CNAE):         %d", groups.get("group4", 0))
    log.info("Backlog total: %d — processando %s",
             groups["total"], "todos" if limit is None else f"até {limit}")
    if not AI_ENRICHMENT_ENABLED:
        log.warning("OPENAI_API_KEY ausente — IA desligada (só crawl/profiler).")

    targets = await store.list_enrichment_candidates(limit=limit, mode=mode)
    log.info("Selecionados: %d alvos", len(targets))
    log.info("---")

    stats = {k: 0 for k in ("processed", "crawled", "crawl_err", "profiles",
                            "ai_calls", "reclassified", "cnae_ai", "cnae_receita",
                            "emails", "erros", "group1", "group2", "group3", "group4")}
    t0 = time.monotonic()

    for i, row in enumerate(targets, 1):
        grp = enrichment_group(row) or 0
        stats[f"group{grp}"] = stats.get(f"group{grp}", 0) + 1

        if args.dry_run:
            do_crawl = needs_crawl(row, only_ai=args.only_ai)
            profile_proxy = ({"description": row.get("profile_description")}
                             if row.get("profile_id") is not None else None)
            ai_planned = AI_ENRICHMENT_ENABLED and (do_crawl or needs_ai(row, profile_proxy))
            log.info("[dry-run] %d/%d %s: grupo=%s crawl=%s ia=%s",
                     i, len(targets), row["url"], grp,
                     "sim" if do_crawl else "não", "sim" if ai_planned else "não")
            stats["processed"] += 1
            continue

        try:
            summary = await process_target(store, redis, row, args, stats)
            stats["processed"] += 1
            log.info("%d/%d %s: %s", i, len(targets), row["url"], summary)
        except Exception as exc:  # noqa: BLE001
            stats["erros"] += 1
            log.warning("%d/%d %s: ERRO %r", i, len(targets), row["url"], exc)

        if i % 50 == 0:
            log.info("... progresso %d/%d", i, len(targets))

    _print_summary(stats, groups, time.monotonic() - t0, args.dry_run)

    if redis is not None:
        try:
            await redis.aclose()
        except Exception:  # noqa: BLE001
            pass
    return stats


def _parse_args(argv=None):
    ap = argparse.ArgumentParser(
        description="Reprocessamento completo de perfis com IA (KL-50 + KL-47A).")
    ap.add_argument("--limit", type=int, default=500,
                    help="máx. de alvos por execução (padrão 500).")
    ap.add_argument("--no-limit", action="store_true",
                    help="processa todos os alvos pendentes (ignora --limit).")
    ap.add_argument("--only-sem-contato", action="store_true",
                    help="restringe a alvos sem_contato (comportamento do enrich_batch).")
    ap.add_argument("--only-ai", action="store_true",
                    help="só IA (pula o crawl multi-page; assume perfil existente).")
    ap.add_argument("--dry-run", action="store_true",
                    help="mostra o que faria, sem crawl/IA/gravação.")
    ap.add_argument("--ai-delay", type=float, default=1.0,
                    help="segundos entre chamadas OpenAI (rate limit; padrão 1.0).")
    ap.add_argument("--cnpj-delay", type=float, default=20.0,
                    help="segundos entre consultas de CNPJ à Receita (KL-55; padrão 20).")
    return ap.parse_args(argv)


if __name__ == "__main__":
    asyncio.run(main(_parse_args()))
