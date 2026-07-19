"""KL-75 — backfill do tech stack a partir dos responses brutos arquivados no GCS.

Reprocessa os arquivos que o KL-77 Fase 2 arquiva em ``gs://klarim-raw/YYYY/MM/DD/
{scan_id}.json.gz`` (headers/html/dns/ssl do momento do scan) pela MESMA função de
detecção do scan worker (``scanner.main.persist_tech_detection``) — nenhum re-scan,
nenhum request HTTP. Idempotente: o UNIQUE index de ``site_tech_stack`` evita duplicar.

Cobre só os scans a partir de 2026-07-19 (quando o arquivamento GCS começou). Scans
anteriores (sem response bruto) precisam de re-scan — que o worker faz gradualmente.

Uso (na VM, dentro do container `worker`, com ADC/SA do GCS):
  python -m scripts.backfill_tech_stack --date 2026-07-19
  python -m scripts.backfill_tech_stack --all
  python -m scripts.backfill_tech_stack --limit 100 --dry-run
"""

from __future__ import annotations

import argparse
import asyncio
import gzip
import json

from discovery.store import get_target_store
from scanner.main import persist_tech_detection

BATCH = 50


def _blob_prefix(date: str | None) -> str:
    """`2026-07-19` → `2026/07/19/` (particionamento do bucket). Vazio = tudo."""
    if not date:
        return ""
    return date.replace("-", "/").strip("/") + "/"


def decode_archive(raw: bytes) -> dict:
    """Descomprime (gzip) e desserializa (JSON) um response arquivado. Puro/testável."""
    return json.loads(gzip.decompress(raw).decode("utf-8"))


async def _process_blob(store, blob, dry_run: bool) -> dict | None:
    """Baixa 1 blob, detecta o tech stack e grava. Retorna um resumo (ou None em erro)."""
    try:
        raw = await asyncio.to_thread(blob.download_as_bytes)
        payload = decode_archive(raw)
    except Exception as exc:  # noqa: BLE001
        print(f"[backfill] {blob.name}: erro ao ler/decodificar ({exc!r})", flush=True)
        return None

    target_id = payload.get("target_id")
    scan_id = payload.get("scan_id")
    domain = payload.get("domain")
    if target_id is None:
        print(f"[backfill] {blob.name}: sem target_id — pulado", flush=True)
        return None

    if dry_run:
        from scanner.tech_detector import detect_tech_stack
        result = detect_tech_stack(
            headers=payload.get("headers") or {}, html=payload.get("html") or "",
            dns=payload.get("dns") or {}, ssl=payload.get("ssl") or {})
        techs = len(result.get("technologies") or [])
        print(f"[dry-run] scan {scan_id} | {domain} | techs={techs} "
              f"email={result.get('email_provider')} status={result.get('site_status')}",
              flush=True)
        return {"scan_id": scan_id, "techs": techs}

    result = await persist_tech_detection(store, target_id, scan_id, payload)
    techs = len(result.get("technologies") or [])
    print(f"scan {scan_id} | {domain} | techs={techs} "
          f"email={result.get('email_provider')}", flush=True)
    return {"scan_id": scan_id, "techs": techs}


async def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill de tech stack dos responses GCS.")
    parser.add_argument("--date", help="Só os scans deste dia (YYYY-MM-DD).")
    parser.add_argument("--all", action="store_true", help="Todo o bucket.")
    parser.add_argument("--limit", type=int, default=0, help="Máx. de arquivos (0 = sem limite).")
    parser.add_argument("--dry-run", action="store_true", help="Só detecta e imprime, não grava.")
    args = parser.parse_args()

    if not args.date and not args.all:
        parser.error("informe --date YYYY-MM-DD ou --all")

    store = get_target_store()
    if not args.dry_run:
        await store.ensure_schema()

    from scanner.gcs_archive import _get_bucket
    bucket = await asyncio.to_thread(_get_bucket)
    prefix = _blob_prefix(args.date)
    print(f"[backfill] listando gs://{bucket.name}/{prefix or '(tudo)'}…", flush=True)

    processed = techs_total = errors = 0
    batch: list = []

    def _iter_blobs():
        return list(bucket.list_blobs(prefix=prefix))

    blobs = await asyncio.to_thread(_iter_blobs)
    print(f"[backfill] {len(blobs)} arquivos encontrados.", flush=True)

    for blob in blobs:
        if not blob.name.endswith(".json.gz"):
            continue
        batch.append(blob)
        if len(batch) >= BATCH:
            results = await asyncio.gather(*[_process_blob(store, b, args.dry_run) for b in batch])
            for r in results:
                if r is None:
                    errors += 1
                else:
                    processed += 1
                    techs_total += r["techs"]
            batch = []
        if args.limit and processed + errors >= args.limit:
            break

    if batch and (not args.limit or processed + errors < args.limit):
        results = await asyncio.gather(*[_process_blob(store, b, args.dry_run) for b in batch])
        for r in results:
            if r is None:
                errors += 1
            else:
                processed += 1
                techs_total += r["techs"]

    print(f"\n[backfill] concluído: {processed} processados, {errors} erros, "
          f"{techs_total} tecnologias detectadas{' (dry-run)' if args.dry_run else ''}.",
          flush=True)


if __name__ == "__main__":
    asyncio.run(main())
