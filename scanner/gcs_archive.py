"""Arquivamento dos responses brutos de scan no Google Cloud Storage (KL-77 Fase 2).

Cada scan gera dados que o PostgreSQL **não** guarda: o HTML da homepage no momento
do scan, os headers crus, o snapshot de DNS/SSL. Esse response bruto é irrecuperável
depois — o KL-75 (enriquecimento expandido) vai precisar dele para reprocessar sem
re-escanear. Este módulo comprime o response e faz upload para um bucket Nearline.

**Fire-and-forget:** o upload NUNCA trava nem derruba o scan. Se o bucket não existe,
a service account não tem permissão, ou a rede cai, a exceção é logada e engolida —
os checks já foram gravados no banco. Se ``GCS_ENABLED=false`` não há tentativa alguma.

**Inicialização lazy:** o client do GCS (e o import de ``google.cloud.storage``) só
acontecem no primeiro upload — o boot do container e os testes offline não pagam por isso.

Contadores (sucesso/falha/bytes) ficam no Redis (chaves por dia, TTL 48h) para que o
worker (que arquiva) e a API/MCP (que lê `get_gcs_archive_stats`) — processos separados —
compartilhem o mesmo estado. Há também um espelho em memória para o caminho sem Redis.
"""

from __future__ import annotations

import asyncio
import gzip
import json
import logging
import os
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

# Client GCS inicializado lazy (só no 1º upload).
_client = None
_bucket = None

# TTL das chaves de contadores por dia no Redis (48h — cobre "hoje" com folga).
_STATS_TTL = 172800


# --------------------------------------------------------------------------- #
# Config (lida em tempo de chamada — permite toggling por env/teste)
# --------------------------------------------------------------------------- #

def _enabled() -> bool:
    """`GCS_ENABLED` (default true). Lido a cada chamada para respeitar mudança de env."""
    return os.getenv("GCS_ENABLED", "true").strip().lower() == "true"


def _bucket_name() -> str:
    return os.getenv("GCS_BUCKET", "klarim-raw")


def _get_bucket():
    """Bucket GCS (client lazy). Import de google.cloud só aqui — nunca no boot."""
    global _client, _bucket
    if _bucket is None:
        from google.cloud import storage  # import lazy: pesado, só quando arquiva
        _client = storage.Client()
        _bucket = _client.bucket(_bucket_name())
    return _bucket


# --------------------------------------------------------------------------- #
# Serialização (puro, testável — sem I/O)
# --------------------------------------------------------------------------- #

def archive_object_path(scan_id, now: datetime) -> str:
    """Caminho do objeto no bucket: ``YYYY/MM/DD/{scan_id}.json.gz`` (particiona por dia)."""
    return f"{now.strftime('%Y/%m/%d')}/{scan_id}.json.gz"


def build_archive_payload(scan_id, target_id, url: str, domain: str,
                          response_data: dict, now: datetime) -> dict:
    """Monta o dict do arquivo a partir do response bruto já em memória.

    O ``response_data`` traz o que o worker capturou durante o scan (sem request
    extra): headers, html, dns, ssl, status e tempo. ``html_size_bytes`` é o
    tamanho em bytes (UTF-8), não em caracteres.
    """
    html = response_data.get("html") or ""
    return {
        "target_id": target_id,
        "scan_id": scan_id,
        "domain": domain,
        "url": url,
        "timestamp": now.isoformat(),
        "http_status": response_data.get("http_status"),
        "response_time_ms": response_data.get("response_time_ms"),
        "headers": response_data.get("headers") or {},
        "html": html,
        "html_size_bytes": len(html.encode("utf-8")),
        "dns": response_data.get("dns") or {},
        "ssl": response_data.get("ssl") or {},
    }


def serialize_payload(payload: dict) -> bytes:
    """Serializa o payload em JSON UTF-8 e comprime com gzip.

    ``default=str`` cobre tipos não-JSON que o snapshot TLS pode trazer (datetimes
    das datas do certificado), sem quebrar a serialização.
    """
    raw = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
    return gzip.compress(raw)


# --------------------------------------------------------------------------- #
# Contadores — memória (fallback) + Redis (cross-process)
# --------------------------------------------------------------------------- #

_MEM_STATS = {"date": None, "count": 0, "bytes": 0, "errors": 0,
              "last_upload_at": None, "last_error": None}


def _mem_rollover(now: datetime) -> None:
    day = now.strftime("%Y%m%d")
    if _MEM_STATS["date"] != day:
        _MEM_STATS.update(date=day, count=0, bytes=0, errors=0)


def _mem_record_success(size: int, now: datetime) -> None:
    _mem_rollover(now)
    _MEM_STATS["count"] += 1
    _MEM_STATS["bytes"] += int(size)
    _MEM_STATS["last_upload_at"] = now.isoformat()


