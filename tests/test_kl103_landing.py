"""KL-103 — social proof da landing: contadores em `/public/stats` + evento
`sector_pill_click`. Endpoint público, agregado (sem PII), cacheado 1h. Offline.
"""

from __future__ import annotations

import asyncio

import api.main as m
from discovery.store import TargetStore


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


class _RecCur:
    def __init__(self, ones):
        self.executed = []
        self._ones = list(ones)

    def execute(self, sql, params=None):
        self.executed.append(sql)

    def fetchone(self):
        return (self._ones.pop(0),) if self._ones else (0,)


# --- store.public_landing_counts (SQL) -------------------------------------- #

def test_public_landing_counts_queries(monkeypatch):
    cur = _RecCur([49951, 50, 27525])   # sites, sectors, profiles (na ordem das queries)
    store = TargetStore()
    monkeypatch.setattr(store, "_run", lambda fn: fn(cur))
    out = _run(store.public_landing_counts())
    assert out == {"sites_analyzed": 49951, "sectors": 50, "public_profiles": 27525}
    sql = " ".join(cur.executed)
    assert "FROM targets WHERE status <> 'discovered'" in sql
    assert "COUNT(DISTINCT sector)" in sql and "sector <> 'outro'" in sql
    assert "FROM site_profile WHERE public_visible = TRUE" in sql


# --- endpoint só expõe agregados (sem PII) ---------------------------------- #

def test_landing_counts_are_aggregate_only(monkeypatch):
    out = _run(_fake_public_stats(monkeypatch))
    # só contadores — nada de e-mail/cnpj/whatsapp/contact/target detalhado
    blob = str(out).lower()
    for leak in ("contact_email", "cnpj", "whatsapp", "@"):
        assert leak not in blob
    assert out["sites_analyzed"] > 0 and out["sectors"] > 0 and out["public_profiles"] > 0


async def _fake_public_stats(monkeypatch):
    class S:
        async def public_platform_stats(self):
            return {"total_targets": 50000, "total_scans": 20000, "scanned": 22000,
                    "score_100_count": 200, "distribution": {}}

        async def all_sector_benchmarks(self, min_count=10):
            return [{"sector": "tecnologia", "count": 300, "avg_score": 70, "median": 71}]

        async def public_landing_counts(self):
            return {"sites_analyzed": 49951, "sectors": 50, "public_profiles": 27525}

    monkeypatch.setattr(m, "get_target_store", lambda: S())

    async def _no_guard(request, ns):
        return None

    async def _no_cache(key):
        return None

    async def _set(key, value, ttl=86400):
        return None

    monkeypatch.setattr(m, "_public_content_guard", _no_guard)
    monkeypatch.setattr(m, "_cache_get", _no_cache)
    monkeypatch.setattr(m, "_cache_set", _set)
    resp = await m.public_stats(None)
    import json
    return json.loads(resp.body)


# --- método: só GET (POST/PUT/DELETE → 405) --------------------------------- #

def test_public_stats_only_get():
    from fastapi.testclient import TestClient
    c = TestClient(m.app, raise_server_exceptions=False)
    assert c.post("/public/stats").status_code == 405
    assert c.put("/public/stats").status_code == 405
    assert c.delete("/public/stats").status_code == 405


# --- evento sector_pill_click aceito ---------------------------------------- #

def test_sector_pill_click_is_known_event():
    assert "sector_pill_click" in m._KNOWN_EVENTS
