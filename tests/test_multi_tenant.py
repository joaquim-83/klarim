"""KL-26 — isolamento multi-tenant. User A e User B com sites distintos: nenhum acessa o
site do outro (IDOR → 404), usuário comum não alcança /admin (401), sem vazamento de dados
nem mass assignment. Bidirecional (A↔B). Offline (FakeStore + TestClient).
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import api.main as m
from api import auth_users


class FakeStore:
    def __init__(self):
        # A(10) dono do site 1; B(20) dono do site 2. Ambos nível 3.
        self.users = {
            10: {"id": 10, "email": "a@empresa-a.com.br", "name": "A", "plan": "pro",
                 "account_level": 3, "is_active": True, "email_confirmed": True,
                 "created_at": None, "password_hash": "x"},
            20: {"id": 20, "email": "b@empresa-b.com.br", "name": "B", "plan": "pro",
                 "account_level": 3, "is_active": True, "email_confirmed": True,
                 "created_at": None, "password_hash": "x"},
        }
        self.targets = {
            1: {"id": 1, "domain": "empresa-a.com.br", "url": "https://empresa-a.com.br"},
            2: {"id": 2, "domain": "empresa-b.com.br", "url": "https://empresa-b.com.br"},
        }
        self.links = {(10, 1): {"id": 1, "user_id": 10, "target_id": 1, "is_owner": True},
                      (20, 2): {"id": 2, "user_id": 20, "target_id": 2, "is_owner": True}}
        self.prefs_calls = []

    async def get_user_by_id(self, uid):
        return self.users.get(int(uid))

    async def get_user_site(self, uid, tid):
        return self.links.get((int(uid), int(tid)))

    async def get_target(self, tid):
        return self.targets.get(int(tid))

    async def count_user_sites(self, uid):
        return sum(1 for (u, _t) in self.links if u == int(uid))

    async def list_user_sites(self, uid):
        out = []
        for (u, t), link in self.links.items():
            if u == int(uid):
                tg = self.targets[t]
                out.append({"id": link["id"], "target_id": t, "domain": tg["domain"],
                            "is_owner": link["is_owner"]})
        return out

    async def get_notification_prefs(self, uid):
        return {"bulletin_frequency": None, "bulletin_hour": None, "notify_vigilia": True,
                "notify_bulletin": True, "notify_news": False}

    async def update_notification_prefs(self, uid, fields):
        self.prefs_calls.append((int(uid), dict(fields)))
        base = {"bulletin_frequency": None, "bulletin_hour": None, "notify_vigilia": True,
                "notify_bulletin": True, "notify_news": False}
        base.update(fields)
        return base


@pytest.fixture
def store():
    return FakeStore()


@pytest.fixture
def client(store, monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "x" * 64)
    monkeypatch.setattr(m, "get_target_store", lambda: store)
    monkeypatch.setattr("discovery.store.get_target_store", lambda: store)
    return TestClient(m.app, raise_server_exceptions=False)


def _hdr(store, uid):
    return {"Authorization": f"Bearer {auth_users.create_user_token(store.users[uid])}"}


# --------------------------------------------------------------------------- #
# IDOR — nenhum usuário acessa o site do outro (404), bidirecional
# --------------------------------------------------------------------------- #

# (method, path suffix, body). O site é injetado pela vítima; o atacante é o outro user.
_SITE_ENDPOINTS = [
    ("GET", "", None),
    ("DELETE", "", None),
    ("GET", "/monitoring", None),
    ("PUT", "/monitoring", {"vigilias": {}}),
    ("PUT", "/profile", {"company_name": "x"}),
    ("PUT", "/visibility", {"public_visible": True}),
    ("GET", "/seal", None),
    ("PUT", "/seal", {"enabled": True, "style": "badge"}),
    ("POST", "/verify/start", {"method": "dns_txt"}),
    ("POST", "/verify/check", None),
]


@pytest.mark.parametrize("attacker,victim_site", [(10, 2), (20, 1)])
@pytest.mark.parametrize("method,suffix,body", _SITE_ENDPOINTS)
def test_idor_site_endpoints_return_404(client, store, attacker, victim_site, method, suffix, body):
    path = f"/account/sites/{victim_site}{suffix}"
    r = client.request(method, path, headers=_hdr(store, attacker), json=body)
    assert r.status_code == 404, f"{method} {path} devolveu {r.status_code} (esperava 404)"


def test_owner_reaches_own_site_monitoring(client, store, monkeypatch):
    # sanidade: o próprio dono NÃO leva 404 (o 404 é isolamento, não bug geral).
    async def allowed(uid):
        return ["ssl", "domain", "score", "email", "reputation"]
    monkeypatch.setattr(m, "_vigilia_allowed_types", allowed)

    async def _lv(uid, domain):
        return []
    monkeypatch.setattr(store, "list_site_vigilias", _lv, raising=False)

    async def _latest(tid):
        return {"score": 70, "semaphore": "amarelo", "checks_json": {}, "scanned_at": None}
    monkeypatch.setattr(store, "get_latest_scan_full", _latest, raising=False)
    r = client.get("/account/sites/1/monitoring", headers=_hdr(store, 10))
    assert r.status_code == 200


@pytest.mark.parametrize("attacker,victim_site", [(10, 2), (20, 1)])
def test_idor_intelligence_is_admin_only(client, store, attacker, victim_site):
    # /admin/* → bloqueado pelo middleware admin (401) antes de chegar ao handler.
    r = client.get(f"/admin/targets/{victim_site}/intelligence", headers=_hdr(store, attacker))
    assert r.status_code == 401


# --------------------------------------------------------------------------- #
# Escalação vertical — usuário comum não alcança /admin/*
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("method,path", [
    ("GET", "/targets"), ("GET", "/scans"), ("GET", "/admin/targets"),
    ("GET", "/alerts"), ("POST", "/admin/targets/1"),
])
def test_user_cannot_reach_admin(client, store, method, path):
    r = client.request(method, path, headers=_hdr(store, 10), json={})
    assert r.status_code == 401  # token de usuário (typ=user) não vale no admin


def test_no_auth_admin_is_401(client):
    assert client.get("/targets").status_code == 401


# --------------------------------------------------------------------------- #
# Vazamento de dados
# --------------------------------------------------------------------------- #

def test_me_returns_only_own_data(client, store):
    r = client.get("/account/me", headers=_hdr(store, 10))
    assert r.status_code == 200
    body = r.text
    assert "a@empresa-a.com.br" in body
    assert "b@empresa-b.com.br" not in body  # nunca o e-mail do outro


def test_monitoring_status_does_not_leak_other_owner(client, store):
    r = client.get("/account/monitoring-status?domain=empresa-b.com.br", headers=_hdr(store, 10))
    assert r.status_code == 200
    d = r.json()
    assert d["logged_in"] is True and d["monitoring"] is False
    assert "b@empresa-b.com.br" not in r.text  # não vaza o dono real


def test_monitoring_status_anonymous(client):
    r = client.get("/account/monitoring-status?domain=empresa-a.com.br")
    assert r.status_code == 200
    assert r.json() == {"logged_in": False, "monitoring": False}


# --------------------------------------------------------------------------- #
# Mass assignment — campos não declarados são IGNORADOS (extra='ignore')
# --------------------------------------------------------------------------- #

def test_signup_inline_body_ignores_privilege_fields():
    b = m.SignupInlineBody(email="a@b.com", domain="x.com",
                           account_level=3, plan="agency", is_owner=True)
    assert b.email == "a@b.com" and b.domain == "x.com"
    assert not hasattr(b, "account_level") and not hasattr(b, "plan")


def test_notification_prefs_body_ignores_account_level():
    b = m.NotificationPrefsBody(bulletin_frequency="monthly", account_level=3)
    assert not hasattr(b, "account_level")


def test_notification_prefs_endpoint_drops_account_level(client, store):
    r = client.put("/account/notification-preferences", headers=_hdr(store, 10),
                   json={"notify_news": True, "account_level": 3, "plan": "agency"})
    assert r.status_code == 200
    uid, fields = store.prefs_calls[-1]
    assert uid == 10 and "account_level" not in fields and "plan" not in fields
    assert fields.get("notify_news") is True


def test_profile_body_ignores_user_id():
    b = m.OwnerProfileBody(company_name="X", user_id=999, target_id=1)
    assert b.company_name == "X" and not hasattr(b, "user_id")
