"""KL-151 Prompt 3/4 — endpoints do portal (dual-auth JWT) + planos públicos + admin de planos.

O frontend (Astro/React) é validado pelo build; aqui exercitamos os endpoints que ele consome."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

import api.main as m
from api import auth_users
from api import gate as g

SECRET = "k" * 64

_PLANS = [
    {"id": 1, "name": "Free", "slug": "free", "price_brl": 0, "scans_per_day": 5, "max_domains": 1,
     "history_days": 7, "checks_allowed": ["headers", "ssl", "exposure", "https_redirect"],
     "scan_third_party": False, "notifications": ["email"], "trial_days": 0, "active": True},
    {"id": 2, "name": "Pro", "slug": "pro", "price_brl": 4900, "scans_per_day": 50, "max_domains": 10,
     "history_days": 90, "checks_allowed": ["headers", "ssl", "exposure", "https_redirect",
     "credentials", "cors", "cookies", "api", "infrastructure"], "scan_third_party": False,
     "notifications": ["email", "webhook"], "trial_days": 0, "active": True},
    {"id": 3, "name": "Team", "slug": "team", "price_brl": 14900, "scans_per_day": 200,
     "max_domains": 50, "history_days": 365, "checks_allowed": ["all"], "scan_third_party": False,
     "notifications": ["email", "webhook", "slack"], "trial_days": 0, "active": True},
    {"id": 4, "name": "Enterprise", "slug": "enterprise", "price_brl": 0, "scans_per_day": -1,
     "max_domains": -1, "history_days": -1, "checks_allowed": ["all"], "scan_third_party": True,
     "notifications": ["email", "webhook", "slack"], "trial_days": 0, "active": True},
]


class FakeStore:
    def __init__(self):
        self.plans = [dict(p) for p in _PLANS]
        self.next_plan_id = 5
        self.by_id = {}
        self.keys = []
        self.projects = []
        self.accounts_usage = []

    async def get_user_by_id(self, uid):
        return self.by_id.get(int(uid))

    async def get_account_gate_fields(self, account_id):
        u = self.by_id.get(int(account_id))
        if not u:
            return None
        return {"id": account_id, "gate_plan_id": u.get("gate_plan_id", 1),
                "gate_trial_ends_at": u.get("gate_trial_ends_at")}

    async def get_gate_plan(self, plan_id):
        return next((dict(p) for p in self.plans if p["id"] == plan_id), None) if plan_id else None

    async def get_gate_plan_by_slug(self, slug):
        return next((dict(p) for p in self.plans if p["slug"] == slug), None)

    async def list_gate_plans(self, active_only=True):
        return [dict(p) for p in self.plans if (p["active"] or not active_only)]

    async def list_gate_projects(self, account_id):
        return [dict(p) for p in self.projects if p["account_id"] == account_id]

    async def list_gate_api_keys(self, account_id):
        return [dict(k) for k in self.keys if k["account_id"] == account_id]

    async def create_gate_plan(self, name, slug, **fields):
        if any(p["slug"] == slug for p in self.plans):
            return None
        p = {"id": self.next_plan_id, "name": name, "slug": slug,
             "price_brl": fields.get("price_brl", 0), "scans_per_day": fields.get("scans_per_day", 5),
             "max_domains": fields.get("max_domains", 1), "history_days": fields.get("history_days", 7),
             "checks_allowed": fields.get("checks_allowed", ["headers"]),
             "scan_third_party": fields.get("scan_third_party", False),
             "notifications": fields.get("notifications", ["email"]),
             "trial_days": fields.get("trial_days", 0), "active": fields.get("active", True)}
        self.plans.append(p)
        self.next_plan_id += 1
        return dict(p)

    async def update_gate_plan(self, plan_id, **fields):
        for p in self.plans:
            if p["id"] == plan_id:
                for k, v in fields.items():
                    if k != "slug":
                        p[k] = v
                return dict(p)
        return None

    async def list_gate_dev_accounts(self, limit=200):
        return list(self.accounts_usage)

    async def set_account_gate_plan(self, account_id, plan_id, trial_started_at=None, trial_ends_at=None):
        u = self.by_id.get(account_id)
        if u:
            u["gate_plan_id"] = plan_id
            u["gate_trial_ends_at"] = trial_ends_at
        return bool(u)


@pytest.fixture
def store(monkeypatch):
    s = FakeStore()
    monkeypatch.setenv("JWT_SECRET", SECRET)
    monkeypatch.setenv("ADMIN_USER", "op")
    monkeypatch.setattr(m, "get_target_store", lambda: s)
    monkeypatch.setattr(g, "get_target_store", lambda: s)
    import discovery.store as ds
    monkeypatch.setattr(ds, "get_target_store", lambda: s)
    return s


@pytest.fixture
def client(store):
    return TestClient(m.app, raise_server_exceptions=False)


def _dev(store, email="dev@acme.com", plan_id=1):
    u = {"id": 10, "email": email, "is_active": True, "account_level": 2, "name": "Dev",
         "account_type": "developer", "role": "owner", "gate_plan_id": plan_id,
         "gate_trial_ends_at": None}
    store.by_id[10] = u
    return u


def _cookie(user):
    return {auth_users.USER_COOKIE: auth_users.create_user_token(user)}


def _admin():
    return {"Authorization": f"Bearer {m._create_token('op')}"}


# =========================================================================== #
# GET /gate/plans (público)
# =========================================================================== #

def test_public_plans(client, store):
    r = client.get("/gate/plans")
    assert r.status_code == 200
    plans = r.json()["plans"]
    assert {p["slug"] for p in plans} == {"free", "pro", "team", "enterprise"}
    free = next(p for p in plans if p["slug"] == "free")
    assert free["checks_count"] == 4
    team = next(p for p in plans if p["slug"] == "team")
    assert team["checks_count"] == 18   # ["all"] → todos


# =========================================================================== #
# GET /account/gate/key-info (JWT)
# =========================================================================== #

def test_key_info_masked(client, store):
    dev = _dev(store)
    store.keys.append({"id": 1, "account_id": 10, "key_prefix": "KLM_ab12", "name": "default",
                       "is_active": True, "created_at": datetime.now(timezone.utc),
                       "last_used_at": None})
    r = client.get("/account/gate/key-info", cookies=_cookie(dev))
    assert r.status_code == 200
    body = r.json()
    assert body["has_key"] is True and body["prefix"] == "KLM_ab12"
    assert "…" in body["masked"] and "created_at" in body


def test_key_info_no_key(client, store):
    dev = _dev(store)
    assert client.get("/account/gate/key-info", cookies=_cookie(dev)).json()["has_key"] is False


def test_key_info_no_session_401(client, store):
    assert client.get("/account/gate/key-info").status_code == 401


# =========================================================================== #
# Dual-auth: portal usa JWT (sem X-API-Key)
# =========================================================================== #

def test_portal_projects_via_jwt(client, store):
    dev = _dev(store)
    store.projects.append({"id": 1, "account_id": 10, "domain": "acme.com.br", "verified": True})
    r = client.get("/gate/projects", cookies=_cookie(dev))
    assert r.status_code == 200
    assert r.json()["projects"][0]["domain"] == "acme.com.br"
    assert r.json()["plan"]["slug"] == "free"


def test_portal_no_auth_401(client, store):
    assert client.get("/gate/projects").status_code == 401


# =========================================================================== #
# Admin de planos
# =========================================================================== #

def test_admin_plans_requires_admin(client, store):
    assert client.get("/admin/gate/plans").status_code == 401


def test_admin_list_plans(client, store):
    r = client.get("/admin/gate/plans", headers=_admin())
    assert r.status_code == 200
    assert len(r.json()["plans"]) == 4 and len(r.json()["all_checks"]) == 18


def test_admin_update_plan(client, store):
    r = client.put("/admin/gate/plans/2", json={"scans_per_day": 100, "price_brl": 5900},
                   headers=_admin())
    assert r.status_code == 200 and r.json()["plan"]["scans_per_day"] == 100
    # efeito imediato no store
    assert next(p for p in store.plans if p["id"] == 2)["scans_per_day"] == 100


def test_admin_update_plan_checks(client, store):
    r = client.put("/admin/gate/plans/1", json={"checks_allowed": ["headers", "ssl", "jwt"]},
                   headers=_admin())
    assert r.json()["plan"]["checks_allowed"] == ["headers", "ssl", "jwt"]


def test_admin_create_plan(client, store):
    r = client.post("/admin/gate/plans", json={"name": "Startup", "slug": "startup",
                                               "price_brl": 2900, "scans_per_day": 20,
                                               "checks_allowed": ["headers", "ssl"]},
                    headers=_admin())
    assert r.status_code == 200 and r.json()["plan"]["slug"] == "startup"
    assert any(p["slug"] == "startup" for p in store.plans)


def test_admin_create_plan_duplicate_slug_409(client, store):
    r = client.post("/admin/gate/plans", json={"name": "Free2", "slug": "free"}, headers=_admin())
    assert r.status_code == 409


def test_admin_create_plan_missing_fields_422(client, store):
    assert client.post("/admin/gate/plans", json={"name": "X"}, headers=_admin()).status_code == 422


def test_admin_list_accounts(client, store):
    store.accounts_usage = [{"id": 10, "email": "dev@x.com", "account_type": "developer",
                             "plan_slug": "pro", "scans_today": 12, "project_count": 3,
                             "key_prefix": "KLM_ab", "gate_trial_ends_at": datetime.now(timezone.utc)}]
    r = client.get("/admin/gate/accounts", headers=_admin())
    assert r.status_code == 200
    acc = r.json()["accounts"][0]
    assert acc["email"] == "dev@x.com" and acc["scans_today"] == 12
    assert isinstance(acc["gate_trial_ends_at"], str)   # datetime → ISO


def test_admin_assign_plan(client, store):
    _dev(store)
    r = client.post("/admin/gate/accounts/10/plan", json={"plan_id": 3}, headers=_admin())
    assert r.status_code == 200 and r.json()["plan"] == "team"
    assert store.by_id[10]["gate_plan_id"] == 3


def test_admin_assign_unknown_plan_404(client, store):
    _dev(store)
    assert client.post("/admin/gate/accounts/10/plan", json={"plan_id": 999},
                       headers=_admin()).status_code == 404
