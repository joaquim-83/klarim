"""Testes do fix de perfis incompletos + notificações (KL-fix). Offline.

Cobre: o enrich_profile LOGA o bloqueio (homepage 403/WAF) em vez de falhar em
silêncio; o `enrich_all --force/--domain` roda o enrich_profile compartilhado; o
endpoint `/analytics/events` filtra por `event_type` (aba Consultas de perfil).
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

import api.main as m
from api import auth_users


# --- 1. enrich_profile loga o bloqueio 403 (não é mais silencioso) ---------- #

def test_enrich_profile_logs_homepage_block(monkeypatch, capsys):
    import scanner.enrichment as en
    import scanner.checks.base as base
    import scanner.checks.dns_util as dns
    import scanner.profiler as profiler

    class _Resp:
        status_code = 403
        text = "forbidden"
        headers = {}

    async def _fake_fetch(url, method="GET", follow_redirects=True):
        return _Resp()

    monkeypatch.setattr(base, "fetch", _fake_fetch)
    monkeypatch.setattr(profiler, "fetch", _fake_fetch)   # o crawl usa o mesmo fetch
    monkeypatch.setattr(dns, "resolve_mx", lambda d: [])
    monkeypatch.setattr(dns, "resolve_ns", lambda d: [])

    class _Store:
        def __init__(self):
            self.upserted = None

        async def upsert_site_profile(self, tid, profile):
            self.upserted = (tid, profile)

    s = _Store()
    asyncio.run(en.enrich_profile(s, 1, "https://blocked.com.br", 80))
    out = capsys.readouterr().out
    assert "homepage HTTP 403" in out          # o bloqueio agora aparece no log
    assert "páginas=" in out                    # log de resumo com nº de páginas
    assert s.upserted is not None               # perfil esparso ainda é gravado (best-effort)


# --- 2. enrich_all --force/--domain roda o enrich_profile compartilhado ----- #

def test_force_enrich_runs_shared_enrich_profile(monkeypatch):
    import scripts.enrich_all as ea
    import scanner.enrichment as en

    calls = []

    async def _fake_enrich(store, tid, url, score):
        calls.append((tid, url, score))

    monkeypatch.setattr(en, "enrich_profile", _fake_enrich)
    targets = [{"id": 1, "url": "https://a.com.br", "last_scan_score": 80},
               {"id": 2, "url": "https://b.com.br", "last_scan_score": None}]
    stats = asyncio.run(ea._force_enrich(None, targets, dry_run=False))
    assert stats["ok"] == 2 and stats["erros"] == 0
    assert [c[0] for c in calls] == [1, 2]


def test_force_enrich_dry_run_does_not_enrich(monkeypatch):
    import scripts.enrich_all as ea
    import scanner.enrichment as en

    called = {"n": 0}

    async def _fake_enrich(*a, **k):
        called["n"] += 1

    monkeypatch.setattr(en, "enrich_profile", _fake_enrich)
    targets = [{"id": 9, "url": "https://c.com.br", "last_scan_score": 50}]
    stats = asyncio.run(ea._force_enrich(None, targets, dry_run=True))
    assert stats["processed"] == 1 and called["n"] == 0   # dry-run não enriquece


# --- 3. /analytics/events?event_type= (aba Consultas de perfil) ------------- #

class _EvStore:
    def __init__(self):
        self.calls = []
        self.rows = []

    async def analytics_events(self, limit=50, event_type=None):
        self.calls.append({"limit": limit, "event_type": event_type})
        if event_type:
            return [r for r in self.rows if r["event_type"] == event_type]
        return self.rows


@pytest.fixture
def evstore(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "k" * 64)
    monkeypatch.setattr(auth_users, "_secret", lambda: "k" * 64)
    s = _EvStore()
    monkeypatch.setattr(m, "get_target_store", lambda: s)
    monkeypatch.setattr("discovery.store.get_target_store", lambda: s)
    return s


@pytest.fixture
def client(evstore):
    return TestClient(m.app, raise_server_exceptions=False)


def _admin(monkeypatch):
    monkeypatch.setenv("ADMIN_USER", "op")
    monkeypatch.setenv("ADMIN_PASSWORD", "pw")
    return {"Authorization": f"Bearer {m._create_token('op')}"}


def test_analytics_events_filters_profile_view(client, evstore, monkeypatch):
    evstore.rows = [
        {"event_type": "profile_view", "target_url": "https://igoove.com.br",
         "metadata": {"domain": "igoove.com.br"}, "created_at": "2026-07-14T10:00:00"},
        {"event_type": "page_view", "target_url": None, "metadata": {}, "created_at": "x"},
    ]
    h = _admin(monkeypatch)
    body = client.get("/analytics/events?event_type=profile_view", headers=h).json()
    assert evstore.calls[-1]["event_type"] == "profile_view"
    assert len(body["events"]) == 1 and body["events"][0]["event_type"] == "profile_view"


def test_analytics_events_no_filter(client, evstore, monkeypatch):
    evstore.rows = [{"event_type": "page_view", "metadata": {}, "created_at": "x"}]
    h = _admin(monkeypatch)
    client.get("/analytics/events", headers=h)
    assert evstore.calls[-1]["event_type"] is None


def test_analytics_events_requires_admin(client):
    assert client.get("/analytics/events?event_type=profile_view").status_code == 401
