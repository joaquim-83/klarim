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
from .enrichment import enrich_profile  # KL-51 f5: perfil compartilhado (worker + API)


SCAN_QUEUE = os.environ.get("KLARIM_SCAN_QUEUE", "klarim:scan_queue")
REPORT_PREFIX = os.environ.get("KLARIM_REPORT_PREFIX", "klarim:report:")


async def persist_tech_detection(store, target_id, scan_id, response_data: dict) -> dict:
    """Detecta e grava o tech stack de um scan (KL-75) a partir do response bruto já em
    memória (headers/html/dns/ssl — sem request extra). **Resiliente:** qualquer erro é
    logado e engolido; o scan já está persistido. Retorna o resultado da detecção (ou {}).

    Compartilhado pelo scan worker e pelo backfill (`scripts/backfill_tech_stack.py`) para
    que os dois usem exatamente a mesma lógica de detecção/gravação.
    """
    if not response_data:
        return {}
    try:
        from scanner.tech_detector import (
            detect_tech_stack, classify_site_status, classify_site_type)
        result = detect_tech_stack(
            headers=response_data.get("headers") or {},
            html=response_data.get("html") or "",
            dns=response_data.get("dns") or {},
            ssl=response_data.get("ssl") or {},
        )
        if result.get("technologies"):
            await store.save_tech_stack(target_id, scan_id, result["technologies"])
        # Status autoritativo: usa o http_status REAL (o detector só vê conteúdo).
        html = response_data.get("html") or ""
        status = classify_site_status(
            response_data.get("http_status"), html,
            response_data.get("response_time_ms"), has_scripts="<script" in html.lower())
        # KL-75 P2: reclassifica o site_type com o status AUTORITATIVO (o detector usou o
        # status por conteúdo). Assim parked/abandonado/dominio_inativo refletem o real.
        signals = {s["signal"] for s in result.get("site_type_signals") or []}
        site_type = classify_site_type(result.get("technologies") or [], html, status, signals)
        result["site_type"] = site_type
        # site_type sempre vem preenchido (default institucional) → sempre atualiza.
        await store.update_target_tech_fields(
            target_id, result.get("email_provider"), result.get("related_domains"),
            site_type=site_type)
        if result.get("company_name"):
            await store.fill_empty_company_name(target_id, result["company_name"])
        await store.save_site_status(
            target_id, status, http_code=response_data.get("http_status"),
            response_time_ms=response_data.get("response_time_ms"))
        n = len(result.get("technologies") or [])
        print(f"[tech] target {target_id}: {n} techs · email={result.get('email_provider')} "
              f"status={status} type={site_type}", flush=True)
        return result
    except Exception as exc:  # noqa: BLE001 - enriquecimento nunca derruba o scan
        print(f"[tech] falha em target {target_id}: {exc!r}", flush=True)
        return {}


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
        # Throttle dinâmico (KL-32 + KL-44): worker_control > admin_settings > env.
        mph = worker_control.worker_config("scan").get("max_per_hour")
        if not mph and store is not None:
            try:
                mph = int(await store.get_setting("WORKER_MAX_SCANS_PER_HOUR", max_per_hour))
            except Exception:  # noqa: BLE001 - config ao vivo é best-effort
                mph = None
        mph = int(mph or max_per_hour)
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
                # KL-77 (Fase 2): capture_raw devolve o response bruto (headers/html/dns/ssl)
                # já buscado no enrich — sem request extra — para arquivarmos no GCS.
                raw = await enrich_profile(store, target_id, url, s.score if s else None,
                                           capture_raw=True)
                if scan_id is not None and raw is not None:
                    # KL-75: extrai o tech stack do MESMO response (após o enrich, antes do
                    # GCS) — parse em memória, resiliente, não trava o scan.
                    await persist_tech_detection(store, target_id, scan_id, raw)
                    # Fire-and-forget: o upload nunca trava nem derruba o scan (já persistido).
                    from scanner.gcs_archive import archive_scan_response
                    from scanner.checks.base import domain_of
                    await archive_scan_response(
                        scan_id, target_id, url, domain_of(url), raw, redis=client)
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