def _mem_record_error(msg: str, now: datetime) -> None:
    _mem_rollover(now)
    _MEM_STATS["errors"] += 1
    _MEM_STATS["last_error"] = f"{now.isoformat()}: {msg}"


def _decode(v):
    """Redis pode devolver bytes (sem decode_responses) — normaliza para str."""
    if isinstance(v, bytes):
        return v.decode("utf-8", "replace")
    return v


async def _redis_record_success(redis, size: int, now: datetime) -> None:
    day = now.strftime("%Y%m%d")
    try:
        await redis.incr(f"klarim:gcs:{day}:count")
        await redis.incrby(f"klarim:gcs:{day}:bytes", int(size))
        await redis.expire(f"klarim:gcs:{day}:count", _STATS_TTL)
        await redis.expire(f"klarim:gcs:{day}:bytes", _STATS_TTL)
        await redis.set("klarim:gcs:last_upload_at", now.isoformat())
    except Exception:  # noqa: BLE001 - contador é best-effort, nunca importa
        pass


async def _redis_record_error(redis, msg: str, now: datetime) -> None:
    day = now.strftime("%Y%m%d")
    try:
        await redis.incr(f"klarim:gcs:{day}:errors")
        await redis.expire(f"klarim:gcs:{day}:errors", _STATS_TTL)
        await redis.set("klarim:gcs:last_error", f"{now.isoformat()}: {msg}"[:500])
    except Exception:  # noqa: BLE001
        pass


# --------------------------------------------------------------------------- #
# Upload
# --------------------------------------------------------------------------- #

async def archive_scan_response(scan_id, target_id, url: str, domain: str,
                                response_data: dict, *, redis=None) -> bool:
    """Comprime e faz upload do response bruto para o GCS.

    Chamado APÓS os checks terem sido gravados no PostgreSQL. Não bloqueia o scan:
    qualquer exceção é logada e engolida (retorna False). ``GCS_ENABLED=false`` faz
    bypass total (nenhum toque no client GCS). O upload roda numa thread para não
    prender o event loop do worker.
    """
    if not _enabled():
        return False

    now = datetime.now(timezone.utc)
    try:
        payload = build_archive_payload(scan_id, target_id, url, domain, response_data, now)
        compressed = serialize_payload(payload)
        path = archive_object_path(scan_id, now)

        bucket = _get_bucket()
        blob = bucket.blob(path)
        # upload_from_string é síncrono/bloqueante → thread separada.
        await asyncio.to_thread(
            blob.upload_from_string, compressed, content_type="application/gzip")

        size = len(compressed)
        _mem_record_success(size, now)
        if redis is not None:
            await _redis_record_success(redis, size, now)
        logger.debug("GCS archived: %s (%d bytes)", path, size)
        return True
    except Exception as exc:  # noqa: BLE001 - NUNCA propagar: o scan já completou
        logger.warning("GCS archive failed for scan %s: %r", scan_id, exc)
        _mem_record_error(repr(exc), now)
        if redis is not None:
            await _redis_record_error(redis, repr(exc), now)
        return False


# --------------------------------------------------------------------------- #
# Stats (para o MCP / painel)
# --------------------------------------------------------------------------- #

async def get_archive_stats(redis=None) -> dict:
    """Saúde do arquivamento HOJE (UTC): habilitado, bucket, arquivos, bytes, média,
    último upload, erros e último erro. Lê do Redis (cross-process) ou, sem Redis,
    do espelho em memória. Best-effort — erro de leitura devolve zeros."""
    now = datetime.now(timezone.utc)
    day = now.strftime("%Y%m%d")
    count = bytes_ = errors = 0
    last_upload = last_error = None

    if redis is not None:
        try:
            count = int(await redis.get(f"klarim:gcs:{day}:count") or 0)
            bytes_ = int(await redis.get(f"klarim:gcs:{day}:bytes") or 0)
            errors = int(await redis.get(f"klarim:gcs:{day}:errors") or 0)
            last_upload = _decode(await redis.get("klarim:gcs:last_upload_at"))
            last_error = _decode(await redis.get("klarim:gcs:last_error"))
        except Exception:  # noqa: BLE001
            count = bytes_ = errors = 0
    else:
        _mem_rollover(now)
        count = _MEM_STATS["count"]
        bytes_ = _MEM_STATS["bytes"]
        errors = _MEM_STATS["errors"]
        last_upload = _MEM_STATS["last_upload_at"]
        last_error = _MEM_STATS["last_error"]

    avg_bytes = round(bytes_ / count) if count else 0
    return {
        "enabled": _enabled(),
        "bucket": _bucket_name(),
        "files_today": count,
        "bytes_today": bytes_,
        "avg_bytes": avg_bytes,
        "errors_today": errors,
        "last_upload_at": last_upload,
        "last_error": last_error,
    }
