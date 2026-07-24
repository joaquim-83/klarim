"""Health checks das dependências externas (KL-16 — dashboard operacional).

Cada check devolve {"status": "ok"|"error"|"unknown", "latency_ms": N, "detail": …}
e nunca levanta — o painel mostra 🟢/🟡/🔴 sem derrubar o /system/status.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from typing import Any, Dict, Optional

import httpx

from discovery.store import get_target_store


async def _timed(coro) -> tuple:
    t0 = time.monotonic()
    try:
        detail = await coro
        return "ok", detail, int((time.monotonic() - t0) * 1000)
    except Exception as exc:  # noqa: BLE001
        return "error", repr(exc), int((time.monotonic() - t0) * 1000)


def _result(status: str, latency_ms: Optional[int] = None, detail: Any = None) -> Dict[str, Any]:
    out: Dict[str, Any] = {"status": status}
    if latency_ms is not None:
        out["latency_ms"] = latency_ms
    if detail is not None:
        out["detail"] = detail
    return out


async def check_postgres() -> Dict[str, Any]:
    status, detail, ms = await _timed(get_target_store().ping())
    return _result(status, ms, None if status == "ok" else detail)


async def check_redis(redis_client) -> Dict[str, Any]:
    if redis_client is None:
        return _result("unknown", detail="sem cliente Redis")
    status, detail, ms = await _timed(redis_client.ping())
    return _result(status, ms, None if status == "ok" else detail)


async def check_ct_logs(redis_client) -> Dict[str, Any]:
    """Lê o status do CT poller (publicado pelo Discovery Worker no Redis)."""
    if redis_client is None:
        return _result("unknown", detail="sem cliente Redis")
    try:
        raw = await redis_client.get(os.environ.get("KLARIM_DISCOVERY_STATUS_KEY", "discovery:status"))
    except Exception as exc:  # noqa: BLE001
        return _result("error", detail=repr(exc))
    if not raw:
        return _result("error", detail="sem heartbeat do discovery")
    src = (json.loads(raw).get("source") or {})
    connected = bool(src.get("connected"))
    return {
        "status": "streaming" if connected else "disconnected",
        "total_seen": src.get("total_seen"),
        "buffer": src.get("buffer_size"),
    }


async def _reachable(url: str, key: str) -> Dict[str, Any]:
    """Health por REACHABILITY: qualquer resposta < 500 = serviço no ar.

    Chaves com escopo limitado (ex.: a do Resend é send-only) respondem 401 em
    endpoints de leitura mesmo válidas — isso NÃO é downtime, então não vira 🔴.
    Só rede/timeout/5xx contam como erro.
    """
    t0 = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=5.0) as c:
            r = await c.get(url, headers={"Authorization": f"Bearer {key}"})
        ms = int((time.monotonic() - t0) * 1000)
        if r.status_code < 500:
            detail = None if r.status_code < 400 else f"reachable (HTTP {r.status_code})"
            return _result("ok", ms, detail)
        return _result("error", ms, f"HTTP {r.status_code}")
    except Exception as exc:  # noqa: BLE001 - rede/timeout
        return _result("error", int((time.monotonic() - t0) * 1000), repr(exc))


async def check_resend() -> Dict[str, Any]:
    """Health por REACHABILITY do host — SEM chamar endpoint autenticado. A key do Resend
    é send-only (`POST /emails`): um `GET /domains` respondia 401 e poluía os logs do Resend
    a cada ciclo (fix operacional 24/07). Um HEAD ao host (sem Authorization) prova a
    conectividade TLS/rede sem consumir permissão nem gerar ruído de auth. Qualquer resposta
    < 500 = no ar; só rede/timeout/5xx viram 🔴."""
    if not os.environ.get("RESEND_API_KEY"):
        return _result("unknown", detail="RESEND_API_KEY não configurada")
    t0 = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=5.0) as c:
            r = await c.head("https://api.resend.com")
        ms = int((time.monotonic() - t0) * 1000)
        if r.status_code < 500:
            return _result("ok", ms, None if r.status_code < 400 else f"reachable (HTTP {r.status_code})")
        return _result("error", ms, f"HTTP {r.status_code}")
    except Exception as exc:  # noqa: BLE001 - rede/timeout
        return _result("error", int((time.monotonic() - t0) * 1000), repr(exc))


async def check_abacatepay() -> Dict[str, Any]:
    key = os.environ.get("ABACATEPAY_API_KEY")
    if not key:
        return _result("unknown", detail="ABACATEPAY_API_KEY não configurada")
    return await _reachable("https://api.abacatepay.com/v2/billing/list", key)


async def run_all(redis_client) -> Dict[str, Any]:
    pg, rd, ct, rs, ab = await asyncio.gather(
        check_postgres(), check_redis(redis_client), check_ct_logs(redis_client),
        check_resend(), check_abacatepay(),
    )
    return {"postgres": pg, "redis": rd, "ct_logs": ct, "resend": rs, "abacatepay": ab}
