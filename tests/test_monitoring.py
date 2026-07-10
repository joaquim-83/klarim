"""Testes de Sites Monitorados (KL-29) — offline, com store falso e rede mockada."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import api.main as m


# --- fakes ----------------------------------------------------------------- #

class FakeScore:
    def __init__(self, score):
        self.score = score


class FakeReport:
    def __init__(self, score):
        self.score = FakeScore(score)


class FakeStore:
    def __init__(self, active=None, upsert=None, by_token=None, approve=None):
        self._active = active or []
        self._upsert = upsert
        self._by_token = by_token
        self._approve = approve
        self.removed = None
        self.status_calls = []

    async def get_target_by_url(self, url):
        return None

    async def upsert_monitoring_offer(self, **kw):
        return self._upsert

    async def get_monitored_by_token(self, token):
        return self._by_token

    async def approve_monitored_site(self, token, display_name=None, logo_url=None):
        if self._approve is None:
            return None
        return {**self._approve, "display_name": display_name or self._approve.get("display_name")}

    async def remove_monitored_site_by_domain(self, domain):
        self.removed = domain
        return True

    async def get_active_monitored_sites(self):
        return self._active

    async def list_monitored_sites(self, status=None):
        return self._active

    async def monitored_stats(self):
        return {"total": len(self._active), "active": len(self._active), "suspended": 0, "pending": 0}


def _client(monkeypatch, store):
    monkeypatch.setattr(m, "get_target_store", lambda: store)
    return TestClient(m.app, raise_server_exceptions=False)


# --- payload público sem dados sensíveis ----------------------------------- #

def test_public_monitored_strips_sensitive():
    site = {"id": 1, "target_id": 9, "domain": "empresa.com.br", "url": "https://empresa.com.br",
            "display_name": "Empresa", "logo_url": None, "contact_email": "dono@empresa.com.br",
            "approval_token": "secret-token", "last_check_score": 100,
            "last_check_at": "2026-07-10T10:00:00", "approved_at": "2026-07-08T10:00:00"}
    pub = m._public_monitored(site)
    assert pub["domain"] == "empresa.com.br" and pub["display_name"] == "Empresa"
    assert pub["score"] == 100 and pub["logo_url"].endswith("/favicon.ico")
    for leaked in ("contact_email", "target_id", "approval_token", "id"):
        assert leaked not in pub


def test_monitoring_sites_endpoint_is_safe(monkeypatch):
    store = FakeStore(active=[{
        "id": 1, "target_id": 9, "domain": "x.com.br", "url": "https://x.com.br",
        "display_name": "X", "logo_url": "https://x.com.br/favicon.ico",
        "contact_email": "a@x.com.br", "approval_token": "tok", "last_check_score": 100,
        "last_check_at": None, "approved_at": None}])
    c = _client(monkeypatch, store)
    r = c.get("/monitoring/sites")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 1
    s0 = body["sites"][0]
    assert "contact_email" not in s0 and "approval_token" not in s0 and "target_id" not in s0


# --- oferta (guard de score 100) ------------------------------------------- #

def _authorize(monkeypatch, ok=True):
    async def _auth(request, url, charge_id):
        return ok
    monkeypatch.setattr(m, "_authorized_for_url", _auth)


def test_offer_requires_authorization(monkeypatch):
    store = FakeStore()
    c = _client(monkeypatch, store)
    _authorize(monkeypatch, ok=False)
    r = c.post("/monitoring/offer", json={"url": "x.com.br", "email": "a@b.com.br"})
    assert r.status_code == 403  # sem prova do scan completo


def test_offer_rejects_when_not_100(monkeypatch):
    store = FakeStore()
    c = _client(monkeypatch, store)
    _authorize(monkeypatch)

    async def _recent(url, full=False):
        return FakeReport(85)
    monkeypatch.setattr(m, "get_recent_only", _recent)
    r = c.post("/monitoring/offer", json={"url": "x.com.br", "email": "a@b.com.br"})
    assert r.status_code == 409


def test_offer_creates_pending_when_100(monkeypatch):
    store = FakeStore(upsert={"status": "pending", "approval_token": "tok123", "domain": "x.com.br"})
    c = _client(monkeypatch, store)
    _authorize(monkeypatch)

    async def _recent(url, full=False):
        return FakeReport(100)
    monkeypatch.setattr(m, "get_recent_only", _recent)
    r = c.post("/monitoring/offer", json={"url": "x.com.br", "email": "a@b.com.br"})
    assert r.status_code == 200
    b = r.json()
    assert b["status"] == "pending" and b["approval_token"] == "tok123"


def test_offer_already_active(monkeypatch):
    store = FakeStore(upsert={"status": "active", "approval_token": None, "domain": "x.com.br"})
    c = _client(monkeypatch, store)
    _authorize(monkeypatch)

    async def _recent(url, full=False):
        return FakeReport(100)
    monkeypatch.setattr(m, "get_recent_only", _recent)
    r = c.post("/monitoring/offer", json={"url": "x.com.br", "email": "a@b.com.br"})
    assert r.json().get("already") is True and r.json()["status"] == "active"


# --- aprovação ------------------------------------------------------------- #

def test_approve_ok(monkeypatch):
    store = FakeStore(by_token={"domain": "x.com.br"},
                      approve={"domain": "x.com.br", "display_name": None})
    c = _client(monkeypatch, store)
    r = c.post("/monitoring/approve", json={"token": "good", "display_name": "Empresa X"})
    assert r.status_code == 200 and r.json()["status"] == "active"
    assert r.json()["display_name"] == "Empresa X"


def test_approve_invalid_token(monkeypatch):
    store = FakeStore(by_token=None, approve=None)
    c = _client(monkeypatch, store)
    r = c.post("/monitoring/approve", json={"token": "bad"})
    assert r.status_code == 404


# --- remoção (HMAC) -------------------------------------------------------- #

def test_remove_valid_and_invalid(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "s" * 40)
    store = FakeStore()
    c = _client(monkeypatch, store)
    good = m._monitor_removal_token("x.com.br")
    r = c.get(f"/monitoring/remove?domain=x.com.br&token={good}")
    assert r.status_code == 200 and "Removido" in r.text and store.removed == "x.com.br"

    store2 = FakeStore()
    c2 = _client(monkeypatch, store2)
    r2 = c2.get("/monitoring/remove?domain=x.com.br&token=wrong")
    assert "inválido" in r2.text and store2.removed is None


# --- admin protegido ------------------------------------------------------- #

def test_admin_monitoring_requires_jwt(monkeypatch):
    c = _client(monkeypatch, FakeStore())
    assert c.get("/monitoring/admin/list").status_code == 401
    assert c.get("/monitoring/admin/stats").status_code == 401
    # público continua livre
    assert c.get("/monitoring/sites").status_code == 200
