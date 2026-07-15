"""Testes de planos, assinaturas e trial de 30 dias (KL-44 Guardião Digital, P1).

Offline: fake store que PERSISTE plans/subscriptions/history (diferente do stub
não-persistente do test_accounts.py). Cobre a lógica de trial (criar/expirar/estender),
mudança de plano, stats, seed de contas e os endpoints admin.
"""

from __future__ import annotations

import asyncio
from collections import Counter
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

import api.main as m
from api import plans

NOW = datetime.now(timezone.utc)


def _plan(pid, name, max_sites, active=True):
    return {"id": pid, "name": name, "max_sites": max_sites, "is_active": active,
            "price_monthly": 0, "price_yearly": 0}


class FakeSubStore:
    def __init__(self):
        self.plans = {
            "free": _plan("free", "Free", 1),
            "pro": _plan("pro", "Pro", 5),
            "agency": _plan("agency", "Agency", 15),
        }
        self.subs = {}       # account_id -> row
        self.history = []
        self.users = {}      # id -> {email, created_at}
        self._sid = 0

    # planos
    async def get_plan(self, pid):
        return self.plans.get(pid)

    async def list_plans(self, active_only=True):
        return [p for p in self.plans.values() if not active_only or p.get("is_active", True)]

    async def update_plan(self, pid, fields):
        p = self.plans.get(pid)
        if p:
            p.update(fields)
        return p

    # assinaturas
    async def get_subscription_row(self, aid):
        return self.subs.get(aid)

    async def upsert_subscription(self, aid, plan_id, status, trial_ends_at=None,
                                  expires_at=None, billing_cycle="monthly"):
        self._sid += 1
        row = {"id": self._sid, "account_id": aid, "plan_id": plan_id, "status": status,
               "trial_ends_at": trial_ends_at, "started_at": NOW, "expires_at": expires_at,
               "billing_cycle": billing_cycle}
        self.subs[aid] = row
        return row

    async def update_subscription(self, aid, **fields):
        row = self.subs.get(aid)
        if row:
            row.update(fields)
        return row

    async def log_subscription_change(self, aid, old_plan, new_plan, old_status, new_status,
                                      changed_by="system", reason=None):
        self.history.append({"account_id": aid, "old_plan_id": old_plan, "new_plan_id": new_plan,
                             "old_status": old_status, "new_status": new_status,
                             "changed_by": changed_by, "reason": reason})

    async def list_subscription_history(self, aid):
        return [h for h in self.history if h["account_id"] == aid]

    async def subscription_group_counts(self):
        c = Counter()
        for aid in self.users:
            row = self.subs.get(aid)
            c[(row["plan_id"] if row else "free", row["status"] if row else "free")] += 1
        return [{"plan_id": k[0], "status": k[1], "n": v} for k, v in c.items()]

    async def count_trials_expiring(self, days=7):
        lim = NOW + timedelta(days=days)
        return sum(1 for r in self.subs.values()
                   if r["status"] == "trial" and r["trial_ends_at"]
                   and NOW <= r["trial_ends_at"] <= lim)

    async def list_subscribers(self, plan_id=None, status=None, search=None, limit=25, offset=0):
        out = []
        for aid, u in self.users.items():
            row = self.subs.get(aid)
            pid, st = (row["plan_id"], row["status"]) if row else ("free", "free")
            if plan_id and pid != plan_id:
                continue
            if status and st != status:
                continue
            if search and search.lower() not in u["email"].lower():
                continue
            out.append({"account_id": aid, "email": u["email"], "plan_id": pid, "status": st,
                        "trial_ends_at": row["trial_ends_at"] if row else None,
                        "plan_name": self.plans[pid]["name"],
                        "plan_max_sites": self.plans[pid]["max_sites"], "sites": 0})
        return out[offset:offset + limit]

    async def users_without_subscription(self):
        return [{"id": aid, "email": u["email"], "created_at": u["created_at"]}
                for aid, u in self.users.items() if aid not in self.subs]


