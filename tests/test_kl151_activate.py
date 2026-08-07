"""Fix — ativação do Security Gate para conta EXISTENTE (owner/técnico logado).

Cobre: POST /account/gate/activate (owner→both + key 1x + trial Pro; idempotência p/ developer/both;
key não regerada se já existe; 401 sem sessão; key só como hash), GET /account/gate/status (CTA da
landing) e o 409 estruturado do POST /gate/register quando o e-mail já tem conta. Offline (FakeStore).
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

import api.main as m
from api import auth_users
from api import gate as g

SECRET = "k" * 64


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _now():
    return datetime.now(timezone.utc)


_FREE = {"id": 1, "name": "Free", "slug": "free", "scans_per_day": 5, "max_domains": 1,
         "checks_allowed": ["headers", "ssl", "exposure", "https_redirect"], "scan_third_party": False}
_PRO = {"id": 2, "name": "Pro", "slug": "pro", "scans_per_day": 50, "max_domains": 10,
        "checks_allowed": ["headers", "ssl", "exposure", "https_redirect", "credentials", "cors",
                           "cookies", "api", "infrastructure"], "scan_third_party": False}
_PLANS = {1: _FREE, 2: _PRO}


class FakeStore:
    def __init__(self):
        self.keys = []
        self.kid = 1
        self.audits = []
        # 20 = owner sem Gate · 21 = developer · 22 = both · 23 = owner que já tem uma key
        self.users = {
            20: {"id": 20, "email": "owner@acme.com", "is_active": True, "account_level": 2,
                 "account_type": "owner", "gate_plan_id": None, "gate_trial_ends_at": None},
            21: {"id": 21, "email": "dev@acme.com", "is_active": True, "account_level": 2,
                 "account_type": "developer", "gate_plan_id": 1, "gate_trial_ends_at": None},
            22: {"id": 22, "email": "both@acme.com", "is_active": True, "account_level": 2,
                 "account_type": "both", "gate_plan_id": 2, "gate_trial_ends_at": None},
            23: {"id": 23, "email": "haskey@acme.com", "is_active": True, "account_level": 2,
                 "account_type": "owner", "gate_plan_id": None, "gate_trial_ends_at": None},
        }
        self.emails = {u["email"]: u for u in self.users.values()}

    async def get_user_by_id(self, uid):
        return self.users.get(int(uid))

    async def get_user_by_email(self, email):
        return self.emails.get((email or "").lower())

    async def get_account_gate_fields(self, account_id):
        u = self.users.get(int(account_id))
        return None if not u else {"id": account_id, "gate_plan_id": u["gate_plan_id"],
                                   "gate_trial_ends_at": u.get("gate_trial_ends_at"),
                                   "account_type": u["account_type"]}

    async def set_account_type(self, account_id, account_type):
        u = self.users.get(int(account_id))
        if u:
            u["account_type"] = account_type
        return bool(u)

    async def set_account_gate_plan(self, account_id, plan_id, trial_started_at=None, trial_ends_at=None):
        u = self.users.get(int(account_id))
        if u:
            u["gate_plan_id"] = plan_id
            u["gate_trial_ends_at"] = trial_ends_at
        return bool(u)

    async def get_gate_plan(self, pid):
        return dict(_PLANS[pid]) if pid in _PLANS else None

    async def get_gate_plan_by_slug(self, slug):
        return next((dict(p) for p in _PLANS.values() if p["slug"] == slug), None)

    async def create_gate_api_key(self, account_id, key_prefix, key_hash, name="default"):
        k = {"id": self.kid, "account_id": account_id, "key_prefix": key_prefix, "key_hash": key_hash,
             "name": name, "is_active": True, "created_at": _now(), "last_used_at": None,
             "revoked_at": None, "grace_expires_at": None}
        self.keys.append(k)
        self.kid += 1
        return dict(k)

    async def list_gate_api_keys(self, account_id):
        return [dict(k) for k in self.keys if k["account_id"] == int(account_id)]

    async def insert_gate_audit(self, account_id, action, key_id=None, target_domain=None,
                                detail=None, ip_address=None, user_agent=None):
        self.audits.append({"account_id": account_id, "action": action, "detail": detail or {},
                            "created_at": _now()})


@pytest.fixture
def store(monkeypatch):
    s = FakeStore()
    monkeypatch.setenv("JWT_SECRET", SECRET)
    monkeypatch.setattr(m, "get_target_store", lambda: s)
    monkeypatch.setattr(g, "get_target_store", lambda: s)
    import discovery.store as ds
    monkeypatch.setattr(ds, "get_target_store", lambda: s)
    monkeypatch.setattr(m, "_cache", None)
    monkeypatch.setattr(m, "_spawn", lambda coro: coro.close())
    monkeypatch.setattr(g, "_scan_redis", lambda: None)
    return s


@pytest.fixture
def client(store):
    return TestClient(m.app, raise_server_exceptions=False)


def _auth(uid, store):
    return {"Authorization": f"Bearer {auth_users.create_user_token(store.users[uid])}"}


# =========================================================================== #
# POST /account/gate/activate
# =========================================================================== #

def test_owner_activates_gate(client, store):
    r = client.post("/account/gate/activate", headers=_auth(20, store))
    assert r.status_code == 200
    d = r.json()
    assert d["status"] == "activated"
    assert store.users[20]["account_type"] == "both"           # owner → both
    assert d["api_key"] and d["api_key"].startswith("KLM_")     # key exibida UMA VEZ
    assert d["plan"] == "Pro"                                   # trial Pro efetivo
    assert d["trial_ends_at"]


def test_activation_key_stored_as_hash_only(client, store):
    r = client.post("/account/gate/activate", headers=_auth(20, store))
    full = r.json()["api_key"]
    stored = store.keys[0]
    assert stored["key_hash"] == g._hash_key(full)              # só o hash no banco
    assert full not in (stored["key_hash"], stored["key_prefix"])   # nunca o valor cru
    assert stored["key_prefix"] == full[:8]


def test_activation_grants_pro_trial(client, store):
    client.post("/account/gate/activate", headers=_auth(20, store))
    plan = _run(g.get_effective_gate_plan(20))
    assert plan["slug"] == "pro"
    assert _run(store.get_account_gate_fields(20))["gate_plan_id"] == 1   # base Free


def test_developer_already_active(client, store):
    r = client.post("/account/gate/activate", headers=_auth(21, store))
    assert r.status_code == 200
    assert r.json()["status"] == "already_active"
    assert store.users[21]["account_type"] == "developer"       # inalterado


def test_both_already_active(client, store):
    r = client.post("/account/gate/activate", headers=_auth(22, store))
    assert r.json()["status"] == "already_active"


def test_activate_requires_session(client, store):
    assert client.post("/account/gate/activate").status_code == 401


def test_activate_does_not_regenerate_existing_key(client, store):
    # owner (23) que já tem uma key ativa: ativa o Gate mas NÃO regera a key.
    _run(store.create_gate_api_key(23, "KLM_old0", g._hash_key("KLM_old0"), "default"))
    r = client.post("/account/gate/activate", headers=_auth(23, store))
    d = r.json()
    assert d["status"] == "activated"
    assert d["api_key"] is None                                 # não exibe de novo
    assert d["has_key"] is True
    assert len([k for k in store.keys if k["account_id"] == 23]) == 1


def test_activation_writes_audit(client, store):
    client.post("/account/gate/activate", headers=_auth(20, store))
    actions = [a["action"] for a in store.audits if a["account_id"] == 20]
    assert "gate_activated" in actions
    ga = next(a for a in store.audits if a["action"] == "gate_activated")
    assert ga["detail"] == {"previous_type": "owner", "new_type": "both"}


# =========================================================================== #
# GET /account/gate/status  (CTA da landing)
# =========================================================================== #

def test_status_logged_out_401(client, store):
    assert client.get("/account/gate/status").status_code == 401


def test_status_inactive(client, store):
    d = client.get("/account/gate/status", headers=_auth(20, store)).json()
    assert d["logged_in"] is True and d["gate_active"] is False


def test_status_active(client, store):
    d = client.get("/account/gate/status", headers=_auth(22, store)).json()
    assert d["gate_active"] is True and d["dashboard_url"] == "/dashboard/gate"


# =========================================================================== #
# POST /gate/register  — 409 estruturado quando o e-mail já existe
# =========================================================================== #

def _register(client, email):
    return client.post("/gate/register", json={
        "email": email, "password": "password123", "project_url": "https://acme.com.br"})


def test_register_existing_account_not_active(client, store):
    r = _register(client, "owner@acme.com")           # conta existe, Gate NÃO ativo
    assert r.status_code == 409
    d = r.json()
    assert d["error"] == "account_exists"
    assert d["login_url"] == "/entrar"
    assert d["activate_after_login"] is True


def test_register_existing_account_gate_active(client, store):
    r = _register(client, "dev@acme.com")             # conta existe, Gate JÁ ativo
    assert r.status_code == 409
    d = r.json()
    assert d["error"] == "account_exists"
    assert "activate_after_login" not in d            # já ativo → só logar
