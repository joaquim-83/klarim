"""KL-150 P2 (item 4) — /public/best devolve o total REAL de sites score 100 (não o tamanho da
lista truncada em 300). Ex.: a vitrine lista 300, mas "São X no total" deve mostrar 719."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import api.main as m


class _Store:
    def __init__(self, rows, total):
        self._rows = rows
        self._total = total
        self.count_calls = 0

    async def public_score_100_sites(self, sector=None, limit=200):
        return self._rows[:limit]

    async def count_public_score_100_sites(self):
        self.count_calls += 1
        return self._total


@pytest.fixture
def client(monkeypatch):
    rows = [{"domain": f"s{i}.com.br", "sector": "tecnologia", "company_name": None,
             "owner_verified": False} for i in range(300)]
    store = _Store(rows, total=719)
    monkeypatch.setattr(m, "get_target_store", lambda: store)
    # sem cache Redis nos testes → recalcula (o guard cai no rate-limit in-memory)
    return TestClient(m.app, raise_server_exceptions=False), store


def test_total_is_real_count_not_list_len(client):
    c, store = client
    r = c.get("/public/best")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 719          # total REAL (não 300)
    assert body["shown"] == 300          # a vitrine lista até 300
    assert store.count_calls == 1


def test_total_falls_back_to_len_when_count_zero(client, monkeypatch):
    c, store = client
    store._total = 0
    body = c.get("/public/best").json()
    assert body["total"] == 300          # count 0 → cai no len(rows) (fail-safe)
