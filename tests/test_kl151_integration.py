"""KL-151 Prompt 4/4 — integração: grace period, RPM por key, Enterprise (redação de terceiro +
CNPJ), audit log e revogação. Offline (TestClient + FakeStore; engine e Redis mockados)."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

import api.main as m
from api import auth_users
from api import gate as g
from security_gate.models import GateReport, Result, Severity, Status

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
_ENT = {"id": 4, "name": "Enterprise", "slug": "enterprise", "scans_per_day": -1, "max_domains": -1,
        "checks_allowed": ["all"], "scan_third_party": True}
_PLANS = {1: _FREE, 2: _PRO, 4: _ENT}


class FakeRedis:
    def __init__(self):
        self.d = {}

    async def incr(self, k):
        self.d[k] = self.d.get(k, 0) + 1
        return self.d[k]

    async def expire(self, k, ttl):
        pass


class FakeStore:
    def __init__(self, plan=_FREE, verified=True):
        self.plan = plan
        self.verified = verified
        self.keys = []          # api keys
        self.kid = 1
        self.projects = [{"id": 5, "account_id": 10, "domain": "acme.com.br", "verified": verified,
                          "verification_method": "dns_txt" if verified else None}]
        self.runs = []
        self.rid = 100
        self.audits = []
        self.users = {10: {"id": 10, "email": "dev@acme.com", "is_active": True, "account_level": 2,
                           "account_type": "developer", "name": "Dev", "role": "owner",
                           "gate_plan_id": plan["id"], "gate_trial_ends_at": None, "company_cnpj": None}}
        self.emails = {"dev@acme.com": self.users[10]}

    # users
    async def get_user_by_id(self, uid):
        return self.users.get(int(uid))

    async def get_user_by_email(self, email):
        return self.emails.get((email or "").lower())

    async def get_account_gate_fields(self, account_id):
        u = self.users.get(int(account_id))
        return None if not u else {"id": account_id, "gate_plan_id": u["gate_plan_id"],
                                   "gate_trial_ends_at": u.get("gate_trial_ends_at"),
                                   "account_type": u["account_type"],
                                   # KL-153 — KYC completo → scan devolve resultado detalhado.
                                   "email_confirmed": True, "kyc_completed": True,
                                   "kyc_completed_at": None, "suspended": False, "cpf": None,
                                   "address": None, "phone": None, "phone_verified": False}

    async def get_gate_plan(self, pid):
        return dict(_PLANS[pid]) if pid in _PLANS else None

    async def get_gate_plan_by_slug(self, slug):
        return next((dict(p) for p in _PLANS.values() if p["slug"] == slug), None)

    async def set_account_gate_plan(self, account_id, plan_id, trial_started_at=None, trial_ends_at=None):
        u = self.users.get(account_id)
        if u:
            u["gate_plan_id"] = plan_id
            u["gate_trial_ends_at"] = trial_ends_at
        return bool(u)

    async def set_enterprise_fields(self, account_id, cnpj=None, contract_url=None, notes=None):
        u = self.users.get(int(account_id))
        if u and cnpj:
            u["company_cnpj"] = cnpj
        return bool(u)

    async def list_gate_dev_accounts(self, limit=200):
        return [{"id": u["id"], "email": u["email"], "account_type": u["account_type"],
                 "plan_slug": _PLANS.get(u["gate_plan_id"], {}).get("slug"), "scans_today": 0,
                 "project_count": 1, "key_prefix": "KLM_ab", "company_cnpj": u.get("company_cnpj"),
                 "gate_trial_ends_at": None} for u in self.users.values()]

    # keys
    async def get_gate_api_key_by_hash(self, key_hash):
        return next((dict(k) for k in self.keys if k["key_hash"] == key_hash), None)

    async def touch_gate_api_key(self, key_id):
        pass

    async def create_gate_api_key(self, account_id, key_prefix, key_hash, name="default"):
        k = {"id": self.kid, "account_id": account_id, "key_prefix": key_prefix, "key_hash": key_hash,
             "name": name, "is_active": True, "created_at": _now(), "last_used_at": None,
             "revoked_at": None, "grace_expires_at": None}
        self.keys.append(k)
        self.kid += 1
        return dict(k)

    async def revoke_gate_api_keys_with_grace(self, account_id, grace_minutes=60):
        out = []
        for k in self.keys:
            if k["account_id"] == account_id and k["is_active"]:
                k["is_active"] = False
                k["grace_expires_at"] = _now() + timedelta(minutes=grace_minutes)
                out.append(k["key_prefix"])
        return out

    async def list_gate_api_keys(self, account_id):
        return [dict(k) for k in self.keys if k["account_id"] == account_id]

    # projects / runs
    async def get_gate_project_by_domain(self, account_id, domain):
        return next((dict(p) for p in self.projects
                     if p["account_id"] == account_id and p["domain"] == domain), None)

    async def list_gate_projects(self, account_id):
        return [dict(p) for p in self.projects if p["account_id"] == account_id]

    async def count_gate_runs_today(self, account_id):
        return sum(1 for r in self.runs if r["account_id"] == account_id)

    async def create_gate_run(self, project_id, account_id, url, score, passed, fail_on,
                              duration_ms, results, checks_run, checks_blocked, metadata=None):
        self.runs.append({"id": self.rid, "account_id": account_id, "results": results})
        self.rid += 1
        return self.rid - 1

    # audit
    async def insert_gate_audit(self, account_id, action, key_id=None, target_domain=None,
                                detail=None, ip_address=None, user_agent=None, cpf=None,
                                url_scanned=None, domain=None, score=None, passed=None):
        self.audits.append({"account_id": account_id, "action": action, "key_id": key_id,
                            "target_domain": target_domain, "detail": detail or {},
                            "ip_address": ip_address, "cpf": cpf, "url_scanned": url_scanned,
                            "domain": domain, "score": score, "passed": passed,
                            "created_at": _now()})

    async def list_gate_audit(self, account_id=None, action=None, limit=50):
        rows = [a for a in self.audits
                if (account_id is None or a["account_id"] == account_id)
                and (action is None or a["action"] == action)]
        return list(reversed(rows))[:limit]


def _key(store, account_id=10, active=True, grace=None):
    full = "KLM_" + ("a" * 32 if active else "b" * 32)
    store.keys.append({"id": len(store.keys) + 1, "account_id": account_id, "key_prefix": full[:8],
                       "key_hash": g._hash_key(full), "name": "d", "is_active": active,
                       "created_at": _now(), "last_used_at": None, "revoked_at": None,
                       "grace_expires_at": grace})
    return full


@pytest.fixture
def store(monkeypatch):
    s = FakeStore()
    monkeypatch.setenv("JWT_SECRET", SECRET)
    monkeypatch.setenv("ADMIN_USER", "op")
    monkeypatch.setattr(m, "get_target_store", lambda: s)
    monkeypatch.setattr(g, "get_target_store", lambda: s)
    import discovery.store as ds
    monkeypatch.setattr(ds, "get_target_store", lambda: s)
    monkeypatch.setattr(m, "_cache", None)
    monkeypatch.setattr(m, "_spawn", lambda coro: coro.close())
    monkeypatch.setattr(g, "_scan_redis", lambda: None)   # sem RPM por padrão
    return s


@pytest.fixture
def client(store):
    return TestClient(m.app, raise_server_exceptions=False)


def _admin():
    return {"Authorization": f"Bearer {m._create_token('op')}"}


def _report(results, dur=100):
    return GateReport(url="https://acme.com.br",
                      results=[Result(c, cat, "/secret/path", st, sev, "detalhe sensível")
                               for (c, cat, sev, st) in results], duration_ms=dur)


def _mock_engine(monkeypatch, report):
    async def _fake(url, timeout=60, checks=None, config=None, deploy_ts=None):
        return report
    monkeypatch.setattr(g, "run_all", _fake)


# =========================================================================== #
# Grace period (rotação de key)
# =========================================================================== #

def test_grace_key_still_authenticates(client, store):
    grace_key = _key(store, active=False, grace=_now() + timedelta(minutes=30))
    assert client.get("/gate/projects", headers={"X-API-Key": grace_key}).status_code == 200


def test_grace_expired_rejected(client, store):
    expired = _key(store, active=False, grace=_now() - timedelta(minutes=1))
    assert client.get("/gate/projects", headers={"X-API-Key": expired}).status_code == 401


def test_revoked_no_grace_rejected(client, store):
    revoked = _key(store, active=False, grace=None)
    assert client.get("/gate/projects", headers={"X-API-Key": revoked}).status_code == 401


# =========================================================================== #
# RPM por key (Redis mockado)
# =========================================================================== #

def test_rpm_limit_free_11th_429(client, store, monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr(g, "_scan_redis", lambda: fake)   # Free RPM = 10 (mesma instância)
    key = _key(store)
    for i in range(10):
        assert client.get("/gate/projects", headers={"X-API-Key": key}).status_code == 200
    assert client.get("/gate/projects", headers={"X-API-Key": key}).status_code == 429


# =========================================================================== #
# Enterprise — redação de scan de terceiro
# =========================================================================== #

def test_enterprise_third_party_redacts(monkeypatch):
    s = FakeStore(plan=_ENT, verified=False)   # domínio NÃO verificado + Enterprise scan_third_party
    monkeypatch.setenv("JWT_SECRET", SECRET)
    monkeypatch.setattr(m, "get_target_store", lambda: s)
    monkeypatch.setattr(g, "get_target_store", lambda: s)
    monkeypatch.setattr(m, "_cache", None)
    monkeypatch.setattr(m, "_spawn", lambda c: c.close())
    monkeypatch.setattr(g, "_scan_redis", lambda: None)
    _mock_engine(monkeypatch, _report([
        ("credential_generic", "credentials", Severity.CRITICAL, Status.FAIL),
        ("env_exposed", "exposure", Severity.CRITICAL, Status.FAIL)]))
    key = _key(s)
    c = TestClient(m.app, raise_server_exceptions=False)
    r = c.post("/gate/scan", json={"url": "https://acme.com.br"}, headers={"X-API-Key": key})
    assert r.status_code == 200
    for res in r.json()["results"]:
        assert res["path"] == "[redacted]"                       # nunca vaza o caminho real
        assert "detalhe sensível" not in res["detail"]           # nem o detalhe cru


def test_third_party_without_enterprise_403(client, store, monkeypatch):
    store.plan = _FREE
    store.projects[0]["verified"] = False    # não verificado + plano SEM scan_third_party
    key = _key(store)
    r = client.post("/gate/scan", json={"url": "https://acme.com.br"}, headers={"X-API-Key": key})
    assert r.status_code == 403


# =========================================================================== #
# Enterprise CNPJ (admin)
# =========================================================================== #

def test_admin_set_enterprise_cnpj(client, store):
    r = client.post("/admin/gate/accounts/10/enterprise",
                    json={"cnpj": "12.345.678/0001-90", "contract_url": "https://x/c.pdf"},
                    headers=_admin())
    assert r.status_code == 200
    assert store.users[10]["company_cnpj"] == "12.345.678/0001-90"
    # visível no admin de contas
    accts = client.get("/admin/gate/accounts", headers=_admin()).json()["accounts"]
    assert accts[0]["company_cnpj"] == "12.345.678/0001-90"


# =========================================================================== #
# Audit log
# =========================================================================== #

def test_scan_writes_audit(client, store, monkeypatch):
    _mock_engine(monkeypatch, _report([("ssl_valid", "ssl", Severity.CRITICAL, Status.PASS)]))
    key = _key(store)
    client.post("/gate/scan", json={"url": "https://acme.com.br"}, headers={"X-API-Key": key})
    scan_audits = [a for a in store.audits if a["action"] == "scan"]
    assert len(scan_audits) == 1
    a = scan_audits[0]
    assert a["target_domain"] == "acme.com.br" and a["detail"]["score"] == 100
    assert a["key_id"] is not None


def test_scan_blocked_writes_audit(client, store, monkeypatch):
    _mock_engine(monkeypatch, _report([("ssl_valid", "ssl", Severity.CRITICAL, Status.PASS)]))
    key = _key(store)
    for _ in range(5):   # Free = 5/dia (fallback DB → count_gate_runs_today)
        client.post("/gate/scan", json={"url": "https://acme.com.br"}, headers={"X-API-Key": key})
    r = client.post("/gate/scan", json={"url": "https://acme.com.br"}, headers={"X-API-Key": key})
    assert r.status_code == 429
    assert any(a["action"] == "scan_blocked" and a["detail"]["reason"] == "daily_limit"
               for a in store.audits)


def test_admin_audit_list_and_filter(client, store):
    _run(store.insert_gate_audit(10, "scan", target_domain="acme.com.br", detail={"score": 90}))
    _run(store.insert_gate_audit(10, "key_regenerated", detail={"new_prefix": "KLM_xy"}))
    _run(store.insert_gate_audit(77, "scan", target_domain="outro.com"))
    allrows = client.get("/admin/gate/audit", headers=_admin()).json()["audit"]
    assert len(allrows) == 3
    filtered = client.get("/admin/gate/audit?action=scan", headers=_admin()).json()["audit"]
    assert len(filtered) == 2 and all(a["action"] == "scan" for a in filtered)
    per_account = client.get("/admin/gate/audit?account_id=77", headers=_admin()).json()["audit"]
    assert len(per_account) == 1


def test_dev_audit_own_only(client, store):
    _run(store.insert_gate_audit(10, "scan", target_domain="acme.com.br"))
    _run(store.insert_gate_audit(77, "scan", target_domain="outro.com"))   # de outra conta
    key = _key(store, account_id=10)
    rows = client.get("/account/gate/audit", headers={"X-API-Key": key}).json()["audit"]
    assert len(rows) == 1 and rows[0]["account_id"] == 10   # nunca vê o de outros


def test_admin_audit_requires_admin(client, store):
    assert client.get("/admin/gate/audit").status_code == 401


# =========================================================================== #
# Plano efetivo (trial) — reconfirmação de ponta a ponta
# =========================================================================== #

def test_trial_active_effective_pro(store):
    store.users[10]["gate_trial_ends_at"] = _now() + timedelta(days=5)
    store.users[10]["gate_plan_id"] = 1
    plan = _run(g.get_effective_gate_plan(10))
    assert plan["slug"] == "pro" and len(g.get_allowed_checks(plan)) == 9


def test_trial_expired_effective_free(store):
    store.users[10]["gate_trial_ends_at"] = _now() - timedelta(days=1)
    plan = _run(g.get_effective_gate_plan(10))
    assert plan["slug"] == "free" and len(g.get_allowed_checks(plan)) == 4