@pytest.fixture
def store(monkeypatch):
    s = FakeSubStore()
    monkeypatch.setattr(m, "get_target_store", lambda: s)
    monkeypatch.setattr("discovery.store.get_target_store", lambda: s)
    return s


@pytest.fixture
def client(store, monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "k" * 64)
    return TestClient(m.app, raise_server_exceptions=False)


def _admin(monkeypatch):
    monkeypatch.setenv("ADMIN_USER", "op")
    monkeypatch.setenv("ADMIN_PASSWORD", "pw")
    return {"Authorization": f"Bearer {m._create_token('op')}"}


# --- lógica de trial / assinatura ------------------------------------------ #

def test_create_trial_subscription(store):
    store.users[1] = {"email": "a@x.com.br", "created_at": NOW}
    row = asyncio.run(plans.create_subscription(1, "pro", is_trial=True))
    assert row["status"] == "trial" and row["plan_id"] == "pro"
    assert row["trial_ends_at"] is not None
    delta = (row["trial_ends_at"] - NOW).days
    assert 29 <= delta <= 30
    assert store.history[-1]["new_status"] == "trial"


def test_create_free_subscription(store):
    row = asyncio.run(plans.create_subscription(2, "free", is_trial=False))
    assert row["status"] == "free" and row["trial_ends_at"] is None


def test_trial_expiry_on_read(store):
    store.users[3] = {"email": "b@x.com.br", "created_at": NOW}
    asyncio.run(plans.create_subscription(3, "pro", is_trial=True,
                                          trial_ends_at=NOW - timedelta(days=1)))
    sub = asyncio.run(plans.get_subscription(3))
    assert sub["status"] == "expired" and sub["plan_id"] == "free"
    assert sub["max_sites"] == 1  # limites do free após expirar
    assert store.subs[3]["status"] == "expired"  # persistido


def test_extend_trial(store):
    asyncio.run(plans.create_subscription(4, "pro", is_trial=True,
                                          trial_ends_at=NOW + timedelta(days=5)))
    asyncio.run(plans.extend_trial(4, 10))
    sub = asyncio.run(plans.get_subscription(4))
    assert sub["status"] == "trial" and sub["trial_days_left"] >= 14


def test_change_plan_trial_to_free(store):
    asyncio.run(plans.create_subscription(5, "pro", is_trial=True))
    asyncio.run(plans.change_plan(5, "free", reason="downgrade"))
    sub = asyncio.run(plans.get_subscription(5))
    assert sub["plan_id"] == "free" and sub["status"] == "free"


def test_change_plan_trial_keeps_trial(store):
    asyncio.run(plans.create_subscription(6, "pro", is_trial=True))
    asyncio.run(plans.change_plan(6, "agency"))
    sub = asyncio.run(plans.get_subscription(6))
    assert sub["plan_id"] == "agency" and sub["status"] == "trial"
    assert sub["max_sites"] == 15


def test_no_subscription_returns_free(store):
    sub = asyncio.run(plans.get_subscription(999))
    assert sub["plan_id"] == "free" and sub["status"] == "free" and sub["max_sites"] == 1


def test_subscription_stats(store):
    store.users.update({1: {"email": "a@x", "created_at": NOW},
                        2: {"email": "b@x", "created_at": NOW},
                        3: {"email": "c@x", "created_at": NOW}})
    asyncio.run(plans.create_subscription(1, "pro", is_trial=True))
    asyncio.run(plans.create_subscription(2, "pro", is_trial=True))
    asyncio.run(plans.create_subscription(3, "free", is_trial=False))
    stats = asyncio.run(plans.get_subscription_stats())
    assert stats["total_accounts"] == 3
    assert stats["by_plan"].get("pro") == 2 and stats["by_plan"].get("free") == 1
    assert stats["trials_active"] == 2


