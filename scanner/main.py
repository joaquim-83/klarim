"""Worker entry point.

Two modes:

* CLI (default): ``python -m scanner.main <url>`` runs a single scan and prints
  the report. ``--json`` prints machine-readable JSON instead.

* Queue worker: ``python -m scanner.main --worker`` blocks on a Redis list
  (``klarim:scan_queue``), pops target URLs, scans them, and stores the JSON
  report back in Redis (``klarim:report:<url>``). This is the shape consumed by
  the ``worker`` service in ``docker-compose.yml``. Redis is optional — if it is
  unavailable, the worker mode explains how to run a one-off scan instead.

The worker deliberately stays thin: it is glue around ``scanner.run_scan``.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timezone

from .runner import run_scan, format_report


SCAN_QUEUE = os.environ.get("KLARIM_SCAN_QUEUE", "klarim:scan_queue")
REPORT_PREFIX = os.environ.get("KLARIM_REPORT_PREFIX", "klarim:report:")


async def _scan_and_print(url: str, as_json: bool, as_pdf: bool) -> int:
    report = await run_scan(url)
    if as_json:
        print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
    else:
        print(format_report(report))

    if as_pdf:
        await _write_pdfs(report, url)

    # Exit non-zero when the target is in the red, so CI/cron can react.
    return 0 if (report.score and report.score.score >= 50) else 1


async def _write_pdfs(report, url: str) -> None:
    """Generate the executive + technical PDFs into the current directory."""
    # Imported lazily: pulls in weasyprint/jinja2 only when --pdf is used.
    from reporter import (
        generate_executive_pdf,
        generate_technical_pdf,
        pdf_filename,
    )

    exec_bytes = await generate_executive_pdf(report, url)
    tech_bytes = await generate_technical_pdf(report, url)
    exec_name = pdf_filename("executive", url, report.started_at)
    tech_name = pdf_filename("technical", url, report.started_at)
    with open(exec_name, "wb") as fh:
        fh.write(exec_bytes)
    with open(tech_name, "wb") as fh:
        fh.write(tech_bytes)
    print(f"\nPDFs gerados:\n  - {exec_name} ({len(exec_bytes)} bytes)\n  - {tech_name} ({len(tech_bytes)} bytes)")


async def _enrich_profile(store, target_id: int, url: str, security_score) -> None:
    """KL-50: extrai o perfil comercial (multi-page + parsers) e grava em site_profile.
    Best-effort — qualquer erro é só logado (não afeta o scan nem o score)."""
    try:
        from scanner import profiler
        from scanner.checks import dns_util
        from scanner.checks.base import fetch, base_url, registrable_domain, domain_of

        headers, homepage_html = {}, None
        try:
            hp = await fetch(base_url(url) + "/", method="GET", follow_redirects=True)
            headers = dict(hp.headers)
            homepage_html = hp.text if hp.status_code == 200 else None
        except Exception:  # noqa: BLE001
            pass
        dom = registrable_domain(domain_of(url))
        mx = await asyncio.to_thread(dns_util.resolve_mx, dom)
        ns = await asyncio.to_thread(dns_util.resolve_ns, dom)
        profile = await profiler.build_profile(
            url, homepage_html=homepage_html, headers=headers,
            mx_records=mx, ns_records=ns, security_score=security_score)

        # KL-47A: enriquecimento por IA (só se OPENAI_API_KEY configurada). Complementa o
        # regex — preenche campos vazios do perfil e refina o setor quando fraco/outro.
        await _ai_enrich_profile(store, target_id, dom, homepage_html, profile)

        await store.upsert_site_profile(target_id, profile)
        found = [k for k in ("commercial_email", "phone", "whatsapp", "cnpj", "instagram")
                 if profile.get(k)]
        print(f"[profile] {url} -> maturity {profile.get('maturity_score')} "
              f"({', '.join(found) or 'sem sinais'})", flush=True)
    except Exception as exc:  # noqa: BLE001 - enriquecimento nunca derruba o worker
        print(f"[profile] falha em {url}: {exc!r}", flush=True)


async def _ai_enrich_profile(store, target_id: int, domain: str, homepage_html, profile: dict) -> None:
    """KL-47A: enriquece com IA. Best-effort — sem chave ou erro, não faz nada."""
    try:
        from scanner.ai_enrichment import AI_ENRICHMENT_ENABLED, ai_enrich, merge_ai_into_profile
        from discovery.classifier import PRICE_TIERS
    except Exception:  # noqa: BLE001
        return
    if not AI_ENRICHMENT_ENABLED or not homepage_html:
        return
    try:
        ai = await ai_enrich(domain, homepage_html, current_profile=profile)
        if not ai:
            return
        changed = merge_ai_into_profile(profile, ai)   # só campos vazios
        sector = ai.get("sector")
        conf = float(ai.get("sector_confidence") or 0.0)
        if sector and sector != "outro" and conf > 0.7:
            tier = PRICE_TIERS.get(sector, "standard")
            await store.ai_update_classification(target_id, sector, tier, conf)  # revê regex; preserva manual/ai (KL-54)
        print(f"[ai] {domain}: sector={sector} conf={conf:.2f} "
              f"preenchidos={changed or 'nenhum'}", flush=True)
    except Exception as exc:  # noqa: BLE001 - IA nunca derruba o worker
        print(f"[ai] falha em {domain}: {exc!r}", flush=True)


def _parse_queue_item(raw: str):
    """Aceita JSON {target_id, url, source?} (workers) ou uma URL simples."""
    try:
        obj = json.loads(raw)
        if isinstance(obj, dict) and obj.get("url"):
            return obj.get("target_id"), obj["url"], obj.get("source", "discovery")
    except (ValueError, TypeError):
        pass
    return None, raw, "discovery"


async def _worker_loop() -> None:
    import redis.asyncio as aioredis

    from scanner.cache import ScanCache
    from discovery.store import get_target_store
    from discovery.heartbeat import publish_heartbeat
    from discovery import worker_control

    redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
    client = aioredis.from_url(redis_url, decode_responses=True)
    await client.ping()
    cache = ScanCache(client)
    store = get_target_store()
    try:
        await store.ensure_schema()
    except Exception as exc:  # noqa: BLE001 - segue sem persistência se o DB faltar
        print(f"[klarim-worker] targets/scans indisponível ({exc!r})", flush=True)
        store = None

    # Rate limit: no máximo N scans/hora (padrão 50 -> 72s entre scans).
    max_per_hour = int(os.environ.get("WORKER_MAX_SCANS_PER_HOUR", "50"))
    min_interval = 3600.0 / max_per_hour if max_per_hour > 0 else 0.0
    loop = asyncio.get_event_loop()
    last = 0.0
    last_scan_at = None

    async def _beat():
        try:
            qlen = await client.llen(SCAN_QUEUE)
        except Exception:  # noqa: BLE001
            qlen = None
        await publish_heartbeat("scan", {"queue_size": qlen, "last_scan_at": last_scan_at})

    print(f"[klarim-worker] conectado a {redis_url}; aguardando '{SCAN_QUEUE}'…", flush=True)
    await _beat()
    while True:
        # Controle centralizado (KL-32): pausado → não consome a fila (itens ficam
        # enfileirados), mas mantém o heartbeat vivo.
        if not worker_control.is_enabled("scan"):
            await _beat()
            await asyncio.sleep(30)
            continue
        # Throttle dinâmico (KL-32): max_per_hour do controle, senão o do env.
        mph = int(worker_control.worker_config("scan").get("max_per_hour") or max_per_hour)
        min_interval = 3600.0 / mph if mph > 0 else 0.0
        # timeout 30s: acorda pra bater o heartbeat mesmo com a fila vazia (KL-16).
        item = await client.blpop(SCAN_QUEUE, timeout=30)
        await _beat()
        if not item:
            continue
        target_id, url, source = _parse_queue_item(item[1])
        if last and min_interval:
            wait = min_interval - (loop.time() - last)
            if wait > 0:
                await asyncio.sleep(wait)
        last = loop.time()
        try:
            # KL-27: discovery/público = tier gratuito (15); admin/manual = completo (29).
            full = source not in ("discovery", "public")
            report = await run_scan(url, full=full)
            await cache.set(url, report, full=full)  # cache do KL-9 (por tier)
            s = report.score
            if store is not None and target_id is not None and s is not None:
                scan_id = await store.save_scan(
                    target_id, url, s.score, s.semaphore, s.passed, s.failed,
                    s.inconclusive, report.to_dict(), source=source)
                await store.update_scan_result(target_id, scan_id, s.score)
                # KL-50: extrai o perfil comercial (best-effort, não afeta o scan).
                await _enrich_profile(store, target_id, url, s.score if s else None)
            last_scan_at = datetime.now(timezone.utc).isoformat()
            print(f"[klarim-worker] {url} -> score {s.score if s else 'n/a'}"
                  f"{' (target ' + str(target_id) + ')' if target_id else ''}", flush=True)
        except Exception as exc:  # noqa: BLE001 - mantém o worker vivo
            print(f"[klarim-worker] erro em {url}: {exc!r}", file=sys.stderr, flush=True)


def _run_worker() -> int:
    try:
        import redis.asyncio  # noqa: F401 - garante o pacote presente
    except ImportError:
        print("redis não instalado. Instale requirements.txt.", file=sys.stderr)
        return 2
    try:
        asyncio.run(_worker_loop())
    except Exception as exc:  # noqa: BLE001
        print(f"[klarim-worker] loop falhou: {exc!r}", file=sys.stderr)
        return 2
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="klarim-scanner",
        description="Klarim passive web-security scanner (passive checks, in continuous expansion).",
    )
    parser.add_argument("url", nargs="?", help="Target URL to scan.")
    parser.add_argument(
        "--worker",
        action="store_true",
        help="Run as a Redis queue worker instead of a one-off scan.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit the report as JSON (single-scan mode).",
    )
    parser.add_argument(
        "--pdf",
        action="store_true",
        help="Also write executive + technical PDF reports to the current directory.",
    )
    args = parser.parse_args(argv)

    if args.worker:
        return _run_worker()

    if not args.url:
        parser.error("provide a URL to scan, or use --worker")

    return asyncio.run(_scan_and_print(args.url, args.json, args.pdf))


if __name__ == "__main__":
    raise SystemExit(main())
