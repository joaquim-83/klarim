"""Testes do dashboard operacional (KL-16) — health checks + heartbeat, offline."""

from __future__ import annotations

import asyncio
import json

import api.health_checks as hc
import discovery.heartbeat as hb


class FakeRedis:
    def __init__(self, data=None):
        self.data = data or {}

    async def get(self, k):
        return self.data.get(k)

    async def ping(self):
        return True

    async def llen(self, k):
        return 3


# --- health checks --------------------------------------------------------- #

def test_check_resend_unknown_without_key(monkeypatch):
    monkeypatch.delenv("RESEND_API_KEY", raising=False)
    assert asyncio.run(hc.check_resend())["status"] == "unknown"


def test_check_abacatepay_unknown_without_key(monkeypatch):
    monkeypatch.delenv("ABACATEPAY_API_KEY", raising=False)
    assert asyncio.run(hc.check_abacatepay())["status"] == "unknown"


def test_check_redis_ok_and_none():
    assert asyncio.run(hc.check_redis(FakeRedis()))["status"] == "ok"
    assert asyncio.run(hc.check_redis(None))["status"] == "unknown"


def test_check_ct_logs_streaming():
    r = FakeRedis({"discovery:status": json.dumps(
        {"source": {"connected": True, "total_seen": 100, "buffer_size": 5}})})
    res = asyncio.run(hc.check_ct_logs(r))
    assert res["status"] == "streaming" and res["total_seen"] == 100


def test_check_ct_logs_disconnected():
    r = FakeRedis({"discovery:status": json.dumps({"source": {"connected": False}})})
    assert asyncio.run(hc.check_ct_logs(r))["status"] == "disconnected"


def test_check_ct_logs_no_heartbeat():
    assert asyncio.run(hc.check_ct_logs(FakeRedis()))["status"] == "error"


# --- heartbeat ------------------------------------------------------------- #

def test_publish_heartbeat(monkeypatch):
    class FakeR:
        def __init__(self):
            self.store = {}

        async def setex(self, k, ttl, v):
            self.store[k] = (ttl, v)

    fr = FakeR()

    async def fake_client():
        return fr

    monkeypatch.setattr(hb, "_client", fake_client)
    asyncio.run(hb.publish_heartbeat("alert", {"last_cycle_at": "2026-07-07T00:00:00Z"}, ttl=600))

    assert "worker:alert:status" in fr.store
    ttl, raw = fr.store["worker:alert:status"]
    body = json.loads(raw)
    assert ttl == 600 and body["alive"] is True
    assert body["last_cycle_at"] == "2026-07-07T00:00:00Z" and body["updated_at"]
