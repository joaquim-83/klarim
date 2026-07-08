"""Heartbeat dos workers no Redis (KL-16 — dashboard operacional).

Cada worker publica `worker:<name>:status` com TTL 600s (10min). Se o worker
morre, a chave expira e o painel mostra 🔴. Republicado periodicamente (mais
frequente que o TTL), independente do ciclo do worker (que pode ser de horas).
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone

HEARTBEAT_TTL = int(os.environ.get("WORKER_HEARTBEAT_TTL", "600"))

_redis = None


async def _client():
    global _redis
    if _redis is None:
        import redis.asyncio as aioredis

        _redis = aioredis.from_url(
            os.environ.get("REDIS_URL", "redis://redis:6379/0"), decode_responses=True)
    return _redis


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def publish_heartbeat(name: str, payload: dict, ttl: int = HEARTBEAT_TTL) -> None:
    try:
        r = await _client()
        body = {"alive": True, "updated_at": _utcnow_iso(), **payload}
        await r.setex(f"worker:{name}:status", ttl, json.dumps(body))
    except Exception as exc:  # noqa: BLE001 - heartbeat é best-effort
        print(f"[heartbeat] {name}: {exc!r}", flush=True)