def test_seed_existing_accounts(store):
    store.users[10] = {"email": "new@x.com.br", "created_at": NOW - timedelta(days=5)}
    store.users[11] = {"email": "old@x.com.br", "created_at": NOW - timedelta(days=60)}
    res = asyncio.run(plans.seed_existing_accounts())
    assert res["pro_trial"] == 1 and res["free"] == 1
    assert store.subs[10]["status"] == "trial" and store.subs[10]["plan_id"] == "pro"
    assert store.subs[11]["status"] == "free" and store.subs[11]["plan_id"] == "free"
    # idempotente: rodar de novo não cria nada
    assert asyncio.run(plans.seed_existing_accounts())["total"] == 0


# --- endpoints admin -------------------------------------------------------- #

def test_admin_plans_endpoint(client, monkeypatch):
    h = _admin(monkeypatch)
    body = client.get("/admin/plans", headers=h).json()
    assert len(body["plans"]) == 3
    assert client.get("/admin/plans", headers=h).status_code == 200


def test_admin_plan_update(client, store, monkeypatch):
    h = _admin(monkeypatch)
    r = client.put("/admin/plans/pro", json={"max_sites": 8, "price_monthly": 2900}, headers=h)
    assert r.status_code == 200 and r.json()["max_sites"] == 8
    assert store.plans["pro"]["max_sites"] == 8


def test_admin_plan_requires_admin(client):
    assert client.get("/admin/plans").status_code == 401


def test_admin_subscriptions_stats_route_before_id(client, store, monkeypatch):
    # /stats não pode ser confundido com /{account_id}
    h = _admin(monkeypatch)
    store.users[1] = {"email": "a@x", "created_at": NOW}
    r = client.get("/admin/subscriptions/stats", headers=h)
    assert r.status_code == 200 and "by_plan" in r.json()


def test_admin_change_plan_endpoint(client, store, monkeypatch):
    h = _admin(monkeypatch)
    store.users[7] = {"email": "g@x", "created_at": NOW}
    asyncio.run(plans.create_subscription(7, "pro", is_trial=True))
    r = client.patch("/admin/subscriptions/7/plan", json={"plan_id": "agency"}, headers=h)
    assert r.status_code == 200 and r.json()["plan_id"] == "agency"


def test_admin_extend_trial_endpoint(client, store, monkeypatch):
    h = _admin(monkeypatch)
    store.users[8] = {"email": "h@x", "created_at": NOW}
    asyncio.run(plans.create_subscription(8, "pro", is_trial=True,
                                          trial_ends_at=NOW + timedelta(days=3)))
    r = client.patch("/admin/subscriptions/8/trial", json={"days": 15}, headers=h)
    assert r.status_code == 200 and r.json()["trial_days_left"] >= 17


def test_admin_bulk_change_plan(client, store, monkeypatch):
    h = _admin(monkeypatch)
    for i in (20, 21):
        store.users[i] = {"email": f"u{i}@x", "created_at": NOW}
        asyncio.run(plans.create_subscription(i, "pro", is_trial=True))
    r = client.post("/admin/subscriptions/bulk",
                    json={"account_ids": [20, 21], "action": "change_plan", "plan_id": "free"},
                    headers=h)
    assert r.status_code == 200 and r.json()["applied"] == 2
    assert store.subs[20]["plan_id"] == "free" and store.subs[21]["plan_id"] == "free"


def test_account_subscription_public(client, store, monkeypatch):
    # endpoint público (cookie/bearer de usuário) — reutiliza a auth de usuário
    from api import auth_users
    monkeypatch.setattr(auth_users, "_secret", lambda: "k" * 64)
    store.users[30] = {"email": "acc@x.com.br", "created_at": NOW}
    asyncio.run(plans.create_subscription(30, "pro", is_trial=True))

    async def _fake_user(request):
        return {"id": 30, "email": "acc@x.com.br", "plan": "pro"}
    monkeypatch.setattr(auth_users, "require_user", _fake_user)
    r = client.get("/account/subscription")
    assert r.status_code == 200 and r.json()["plan_id"] == "pro"
