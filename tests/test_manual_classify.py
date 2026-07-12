"""Testes da classificação manual de setor pelo painel admin — offline."""

from __future__ import annotations

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

import api.main as m


# --- _resolve_classification (lógica pura) --------------------------------- #

def test_resolve_derives_tier_from_sector():
    # KL-54: preço único ⇒ todo setor deriva tier `standard`.
    assert m._resolve_classification("clinica", None) == ("clinica", "standard")
    assert m._resolve_classification("hotel", None) == ("hotel", "standard")
    assert m._resolve_classification("odontologia", None) == ("odontologia", "standard")


def test_resolve_honors_explicit_tier():
    assert m._resolve_classification("hotel", "professional") == ("hotel", "professional")


def test_resolve_rejects_invalid_sector():
    with pytest.raises(HTTPException) as exc:
        m._resolve_classification("banana", None)
    assert exc.value.status_code == 422


def test_resolve_rejects_invalid_tier():
    with pytest.raises(HTTPException) as exc:
        m._resolve_classification("hotel", "ouro")
    assert exc.value.status_code == 422


# --- endpoints (auth + store falso) ---------------------------------------- #

class FakeStore:
    def __init__(self):
        self.calls = []

    async def manual_classify(self, target_id, sector, price_tier):
        self.calls.append((target_id, sector, price_tier))
        if target_id == 999:
            return None
        return {"id": target_id, "sector": sector, "price_tier": price_tier,
                "classification_source": "manual", "classification_confidence": 1.0}

    async def manual_classify_batch(self, target_ids, sector, price_tier):
        self.calls.append((tuple(target_ids), sector, price_tier))
        return len(target_ids)


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("ADMIN_USER", "admin")
    monkeypatch.setenv("ADMIN_PASSWORD", "s3nha-forte")
    monkeypatch.setenv("JWT_SECRET", "x" * 64)
    store = FakeStore()
    monkeypatch.setattr(m, "get_target_store", lambda: store)
    c = TestClient(m.app, raise_server_exceptions=False)
    c._store = store
    return c


def _auth(client):
    token = client.post("/auth/login", json={"username": "admin", "password": "s3nha-forte"}).json()["token"]
    return {"Authorization": f"Bearer {token}"}


def test_classify_endpoints_protected():
    assert m._is_protected("/targets/1/classify") is True
    assert m._is_protected("/admin/classify-batch") is True
    c = TestClient(m.app, raise_server_exceptions=False)
    assert c.patch("/targets/1/classify", json={"sector": "hotel"}).status_code == 401
    assert c.post("/admin/classify-batch", json={"target_ids": [1], "sector": "hotel"}).status_code == 401


def test_patch_classify_derives_tier(client):
    r = client.patch("/targets/5/classify", json={"sector": "clinica"}, headers=_auth(client))
    assert r.status_code == 200
    body = r.json()
    assert body["sector"] == "clinica" and body["price_tier"] == "standard"
    assert body["classification_source"] == "manual"
    assert client._store.calls[-1] == (5, "clinica", "standard")


def test_patch_classify_invalid_sector(client):
    r = client.patch("/targets/5/classify", json={"sector": "xpto"}, headers=_auth(client))
    assert r.status_code == 422


def test_patch_classify_not_found(client):
    r = client.patch("/targets/999/classify", json={"sector": "hotel"}, headers=_auth(client))
    assert r.status_code == 404


def test_classify_batch(client):
    r = client.post("/admin/classify-batch",
                    json={"target_ids": [1, 2, 3], "sector": "hotel"}, headers=_auth(client))
    assert r.status_code == 200
    assert r.json() == {"updated": 3, "sector": "hotel", "price_tier": "standard"}
