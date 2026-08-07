"""KL-151 Prompt 1/4 — Security Gate como produto: conta dev, API key, planos, projetos,
verificação de domínio, convite dono→dev. Offline (TestClient + FakeStore).

O SQL das store methods foi validado à parte contra Postgres 16 real; aqui exercitamos os
endpoints + as regras (enforcement de plano, API key hash, plano efetivo trial>plano, convite)."""
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
    """Loop isolado (não usa o loop global, que o TestClient de outros testes pode ter fechado)."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# --------------------------------------------------------------------------- #
# FakeStore (só o que os endpoints do Gate tocam)
# --------------------------------------------------------------------------- #

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
        self.users = {}       # email -> user
        self.by_id = {}
        self.uid = 1
        self.keys = []        # gate_api_keys
        self.kid = 1
        self.projects = []    # gate_projects
        self.pid = 1
        self.invites = []     # gate_invites
        self.iid = 1
        self.runs_today = {}  # account_id -> count (test-controlled)
        self.owned_domains = set()   # (user_id, domain) verified-owner links
        self.sent_gate_invites = []

    # --- users --- #
    async def create_user(self, email, password_hash, name=None, role="owner",
                          email_confirmed=True, confirmation_source=None, source="signup"):
        email = email.lower().strip()
        if email in self.users:
            return None
        u = {"id": self.uid, "email": email, "name": name, "plan": "free", "max_sites": 5,
             "is_active": True, "role": role, "email_confirmed": email_confirmed,
             "password_hash": password_hash, "account_level": 2 if password_hash else 1,
             "account_type": "owner", "full_name": None, "company_name_dev": None, "phone": None,
             "gate_plan_id": None, "gate_trial_started_at": None, "gate_trial_ends_at": None,
             "source": source}
        self.users[email] = u
        self.by_id[u["id"]] = u
        self.uid += 1
        return {k: v for k, v in u.items() if k != "password_hash"}

    async def get_user_by_email(self, email, with_hash=False):
        return self.users.get((email or "").lower().strip())

    async def get_user_by_id(self, uid):
        return self.by_id.get(int(uid))

    async def set_account_type(self, account_id, account_type):
        u = self.by_id.get(account_id)
        if u:
            u["account_type"] = account_type
        return bool(u)

    async def set_account_dev_profile(self, account_id, full_name=None, company_name_dev=None, phone=None):
        u = self.by_id.get(account_id)
        if u:
            u["full_name"] = full_name or u["full_name"]
            u["company_name_dev"] = company_name_dev or u["company_name_dev"]
            u["phone"] = phone or u["phone"]
        return bool(u)

    async def set_account_gate_plan(self, account_id, plan_id, trial_started_at=None, trial_ends_at=None):
        u = self.by_id.get(account_id)
        if u:
            u["gate_plan_id"] = plan_id
            u["gate_trial_started_at"] = trial_started_at
            u["gate_trial_ends_at"] = trial_ends_at
        return bool(u)

    async def get_account_gate_fields(self, account_id):
        u = self.by_id.get(int(account_id))
        if not u:
            return None
        return {k: u.get(k) for k in ("id", "email", "account_type", "gate_plan_id",
                                      "gate_trial_started_at", "gate_trial_ends_at")}

    # --- plans --- #
    async def get_gate_plan_by_slug(self, slug):
        return next((dict(p) for p in _PLANS if p["slug"] == slug), None)

    async def get_gate_plan(self, plan_id):
        return next((dict(p) for p in _PLANS if p["id"] == plan_id), None) if plan_id else None

    async def list_gate_plans(self, active_only=True):
        return [dict(p) for p in _PLANS]

    # --- api keys --- #
    async def create_gate_api_key(self, account_id, key_prefix, key_hash, name="default"):
        rec = {"id": self.kid, "account_id": account_id, "key_prefix": key_prefix,
               "key_hash": key_hash, "name": name, "is_active": True,
               "created_at": datetime.now(timezone.utc), "last_used_at": None, "revoked_at": None}
        self.keys.append(rec)
        self.kid += 1
        return {k: rec[k] for k in ("id", "account_id", "key_prefix", "name", "is_active", "created_at")}

    async def get_gate_api_key_by_hash(self, key_hash):
        return next((dict(k) for k in self.keys if k["key_hash"] == key_hash), None)

    async def touch_gate_api_key(self, key_id):
        for k in self.keys:
            if k["id"] == key_id:
                k["last_used_at"] = datetime.now(timezone.utc)

    async def revoke_gate_api_keys(self, account_id):
        n = 0
        for k in self.keys:
            if k["account_id"] == account_id and k["is_active"]:
                k["is_active"] = False
                k["revoked_at"] = datetime.now(timezone.utc)
                n += 1
        return n

    async def revoke_gate_api_keys_with_grace(self, account_id, grace_minutes=60):
        out = []
        for k in self.keys:
            if k["account_id"] == account_id and k["is_active"]:
                k["is_active"] = False
                k["revoked_at"] = datetime.now(timezone.utc)
                k["grace_expires_at"] = datetime.now(timezone.utc) + timedelta(minutes=grace_minutes)
                out.append(k["key_prefix"])
        return out

    async def insert_gate_audit(self, **kw):
        return None

    async def list_gate_api_keys(self, account_id):
        return [dict(k) for k in self.keys if k["account_id"] == account_id]

    # --- projects --- #
    async def create_gate_project(self, account_id, name, url, domain, verified=False,
                                  verification_method=None, invited_by=None):
        domain = (domain or "").lower().strip()
        if any(p["account_id"] == account_id and p["domain"] == domain for p in self.projects):
            return None
        p = {"id": self.pid, "account_id": account_id, "name": name, "url": url, "domain": domain,
             "verified": verified, "verified_at": datetime.now(timezone.utc) if verified else None,
             "verification_method": verification_method, "config": {}, "invited_by": invited_by,
             "created_at": datetime.now(timezone.utc)}
        self.projects.append(p)
        self.pid += 1
        return dict(p)

    async def get_gate_project(self, project_id, account_id):
        return next((dict(p) for p in self.projects
                     if p["id"] == int(project_id) and p["account_id"] == int(account_id)), None)

    async def get_gate_project_by_domain(self, account_id, domain):
        domain = (domain or "").lower().strip()
        return next((dict(p) for p in self.projects
                     if p["account_id"] == int(account_id) and p["domain"] == domain), None)

    async def list_gate_projects(self, account_id):
        return [dict(p) for p in self.projects if p["account_id"] == account_id]

    async def count_gate_projects(self, account_id):
        return sum(1 for p in self.projects if p["account_id"] == account_id)

    async def start_gate_project_verification(self, project_id, account_id, method, token):
        for p in self.projects:
            if p["id"] == int(project_id) and p["account_id"] == int(account_id):
                p["config"] = {**p["config"], "verify_method": method, "verify_token": token,
                               "verify_expires_at": (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()}
                return True
        return False

    async def get_gate_verification_challenge(self, project_id, account_id):
        p = await self.get_gate_project(project_id, account_id)
        if not p:
            return None
        cfg = p["config"]
        if cfg.get("verify_token") and datetime.fromisoformat(cfg["verify_expires_at"]) > datetime.now(timezone.utc):
            return {"method": cfg["verify_method"], "token": cfg["verify_token"]}
        return None

    async def mark_gate_project_verified(self, project_id, method, invited_by=None):
        for p in self.projects:
            if p["id"] == int(project_id):
                p["verified"] = True
                p["verified_at"] = datetime.now(timezone.utc)
                p["verification_method"] = method
                if invited_by is not None:
                    p["invited_by"] = invited_by
                p["config"] = {k: v for k, v in p["config"].items()
                               if k not in ("verify_method", "verify_token", "verify_expires_at")}
                return True
        return False

    async def delete_gate_project_by_domain(self, account_id, domain):
        domain = (domain or "").lower().strip()
        before = len(self.projects)
        self.projects = [p for p in self.projects
                         if not (p["account_id"] == int(account_id) and p["domain"] == domain)]
        return before - len(self.projects)

    # --- runs --- #
    async def count_gate_runs_today(self, account_id):
        return self.runs_today.get(account_id, 0)

    # --- invites --- #
    async def create_gate_invite(self, domain, owner_account_id, dev_email, token, expires_days=7):
        inv = {"id": self.iid, "domain": (domain or "").lower().strip(),
               "owner_account_id": owner_account_id, "dev_email": (dev_email or "").lower().strip(),
               "token": token, "status": "pending", "accepted_at": None,
               "expires_at": datetime.now(timezone.utc) + timedelta(days=expires_days),
               "created_at": datetime.now(timezone.utc)}
        self.invites.append(inv)
        self.iid += 1
        return dict(inv)

    async def get_gate_invite_by_token(self, token):
        return next((dict(i) for i in self.invites if i["token"] == token), None)

    async def get_gate_invite(self, invite_id, owner_account_id):
        return next((dict(i) for i in self.invites
                     if i["id"] == int(invite_id) and i["owner_account_id"] == int(owner_account_id)), None)

    async def mark_gate_invite_accepted(self, invite_id):
        for i in self.invites:
            if i["id"] == int(invite_id) and i["status"] == "pending":
                i["status"] = "accepted"
                i["accepted_at"] = datetime.now(timezone.utc)
                return True
        return False

    async def revoke_gate_invite(self, invite_id, owner_account_id):
        for i in self.invites:
            if (i["id"] == int(invite_id) and i["owner_account_id"] == int(owner_account_id)
                    and i["status"] in ("pending", "accepted")):
                i["status"] = "revoked"
                return {"domain": i["domain"], "dev_email": i["dev_email"]}
        return None

    async def list_gate_invites(self, owner_account_id):
        return [dict(i) for i in self.invites if i["owner_account_id"] == owner_account_id]

    async def user_owns_verified_domain(self, user_id, domain):
        return (int(user_id), (domain or "").lower().strip()) in self.owned_domains


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #

@pytest.fixture
def store(monkeypatch):
    s = FakeStore()
    monkeypatch.setenv("JWT_SECRET", SECRET)
    monkeypatch.setattr(m, "get_target_store", lambda: s)
    monkeypatch.setattr(g, "get_target_store", lambda: s)
    import discovery.store as ds
    monkeypatch.setattr(ds, "get_target_store", lambda: s)

    async def _allow(*a, **k):
        return True, 0
    monkeypatch.setattr(m, "_redis_allow", _allow)          # nunca limita nos testes
    monkeypatch.setattr(m, "_spawn", lambda coro: coro.close())  # não dispara e-mail
    return s


@pytest.fixture
def client(store):
    return TestClient(m.app, raise_server_exceptions=False)


def _auth_cookie(user):
    return {auth_users.USER_COOKIE: auth_users.create_user_token(user)}


def _dev(store, email="dev@acme.com", level=2):
    u = {"id": store.uid, "email": email, "is_active": True, "account_level": level,
         "account_type": "developer", "name": "Dev", "role": "owner"}
    store.by_id[u["id"]] = u
    store.users[email] = {**u, "password_hash": "h"}
    store.uid += 1
    return u


# =========================================================================== #
# Helpers puros
# =========================================================================== #

def test_generate_api_key_shape():
    full, prefix, kh = g.generate_api_key()
    assert full.startswith("KLM_") and len(full) == 36
    assert prefix == full[:8] and len(kh) == 64   # sha256 hex
    assert g._hash_key(full) == kh


def test_extract_domain():
    assert g._extract_domain("https://www.acme.com.br/path") == "acme.com.br"
    assert g._extract_domain("acme.com.br") == "acme.com.br"
    assert g._extract_domain("") == ""


def test_get_allowed_checks_free_vs_all():
    assert g.get_allowed_checks({"checks_allowed": ["headers", "ssl", "exposure", "https_redirect"]}) \
        == ["headers", "ssl", "exposure", "https_redirect"]
    assert g.get_allowed_checks({"checks_allowed": ["all"]}) == g.ALL_CHECK_NAMES
    assert len(g.get_allowed_checks({"checks_allowed": ["all"]})) == 18


# =========================================================================== #
# Registro dev + API key
# =========================================================================== #

def test_register_creates_dev_key_project(client, store):
    r = client.post("/gate/register", json={"email": "novo@dev.com", "password": "senhaforte",
                                            "full_name": "Novo Dev", "company": "Acme",
                                            "project_url": "https://acme.com.br"})
    assert r.status_code == 200
    body = r.json()
    assert body["api_key"].startswith("KLM_") and len(body["api_key"]) == 36
    assert body["account_id"] and body["project_id"]
    # conta é developer, plano Free + trial Pro
    u = store.by_id[body["account_id"]]
    assert u["account_type"] == "developer" and u["gate_plan_id"] == 1
    assert u["gate_trial_ends_at"] > datetime.now(timezone.utc)
    # a key vive só como HASH (nunca raw)
    assert all(k["key_hash"] and k["key_hash"] != body["api_key"] for k in store.keys)
    assert g._hash_key(body["api_key"]) == store.keys[0]["key_hash"]
    # domínio extraído da URL
    assert store.projects[0]["domain"] == "acme.com.br"


def test_register_duplicate_email_409(client, store):
    _dev(store, "dup@dev.com")
    r = client.post("/gate/register", json={"email": "dup@dev.com", "password": "senhaforte",
                                            "project_url": "https://x.com"})
    assert r.status_code == 409


def test_register_short_password_400(client, store):
    r = client.post("/gate/register", json={"email": "a@b.com", "password": "123",
                                            "project_url": "https://x.com"})
    assert r.status_code == 400


# =========================================================================== #
# Autenticação por API key
# =========================================================================== #

def _make_key(store, account_id):
    full, prefix, kh = g.generate_api_key()
    _run(
        store.create_gate_api_key(account_id, prefix, kh))
    return full


def test_api_key_auth_valid(client, store):
    dev = _dev(store)
    dev["gate_plan_id"] = 1
    key = _make_key(store, dev["id"])
    r = client.get("/gate/projects", headers={"X-API-Key": key})
    assert r.status_code == 200
    assert r.json()["plan"]["slug"] == "free"


def test_api_key_auth_missing_401(client, store):
    assert client.get("/gate/projects").status_code == 401


def test_api_key_auth_bad_prefix_401(client, store):
    assert client.get("/gate/projects", headers={"X-API-Key": "abc123"}).status_code == 401


def test_api_key_auth_revoked_401(client, store):
    dev = _dev(store)
    key = _make_key(store, dev["id"])
    _run(store.revoke_gate_api_keys(dev["id"]))
    assert client.get("/gate/projects", headers={"X-API-Key": key}).status_code == 401


def test_api_key_last_used_updated(client, store):
    dev = _dev(store)
    dev["gate_plan_id"] = 1
    key = _make_key(store, dev["id"])
    assert store.keys[0]["last_used_at"] is None
    client.get("/gate/projects", headers={"X-API-Key": key})
    assert store.keys[0]["last_used_at"] is not None


def test_regenerate_key_revokes_old_with_grace(client, store):
    # KL-151 P4: a regeneração dá grace period de 1h — a key antiga AINDA autentica (CI não quebra).
    dev = _dev(store)
    dev["gate_plan_id"] = 1
    old = _make_key(store, dev["id"])
    r = client.post("/account/gate/regenerate-key", cookies=_auth_cookie(dev))
    assert r.status_code == 200 and r.json()["grace_period_minutes"] == 60
    new = r.json()["api_key"]
    assert new != old
    # old marcada revogada (is_active False) mas com grace futuro; nova ativa.
    assert store.keys[0]["is_active"] is False and store.keys[1]["is_active"] is True
    assert store.keys[0]["grace_expires_at"] > datetime.now(timezone.utc)
    # dentro do grace, a old ainda autentica; a nova também.
    assert client.get("/gate/projects", headers={"X-API-Key": old}).status_code == 200
    assert client.get("/gate/projects", headers={"X-API-Key": new}).status_code == 200


# =========================================================================== #
# Plano efetivo + enforcement
# =========================================================================== #

def test_effective_plan_trial_active_is_pro(store):
    import asyncio
    dev = _dev(store)
    dev["gate_plan_id"] = 1
    dev["gate_trial_ends_at"] = datetime.now(timezone.utc) + timedelta(days=5)
    plan = _run(g.get_effective_gate_plan(dev["id"]))
    assert plan["slug"] == "pro"


def test_effective_plan_trial_expired_is_free(store):
    import asyncio
    dev = _dev(store)
    dev["gate_plan_id"] = 1
    dev["gate_trial_ends_at"] = datetime.now(timezone.utc) - timedelta(days=1)
    plan = _run(g.get_effective_gate_plan(dev["id"]))
    assert plan["slug"] == "free"


def test_enforce_scan_limit_free_6th_429(store):
    import asyncio
    dev = _dev(store)
    store.runs_today[dev["id"]] = 5   # Free = 5/dia; o 6º estoura
    free = _PLANS[0]
    with pytest.raises(Exception) as ei:
        _run(g.enforce_scan_limit(dev["id"], free))
    assert ei.value.status_code == 429


def test_enforce_domain_limit_free_2nd_403(store):
    import asyncio
    dev = _dev(store)
    _run(
        store.create_gate_project(dev["id"], "x", "https://x.com", "x.com"))
    with pytest.raises(Exception) as ei:
        _run(g.enforce_domain_limit(dev["id"], _PLANS[0]))
    assert ei.value.status_code == 403


def test_enforce_enterprise_unlimited(store):
    import asyncio
    dev = _dev(store)
    store.runs_today[dev["id"]] = 9999
    # scans_per_day = -1 → nunca levanta
    _run(g.enforce_scan_limit(dev["id"], _PLANS[3]))


def test_checks_enforcement_free_4_pro_9():
    assert len(g.get_allowed_checks(_PLANS[0])) == 4   # Free
    assert len(g.get_allowed_checks(_PLANS[1])) == 9   # Pro


# =========================================================================== #
# Projetos + verificação de domínio
# =========================================================================== #

def test_create_project_extracts_domain(client, store):
    dev = _dev(store)
    dev["gate_plan_id"] = 2   # Pro (10 domínios)
    key = _make_key(store, dev["id"])
    r = client.post("/gate/projects", json={"url": "https://www.loja.com.br/x"},
                    headers={"X-API-Key": key})
    assert r.status_code == 200
    assert r.json()["project"]["domain"] == "loja.com.br"


def test_project_limit_enforced_on_create(client, store):
    dev = _dev(store)
    dev["gate_plan_id"] = 1   # Free (1 domínio)
    key = _make_key(store, dev["id"])
    client.post("/gate/projects", json={"url": "https://a.com"}, headers={"X-API-Key": key})
    r2 = client.post("/gate/projects", json={"url": "https://b.com"}, headers={"X-API-Key": key})
    assert r2.status_code == 403   # 2º domínio no Free


def test_verify_start_and_check(client, store, monkeypatch):
    dev = _dev(store)
    key = _make_key(store, dev["id"])
    proj = _run(
        store.create_gate_project(dev["id"], "Acme", "https://acme.com.br", "acme.com.br"))
    r = client.post(f"/gate/projects/{proj['id']}/verify/start", json={"method": "dns_txt"},
                    headers={"X-API-Key": key})
    assert r.status_code == 200 and r.json()["method"] == "dns_txt"
    challenge = r.json()["challenge"]
    # a checagem: monkeypatch o controle de domínio p/ True
    async def _ok(method, token, domain):
        return token == challenge and domain == "acme.com.br"
    monkeypatch.setattr(m, "_check_domain_control", _ok)
    r2 = client.post(f"/gate/projects/{proj['id']}/verify/check", headers={"X-API-Key": key})
    assert r2.status_code == 200 and r2.json()["status"] == "verified"
    assert store.projects[0]["verified"] is True


def test_verify_check_no_pending(client, store):
    dev = _dev(store)
    key = _make_key(store, dev["id"])
    proj = _run(
        store.create_gate_project(dev["id"], "Acme", "https://acme.com.br", "acme.com.br"))
    r = client.post(f"/gate/projects/{proj['id']}/verify/check", headers={"X-API-Key": key})
    assert r.json()["status"] == "no_pending"


def test_verify_other_account_project_404(client, store):
    dev = _dev(store, "dev1@x.com")
    other = _dev(store, "dev2@x.com")
    key = _make_key(store, dev["id"])
    proj = _run(
        store.create_gate_project(other["id"], "X", "https://x.com", "x.com"))
    r = client.post(f"/gate/projects/{proj['id']}/verify/start", json={"method": "dns_txt"},
                    headers={"X-API-Key": key})
    assert r.status_code == 404


# =========================================================================== #
# Convite dono→dev
# =========================================================================== #

def test_owner_invite_requires_domain_ownership(client, store):
    owner = _dev(store, "owner@x.com", level=3)
    r = client.post("/account/gate/invite", json={"domain": "naomeu.com", "dev_email": "d@x.com"},
                    cookies=_auth_cookie(owner))
    assert r.status_code == 403   # não é dono verificado do domínio


def test_owner_invite_sends(client, store):
    owner = _dev(store, "owner@x.com", level=3)
    store.owned_domains.add((owner["id"], "meusite.com"))
    r = client.post("/account/gate/invite", json={"domain": "meusite.com", "dev_email": "d@x.com"},
                    cookies=_auth_cookie(owner))
    assert r.status_code == 200 and r.json()["status"] == "sent"
    assert store.invites[0]["domain"] == "meusite.com" and store.invites[0]["token"]


def test_invite_level_below_3_forbidden(client, store):
    owner = _dev(store, "o2@x.com", level=2)   # nível 2 não pode convidar
    r = client.post("/account/gate/invite", json={"domain": "x.com", "dev_email": "d@x.com"},
                    cookies=_auth_cookie(owner))
    assert r.status_code == 403


def test_dev_accepts_invite_creates_verified_project(client, store):
    owner = _dev(store, "owner@x.com", level=3)
    dev = _dev(store, "d@x.com")
    _run(
        store.create_gate_invite("meusite.com", owner["id"], "d@x.com", "TOK1"))
    r = client.post("/gate/invite/TOK1/accept", cookies=_auth_cookie(dev))
    assert r.status_code == 200 and r.json()["status"] == "accepted"
    proj = store.projects[0]
    assert proj["verified"] is True and proj["verification_method"] == "invite"
    assert proj["invited_by"] == owner["id"] and proj["account_id"] == dev["id"]
    assert store.invites[0]["status"] == "accepted"


def test_accept_invite_wrong_email_403(client, store):
    owner = _dev(store, "owner@x.com", level=3)
    other = _dev(store, "outro@x.com")
    _run(
        store.create_gate_invite("meusite.com", owner["id"], "convidado@x.com", "TOK2"))
    r = client.post("/gate/invite/TOK2/accept", cookies=_auth_cookie(other))
    assert r.status_code == 403


def test_accept_expired_invite_400(client, store):
    owner = _dev(store, "owner@x.com", level=3)
    dev = _dev(store, "d@x.com")
    inv = _run(
        store.create_gate_invite("meusite.com", owner["id"], "d@x.com", "TOK3"))
    store.invites[0]["expires_at"] = datetime.now(timezone.utc) - timedelta(days=1)  # expirado
    r = client.post("/gate/invite/TOK3/accept", cookies=_auth_cookie(dev))
    assert r.status_code == 400


def test_owner_revoke_removes_dev_project(client, store):
    owner = _dev(store, "owner@x.com", level=3)
    dev = _dev(store, "d@x.com")
    inv = _run(
        store.create_gate_invite("meusite.com", owner["id"], "d@x.com", "TOK4"))
    client.post("/gate/invite/TOK4/accept", cookies=_auth_cookie(dev))
    assert len(store.projects) == 1
    r = client.delete(f"/account/gate/invite/{inv['id']}", cookies=_auth_cookie(owner))
    assert r.status_code == 200 and r.json()["status"] == "revoked"
    assert len(store.projects) == 0   # projeto do dev removido → perde acesso


def test_invite_info_public(client, store):
    owner = _dev(store, "owner@x.com", level=3)
    _dev(store, "d@x.com")
    _run(
        store.create_gate_invite("meusite.com", owner["id"], "d@x.com", "TOK5"))
    r = client.get("/gate/invite/TOK5")
    assert r.status_code == 200
    assert r.json()["domain"] == "meusite.com" and r.json()["dev_has_account"] is True
