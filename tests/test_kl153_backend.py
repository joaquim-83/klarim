"""KL-153 Prompt 1/2 — backend do Security Gate: KYC + rate limiting 3 camadas + scan avulso +
resultado filtrado por KYC + upgrade + status. Offline (engine mockada; Redis é um fake em memória).

O foco é a lógica de autorização/limite/filtragem — não a rede. `run_all` (a engine) é mockada;
o Redis do rate limiter é um fake determinístico; a AbacatePay do upgrade é um seam mockado."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

import api.main as m
from api import gate as g
from api import gate_rate_limiter as rl
from api.validators import validate_cpf
from security_gate.models import GateReport, Result, Severity, Status

SECRET = "k" * 64
# CPF válido de teste (dígitos verificadores corretos).
VALID_CPF = "529.982.247-25"


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _now():
    return datetime.now(timezone.utc)


# =========================================================================== #
# Fake Redis (async) — suporta o que o rate limiter usa.
# =========================================================================== #

class FakeRedis:
    def __init__(self):
        self.kv, self.sets, self.ttls = {}, {}, {}

    async def incr(self, k):
        self.kv[k] = int(self.kv.get(k, 0)) + 1
        return self.kv[k]

    async def expire(self, k, ttl):
        self.ttls[k] = int(ttl); return True

    async def ttl(self, k):
        return self.ttls.get(k, -1)

    async def set(self, k, v, nx=False, ex=None):
        if nx and k in self.kv:
            return False
        self.kv[k] = v
        if ex is not None:
            self.ttls[k] = int(ex)
        return True

    async def get(self, k):
        return self.kv.get(k)

    async def sadd(self, k, *vals):
        s = self.sets.setdefault(k, set())
        before = len(s); s.update(vals)
        return len(s) - before

    async def scard(self, k):
        return len(self.sets.get(k, set()))

    async def smembers(self, k):
        return set(self.sets.get(k, set()))


# =========================================================================== #
# Planos + FakeStore
# =========================================================================== #

_FREE = {"id": 1, "name": "Free", "slug": "free", "price_brl": 0, "scans_per_day": 5,
         "max_domains": 1, "checks_allowed": ["headers", "ssl", "exposure", "https_redirect"],
         "scan_third_party": False}
_PRO = {"id": 2, "name": "Pro", "slug": "pro", "price_brl": 4900, "scans_per_day": 50,
        "max_domains": 10, "checks_allowed": ["headers", "ssl", "exposure", "credentials"],
        "scan_third_party": False}
_TEAM = {"id": 3, "name": "Team", "slug": "team", "price_brl": 14900, "scans_per_day": 200,
         "max_domains": 50, "checks_allowed": ["all"], "scan_third_party": False}
_PLANS = {1: _FREE, 2: _PRO, 3: _TEAM}


class FakeStore:
    def __init__(self):
        self.users = {
            10: {"id": 10, "email": "dev@acme.com", "is_active": True, "account_level": 2,
                 "account_type": "developer", "gate_plan_id": 1, "gate_trial_ends_at": None,
                 "email_confirmed": True, "kyc_completed": False, "kyc_completed_at": None,
                 "suspended": False, "cpf": None, "address": None, "phone": None,
                 "phone_verified": False},
        }
        self.emails = {u["email"]: u for u in self.users.values()}
        self.keys, self.kid = [], 1
        self.projects = []   # scan avulso por default (sem projeto)
        self.runs, self.rid = [], 100
        self.audits = []
        self.payments = []

    # --- users --- #
    async def get_user_by_id(self, uid):
        return self.users.get(int(uid))

    async def get_user_by_email(self, email):
        return self.emails.get((email or "").lower())

    async def get_account_gate_fields(self, account_id):
        u = self.users.get(int(account_id))
        if not u:
            return None
        return {k: u.get(k) for k in ("id", "email", "account_type", "gate_plan_id",
                                      "gate_trial_ends_at", "email_confirmed", "kyc_completed",
                                      "kyc_completed_at", "suspended", "cpf", "address", "phone",
                                      "phone_verified")}

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

    # --- KYC / suspensão (KL-153) --- #
    async def is_cpf_taken(self, cpf, exclude_account_id=None):
        return any(u.get("cpf") == cpf and u["id"] != exclude_account_id for u in self.users.values())

    async def update_user_kyc(self, account_id, cpf, address, phone, phone_verified,
                              kyc_completed, kyc_completed_at=None):
        u = self.users.get(int(account_id))
        if not u:
            return False
        u.update(cpf=cpf, address=address, phone=phone, phone_verified=phone_verified,
                 kyc_completed=kyc_completed)
        if kyc_completed:
            u["kyc_completed_at"] = u.get("kyc_completed_at") or kyc_completed_at
        else:
            u["kyc_completed_at"] = None
        return True

    async def set_user_suspended(self, account_id, suspended):
        u = self.users.get(int(account_id))
        if u:
            u["suspended"] = bool(suspended)
        return bool(u)

    # --- planos --- #
    async def get_gate_plan(self, pid):
        return dict(_PLANS[pid]) if pid in _PLANS else None

    async def get_gate_plan_by_slug(self, slug):
        return next((dict(p) for p in _PLANS.values() if p["slug"] == slug), None)

    # --- keys --- #
    async def get_gate_api_key_by_hash(self, key_hash):
        return next((dict(k) for k in self.keys if k["key_hash"] == key_hash), None)

    async def touch_gate_api_key(self, key_id):
        pass

    async def create_gate_api_key(self, account_id, key_prefix, key_hash, name="default"):
        k = {"id": self.kid, "account_id": account_id, "key_prefix": key_prefix, "key_hash": key_hash,
             "name": name, "is_active": True, "created_at": _now(), "last_used_at": None,
             "revoked_at": None, "grace_expires_at": None}
        self.keys.append(k); self.kid += 1
        return dict(k)

    async def list_gate_api_keys(self, account_id):
        return [dict(k) for k in self.keys if k["account_id"] == int(account_id)]

    # --- projects / runs --- #
    async def get_gate_project_by_id(self, project_id):
        return next((dict(p) for p in self.projects if p["id"] == int(project_id)), None)

    async def get_gate_project_by_domain(self, account_id, domain):
        return next((dict(p) for p in self.projects
                     if p["account_id"] == account_id and p["domain"] == domain), None)

    async def list_gate_projects(self, account_id):
        return [dict(p) for p in self.projects if p["account_id"] == int(account_id)]

    async def count_gate_projects(self, account_id):
        return len(await self.list_gate_projects(account_id))

    async def count_gate_runs_today(self, account_id):
        return sum(1 for r in self.runs if r["account_id"] == account_id)

    async def create_gate_run(self, project_id, account_id, url, score, passed, fail_on,
                              duration_ms, results, checks_run, checks_blocked, metadata=None):
        r = {"id": self.rid, "project_id": project_id, "account_id": account_id, "url": url,
             "score": score, "passed": passed, "results": results, "checks_run": checks_run,
             "checks_blocked": checks_blocked, "metadata": metadata or {},
             "created_at": "2026-08-08T00:00:00Z"}
        self.runs.append(r); self.rid += 1
        return r["id"]

    async def list_gate_runs(self, account_id=None, project_id=None, limit=20):
        rows = [r for r in self.runs if account_id is None or r["account_id"] == account_id]
        return [{k: v for k, v in r.items() if k != "results"} for r in rows[-limit:][::-1]]

    async def get_gate_run(self, run_id, account_id=None):
        return next((dict(r) for r in self.runs if r["id"] == run_id), None)

    # --- audit --- #
    async def insert_gate_audit(self, account_id, action, key_id=None, target_domain=None,
                                detail=None, ip_address=None, user_agent=None, cpf=None,
                                url_scanned=None, domain=None, score=None, passed=None):
        self.audits.append({"account_id": account_id, "action": action, "detail": detail or {},
                            "cpf": cpf, "url_scanned": url_scanned, "domain": domain,
                            "score": score, "passed": passed})

    # --- pagamentos (upgrade) --- #
    async def create_subscription_payment(self, user_id, plan, amount, charge_id, br_code,
                                          br_code_base64, expires_at=None):
        row = {"user_id": user_id, "plan": plan, "amount": amount, "charge_id": charge_id,
               "status": "pending"}
        self.payments.append(row)
        return row


def _register_key(store, full_key, account_id=10):
    store.keys.append({"id": len(store.keys) + 1, "account_id": account_id, "key_prefix": full_key[:8],
                       "key_hash": g._hash_key(full_key), "name": "d", "is_active": True,
                       "created_at": _now(), "last_used_at": None, "grace_expires_at": None})
    return full_key


def _cookie(user):
    from api import auth_users
    return {auth_users.USER_COOKIE: auth_users.create_user_token(user)}


def _report(results=None):
    results = results or [("ssl_valid", "ssl", Severity.CRITICAL, Status.PASS),
                          ("header_csp", "headers", Severity.HIGH, Status.FAIL)]
    return GateReport(url="https://acme.com.br",
                      results=[Result(c, cat, "/", st, sev, "detalhe") for (c, cat, sev, st) in results],
                      duration_ms=1200)


def _mock_engine(monkeypatch, report=None):
    rep = report or _report()

    async def _fake_run_all(url, timeout=60, checks=None, config=None, deploy_ts=None):
        return rep
    monkeypatch.setattr(g, "run_all", _fake_run_all)


@pytest.fixture
def store(monkeypatch):
    s = FakeStore()
    monkeypatch.setenv("JWT_SECRET", SECRET)
    monkeypatch.setattr(m, "get_target_store", lambda: s)
    monkeypatch.setattr(g, "get_target_store", lambda: s)
    import discovery.store as ds
    monkeypatch.setattr(ds, "get_target_store", lambda: s)
    monkeypatch.setattr(m, "_cache", None)      # sem Redis global (rate diário cai no banco)
    monkeypatch.setattr(g, "_scan_redis", lambda: None)   # limiter fail-open por default
    return s


@pytest.fixture
def client(store):
    return TestClient(m.app, raise_server_exceptions=False)


def _dev(store):
    return store.users[10]


# =========================================================================== #
# 1. CPF (validador puro + duplicidade via KYC)
# =========================================================================== #

def test_cpf_valid_formatted():
    assert validate_cpf("529.982.247-25") == "529.982.247-25"


def test_cpf_unformatted_is_normalized():
    assert validate_cpf("52998224725") == "529.982.247-25"


def test_cpf_wrong_check_digits():
    with pytest.raises(ValueError):
        validate_cpf("529.982.247-20")


def test_cpf_repeated_sequence():
    with pytest.raises(ValueError):
        validate_cpf("111.111.111-11")


def test_cpf_wrong_length():
    with pytest.raises(ValueError):
        validate_cpf("529.982.247")


# =========================================================================== #
# 2. KYC endpoint
# =========================================================================== #

def test_kyc_complete_sets_flag(client, store):
    r = client.post("/account/kyc", json={"cpf": VALID_CPF, "address": "Rua Exemplo 123, Centro",
                                          "phone": "+55 41 99999-9999"}, cookies=_cookie(_dev(store)))
    assert r.status_code == 200
    body = r.json()
    assert body["kyc_completed"] is True and body["access_level"] == "complete"
    assert store.users[10]["kyc_completed"] is True and store.users[10]["cpf"] == VALID_CPF
    assert store.users[10]["phone_verified"] is True


def test_kyc_partial_only_cpf(client, store):
    r = client.post("/account/kyc", json={"cpf": VALID_CPF}, cookies=_cookie(_dev(store)))
    assert r.status_code == 200
    assert r.json()["kyc_completed"] is False and r.json()["access_level"] == "basic"
    assert store.users[10]["cpf"] == VALID_CPF and store.users[10]["kyc_completed"] is False


def test_kyc_invalid_cpf_422(client, store):
    r = client.post("/account/kyc", json={"cpf": "111.111.111-11", "address": "Rua Exemplo 123",
                                          "phone": "999"}, cookies=_cookie(_dev(store)))
    assert r.status_code == 422


def test_kyc_duplicate_cpf_409(client, store):
    store.users[11] = {"id": 11, "cpf": VALID_CPF, "email": "x@y.com", "is_active": True,
                       "account_level": 2}
    r = client.post("/account/kyc", json={"cpf": VALID_CPF, "address": "Rua Exemplo 123",
                                          "phone": "999"}, cookies=_cookie(_dev(store)))
    assert r.status_code == 409


def test_kyc_no_email_confirmed_403(client, store):
    store.users[10]["email_confirmed"] = False
    r = client.post("/account/kyc", json={"cpf": VALID_CPF}, cookies=_cookie(_dev(store)))
    assert r.status_code == 403


def test_kyc_no_auth_401(client, store):
    assert client.post("/account/kyc", json={"cpf": VALID_CPF}).status_code == 401


# =========================================================================== #
# 3. Rate limiting (camadas testadas com fake Redis)
# =========================================================================== #

def test_user_limit_free_5_then_429():
    r = FakeRedis()
    assert all(_run(rl.check_user(r, 10, "free")) is None for _ in range(5))
    assert _run(rl.check_user(r, 10, "free")) is not None    # 6º bloqueia


def test_user_limit_pro_50_then_429():
    r = FakeRedis()
    assert all(_run(rl.check_user(r, 10, "pro")) is None for _ in range(50))
    assert _run(rl.check_user(r, 10, "pro")) is not None


def test_ip_limit_10_then_429():
    r = FakeRedis()
    assert all(_run(rl.check_ip(r, "1.2.3.4")) is None for _ in range(10))
    assert _run(rl.check_ip(r, "1.2.3.4")) is not None


def test_domain_limit_same_domain_429():
    r = FakeRedis()
    assert _run(rl.check_domain(r, 10, "acme.com.br", "free")) is None
    assert _run(rl.check_domain(r, 10, "acme.com.br", "free")) is not None   # 2º em 30min (Free) bloqueia


def test_interval_free_different_domain_429():
    r = FakeRedis()
    assert _run(rl.check_interval(r, 10, "a.com", "free")) is None
    assert _run(rl.check_interval(r, 10, "b.com", "free")) is not None   # < 5min entre A e B


def test_interval_pro_different_domain_429():
    r = FakeRedis()
    assert _run(rl.check_interval(r, 10, "a.com", "pro")) is None
    assert _run(rl.check_interval(r, 10, "b.com", "pro")) is not None    # < 1min


def test_interval_team_no_restriction():
    r = FakeRedis()
    assert _run(rl.check_interval(r, 10, "a.com", "team")) is None
    assert _run(rl.check_interval(r, 10, "b.com", "team")) is None       # intervalo 0


def test_interval_same_domain_ok():
    r = FakeRedis()
    assert _run(rl.check_interval(r, 10, "a.com", "free")) is None
    assert _run(rl.check_interval(r, 10, "a.com", "free")) is None       # mesmo domínio, sem intervalo


def test_abuse_after_21_distinct_domains():
    r = FakeRedis()
    for i in range(20):
        assert _run(rl.is_abuse(r, 10, f"d{i}.com")) is False
    assert _run(rl.is_abuse(r, 10, "d20.com")) is True                   # 21º distinto → abuso


def test_enforce_returns_payload_with_retry_after():
    r = FakeRedis()
    assert _run(rl.enforce(r, "1.2.3.4", 10, _FREE, "acme.com.br")) is None
    blocked = _run(rl.enforce(r, "1.2.3.4", 10, _FREE, "acme.com.br"))   # 2º mesmo domínio
    assert blocked and blocked["limit_type"] == "domain"
    assert blocked["retry_after_seconds"] > 0 and blocked["current_plan"] == "free"


def test_enforce_fail_open_without_redis():
    assert _run(rl.enforce(None, "1.2.3.4", 10, _FREE, "acme.com.br")) is None


# =========================================================================== #
# 4. Scan avulso + resultado filtrado por KYC + audit
# =========================================================================== #

def test_standalone_scan_ok(client, store, monkeypatch):
    _mock_engine(monkeypatch)
    key = _register_key(store, "KLM_" + "a" * 32)
    r = client.post("/gate/scan", json={"url": "https://novosite.com"}, headers={"X-API-Key": key})
    assert r.status_code == 200
    assert store.runs[-1]["project_id"] is None   # avulso: sem projeto


def test_scan_no_auth_401(client, store):
    assert client.post("/gate/scan", json={"url": "https://x.com"}).status_code == 401


def test_standalone_scan_requires_email_confirmed_403(client, store, monkeypatch):
    _mock_engine(monkeypatch)
    store.users[10]["email_confirmed"] = False
    key = _register_key(store, "KLM_" + "b" * 32)
    r = client.post("/gate/scan", json={"url": "https://x.com"}, headers={"X-API-Key": key})
    assert r.status_code == 403


def test_result_basic_without_kyc(client, store, monkeypatch):
    _mock_engine(monkeypatch)
    key = _register_key(store, "KLM_" + "c" * 32)
    body = client.post("/gate/scan", json={"url": "https://x.com"}, headers={"X-API-Key": key}).json()
    assert body["access_level"] == "basic"
    assert body["kyc_required_for_details"] is True and "kyc_message" in body
    assert "results" not in body                       # sem checks detalhados
    assert body["categories"] and "checks_total" in body["categories"][0]


def test_result_complete_with_kyc(client, store, monkeypatch):
    _mock_engine(monkeypatch)
    store.users[10]["kyc_completed"] = True
    key = _register_key(store, "KLM_" + "d" * 32)
    body = client.post("/gate/scan", json={"url": "https://x.com"}, headers={"X-API-Key": key}).json()
    assert body["access_level"] == "complete"
    assert isinstance(body["results"], list) and len(body["results"]) == 2
    assert "history" in body and "ci_snippet" in body


def test_audit_has_cpf_with_kyc(client, store, monkeypatch):
    _mock_engine(monkeypatch)
    store.users[10]["kyc_completed"] = True
    store.users[10]["cpf"] = VALID_CPF
    key = _register_key(store, "KLM_" + "e" * 32)
    client.post("/gate/scan", json={"url": "https://x.com"}, headers={"X-API-Key": key})
    scan_audit = next(a for a in store.audits if a["action"] == "scan")
    assert scan_audit["cpf"] == VALID_CPF and scan_audit["score"] is not None


def test_audit_null_cpf_without_kyc(client, store, monkeypatch):
    _mock_engine(monkeypatch)
    key = _register_key(store, "KLM_" + "f" * 32)
    client.post("/gate/scan", json={"url": "https://x.com"}, headers={"X-API-Key": key})
    scan_audit = next(a for a in store.audits if a["action"] == "scan")
    assert scan_audit["cpf"] is None


def test_scan_429_has_retry_after_header(client, store, monkeypatch):
    _mock_engine(monkeypatch)
    fake = FakeRedis()   # UM único fake compartilhado entre as chamadas → o domínio "lembra".
    monkeypatch.setattr(g, "_scan_redis", lambda: fake)
    key = _register_key(store, "KLM_" + "g" * 32)
    client.post("/gate/scan", json={"url": "https://mesmo.com"}, headers={"X-API-Key": key})
    r = client.post("/gate/scan", json={"url": "https://mesmo.com"}, headers={"X-API-Key": key})
    assert r.status_code == 429 and r.headers.get("Retry-After")
    assert r.json()["limit_type"] == "domain"


def test_suspended_account_403(client, store, monkeypatch):
    _mock_engine(monkeypatch)
    store.users[10]["suspended"] = True
    key = _register_key(store, "KLM_" + "h" * 32)
    r = client.post("/gate/scan", json={"url": "https://x.com"}, headers={"X-API-Key": key})
    assert r.status_code == 403 and r.json()["suspended"] is True


def test_scan_abuse_suspends_account(client, store, monkeypatch):
    _mock_engine(monkeypatch)
    fake = FakeRedis()
    monkeypatch.setattr(g, "_scan_redis", lambda: fake)

    async def _abuse(redis, account_id, domain):
        return True
    monkeypatch.setattr(g.gate_rl, "is_abuse", _abuse)
    key = _register_key(store, "KLM_" + "i" * 32)
    r = client.post("/gate/scan", json={"url": "https://x.com"}, headers={"X-API-Key": key})
    assert r.status_code == 403 and r.json()["suspended"] is True
    assert store.users[10]["suspended"] is True


# =========================================================================== #
# 5. Status + activate + upgrade + provision
# =========================================================================== #

def test_status_shape(client, store):
    body = client.get("/account/gate/status", cookies=_cookie(_dev(store))).json()
    assert body["is_developer"] is True and body["has_api_key"] is False
    assert body["kyc_completed"] is False and body["access_level"] == "basic"
    assert body["scans_limit_hour"] == 5 and body["suspended"] is False
    assert body["projects_count"] == 0


def test_status_no_auth_401(client, store):
    # KL-157: sem sessão → 401 (o front trata como deslogado e mostra "Criar conta").
    assert client.get("/account/gate/status").status_code == 401


def test_activate_owner_becomes_developer(client, store):
    store.users[20] = {"id": 20, "email": "owner@acme.com", "is_active": True, "account_level": 2,
                       "account_type": "owner", "gate_plan_id": None, "gate_trial_ends_at": None,
                       "email_confirmed": True, "kyc_completed": False, "suspended": False}
    r = client.post("/account/gate/activate", cookies=_cookie(store.users[20]))
    assert r.status_code == 200 and r.json()["status"] == "activated"
    assert r.json()["api_key"] and store.users[20]["account_type"] == "both"
    # KL-158: começa no Free, SEM trial Pro.
    assert store.users[20]["gate_plan_id"] == 1 and store.users[20]["gate_trial_ends_at"] is None
    assert r.json()["trial_ends_at"] is None
    assert (_run(g.get_effective_gate_plan(20)) or {}).get("slug") == "free"


def test_activate_developer_idempotent(client, store):
    r = client.post("/account/gate/activate", cookies=_cookie(_dev(store)))
    assert r.status_code == 200 and r.json()["status"] == "already_active"


def test_upgrade_generates_checkout(client, store, monkeypatch):
    store.users[10]["gate_plan_id"] = 1   # Free (sem trial) → pode subir p/ Pro
    monkeypatch.setattr(m, "_payments_enabled", lambda: True)

    async def _charge(amount, desc):
        return {"id": "ch_1", "brCode": "br", "brCodeBase64": "b64", "expiresAt": None}
    monkeypatch.setattr(g, "_create_gate_pix_charge", _charge)
    r = client.post("/account/gate/upgrade", json={"plan": "pro"}, cookies=_cookie(_dev(store)))
    assert r.status_code == 200
    assert r.json()["checkout_url"] and r.json()["plan"] == "pro"
    assert r.json()["price_display"] == "R$ 49/mês"
    assert store.payments[-1]["plan"] == "gate:pro"


def test_upgrade_no_auth_401(client, store):
    assert client.post("/account/gate/upgrade", json={"plan": "pro"}).status_code == 401


def test_upgrade_same_plan_409(client, store, monkeypatch):
    store.users[10]["gate_plan_id"] = 2   # já Pro
    monkeypatch.setattr(m, "_payments_enabled", lambda: True)
    r = client.post("/account/gate/upgrade", json={"plan": "pro"}, cookies=_cookie(_dev(store)))
    assert r.status_code == 409


def test_provision_gate_developer(store):
    store.users[30] = {"id": 30, "email": "n@acme.com", "account_type": "owner", "gate_plan_id": None,
                       "gate_trial_ends_at": None, "is_active": True, "account_level": 2}
    api_key = _run(g.provision_gate_developer(store, 30))
    assert api_key.startswith("KLM_")
    assert store.users[30]["account_type"] == "developer"
    assert any(k["account_id"] == 30 for k in store.keys)
    # KL-158: plano Free SEM trial (Pro exige pagamento) — o registro source=security-gate usa isto.
    assert store.users[30]["gate_plan_id"] == 1 and store.users[30]["gate_trial_ends_at"] is None
    eff = _run(g.get_effective_gate_plan(30)) or {}
    assert eff.get("slug") == "free" and int(eff.get("scans_per_day")) == 5   # limites Free valem


# =========================================================================== #
# KL-156 — KYC exige e-mail confirmado · upgrade com fallback (não erro silencioso)
# =========================================================================== #

def test_kl156_kyc_complete_requires_email_confirmed():
    # Mesmo com CPF+endereço+telefone, sem e-mail confirmado → kyc_completed=FALSE.
    assert g._kyc_complete(VALID_CPF, "Rua Exemplo 123 Centro", "999", False) is False
    assert g._kyc_complete(VALID_CPF, "Rua Exemplo 123 Centro", "999", True) is True
    # Falta um campo → FALSE mesmo com e-mail confirmado.
    assert g._kyc_complete(VALID_CPF, "curto", "999", True) is False       # endereço < 10
    assert g._kyc_complete(VALID_CPF, "Rua Exemplo 123 Centro", "", True) is False  # sem telefone


def test_kl156_kyc_endpoint_email_not_confirmed_403(client, store):
    # O endpoint barra ANTES (403) — a conta continua sem KYC.
    store.users[10]["email_confirmed"] = False
    r = client.post("/account/kyc", json={"cpf": VALID_CPF, "address": "Rua Exemplo 123 Centro",
                                          "phone": "999"}, cookies=_cookie(_dev(store)))
    assert r.status_code == 403
    assert store.users[10]["kyc_completed"] is False


def test_kl156_upgrade_fallback_when_payments_disabled(client, store, monkeypatch):
    store.users[10]["gate_plan_id"] = 1   # Free (sem trial) → pode subir
    monkeypatch.setattr(m, "_payments_enabled", lambda: False)   # AbacatePay não configurado
    r = client.post("/account/gate/upgrade", json={"plan": "pro"}, cookies=_cookie(_dev(store)))
    assert r.status_code == 200                       # NÃO é erro silencioso
    body = r.json()
    assert body["fallback"] is True
    assert "suporte@klarim.net" in body["contact_email"]
    assert "suporte@klarim.net" in body["message"]
