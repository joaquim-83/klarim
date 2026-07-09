"""Testes da edição de status/e-mail e da busca de alvos no painel — offline."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import api.main as m


class FakeStore:
    def __init__(self):
        self.calls = []
        self.list_kwargs = None

    async def update_target_status(self, target_id, status):
        self.calls.append(("status", target_id, status))
        if target_id == 999:
            return None
        return {"id": target_id, "status": status}

    async def update_target_email(self, target_id, email):
        self.calls.append(("email", target_id, email))
        if target_id == 999:
            return None
        # simula a regra sem_contato -> discovered
        return {"id": target_id, "contact_email": email, "status": "discovered"}

    async def list_targets(self, status=None, platform=None, sector=None, source=None,
                           limit=50, offset=0, low_confidence=False, search=None):
        self.list_kwargs = {"status": status, "search": search, "limit": limit}
        return [{"id": 1, "url": "https://verdegreen.com.br", "domain": "verdegreen.com.br"}]


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


# --- proteção JWT ---------------------------------------------------------- #

def test_edit_endpoints_protected():
    assert m._is_protected("/targets/1/status") is True
    assert m._is_protected("/targets/1/email") is True
    c = TestClient(m.app, raise_server_exceptions=False)
    assert c.patch("/targets/1/status", json={"status": "scanned"}).status_code == 401
    assert c.patch("/targets/1/email", json={"contact_email": "a@b.com.br"}).status_code == 401


# --- status ---------------------------------------------------------------- #

def test_patch_status_valid(client):
    r = client.patch("/targets/5/status", json={"status": "scanned"}, headers=_auth(client))
    assert r.status_code == 200 and r.json()["status"] == "scanned"
    assert client._store.calls[-1] == ("status", 5, "scanned")


def test_patch_status_invalid(client):
    r = client.patch("/targets/5/status", json={"status": "banana"}, headers=_auth(client))
    assert r.status_code == 422


def test_patch_status_not_found(client):
    r = client.patch("/targets/999/status", json={"status": "scanned"}, headers=_auth(client))
    assert r.status_code == 404


# --- e-mail ---------------------------------------------------------------- #

def test_patch_email_valid_lowercased(client):
    r = client.patch("/targets/5/email", json={"contact_email": "Contato@Hotel.com.BR"}, headers=_auth(client))
    assert r.status_code == 200
    assert r.json()["contact_email"] == "contato@hotel.com.br"  # normalizado
    assert r.json()["status"] == "discovered"
    assert client._store.calls[-1] == ("email", 5, "contato@hotel.com.br")


def test_patch_email_invalid(client):
    for bad in ("nao-eh-email", "sem-arroba.com", "a@b", ""):
        r = client.patch("/targets/5/email", json={"contact_email": bad}, headers=_auth(client))
        assert r.status_code == 422, bad


def test_patch_email_not_found(client):
    r = client.patch("/targets/999/email", json={"contact_email": "a@b.com.br"}, headers=_auth(client))
    assert r.status_code == 404


# --- busca ----------------------------------------------------------------- #

def test_list_targets_forwards_search(client):
    r = client.get("/targets?search=verde", headers=_auth(client))
    assert r.status_code == 200
    assert client._store.list_kwargs["search"] == "verde"


def test_list_targets_no_search(client):
    r = client.get("/targets", headers=_auth(client))
    assert r.status_code == 200
    assert client._store.list_kwargs["search"] is None
