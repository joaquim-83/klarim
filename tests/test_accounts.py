"""Testes das contas de usuário (KL-51 f3) — offline (TestClient + FakeStore).

Rotas autenticadas usam o token via `Authorization: Bearer` (o cookie é Secure e o
TestClient roda em http://testserver, então o cookie não voltaria). O `require_user`
aceita ambos, então o Bearer exercita o mesmo caminho.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import jwt
import pytest
from fastapi.testclient import TestClient

import api.main as m
from api import auth_users


# --------------------------------------------------------------------------- #
# FakeStore — implementa só os métodos que os endpoints de conta usam.
# --------------------------------------------------------------------------- #
class FakeStore:
    def __init__(self):
        self.users = {}      # email -> user (com password_hash)
        self.by_id = {}      # id -> user
        self.next_id = 1
        self.sites = {}      # (user_id, target_id) -> {"is_owner": bool}
        self.resets = {}     # email -> code
        self.targets = {}    # target_id -> target dict
        self.scanned_by = {} # email -> [target_ids] (histórico KL-25)
        self.scan_history = []       # linhas de /account/scan-history
        self.users_with_sites = []   # linhas de /admin/clients
        self.verified_scan_emails = None  # None ⇒ todo e-mail é "verificado no scan" (KL-25)
        self.ownership = []          # KL-68: verificações de propriedade (Tier 2)
        self.next_verif_id = 1

    # --- users ---
    async def email_has_verified_scan(self, email):
        # KL-44 F-03b: por padrão todo e-mail conta como já verificado no scan (fluxo
        # scan→cadastro), então o signup cria direto. Para testar o caminho com código,
        # os testes setam `verified_scan_emails` a um conjunto explícito.
        if self.verified_scan_emails is None:
            return True
        return email.lower().strip() in self.verified_scan_emails

    async def create_user(self, email, password_hash, name=None):
        email = email.lower().strip()
        if email in self.users:
            return None
        u = {"id": self.next_id, "email": email, "name": name, "plan": "free",
             "max_sites": 1, "is_active": True, "password_hash": password_hash}
        self.users[email] = u
        self.by_id[u["id"]] = u
        self.next_id += 1
        return {k: v for k, v in u.items() if k != "password_hash"}

    async def get_user_by_email(self, email, with_hash=False):
        u = self.users.get(email.lower().strip())
        if not u:
            return None
        return dict(u) if with_hash else {k: v for k, v in u.items() if k != "password_hash"}

    async def get_user_by_id(self, uid):
        u = self.by_id.get(int(uid))
        return {k: v for k, v in u.items() if k != "password_hash"} if u else None

    async def touch_user_login(self, uid):
        pass

    # KL-61: hooks de lead (fire-and-forget no signup/add_site)
    async def set_lead_account(self, email, account_id):
        pass

    async def set_lead_monitoring(self, email):
        pass

    async def set_user_password(self, email, ph):
        u = self.users.get(email.lower().strip())
        if not u:
            return False
        u["password_hash"] = ph
        return True

    # --- password reset ---
    async def create_password_reset(self, email, code, ttl):
        self.resets[email.lower().strip()] = code

    async def verify_password_reset(self, email, code):
        e = email.lower().strip()
        if self.resets.get(e) == code:
            del self.resets[e]
            return True
        return False

    # --- sites ---
    async def count_user_sites(self, uid):
        return sum(1 for (u, t) in self.sites if u == uid)

    async def list_user_sites(self, uid):
        return [{"target_id": t, "is_owner": v["is_owner"], "url": "https://x.com.br",
                 "domain": "x.com.br", "sector": "outro", "last_scan_score": 80,
                 "last_scan_at": None, "platform": "", "last_semaphore": "amarelo"}
                for (u, t), v in self.sites.items() if u == uid]

    async def get_user_site(self, uid, tid):
        v = self.sites.get((uid, tid))
        return {"id": 1, "user_id": uid, "target_id": tid, "is_owner": v["is_owner"]} if v else None

    async def link_user_site(self, uid, tid, is_owner=False):
        if (uid, tid) in self.sites:
            return False
        self.sites[(uid, tid)] = {"is_owner": is_owner}
        return True

    async def unlink_user_site(self, uid, tid):
        return self.sites.pop((uid, tid), None) is not None

    async def set_user_site_owner(self, uid, tid, is_owner=True):
        if (uid, tid) in self.sites:
            self.sites[(uid, tid)]["is_owner"] = is_owner
            return True
        return False

    # --- ownership (KL-68) ---
    async def site_has_owner(self, tid, exclude_user_id=None):
        return any(v.get("is_owner") for (u, t), v in self.sites.items()
                   if t == tid and (exclude_user_id is None or u != exclude_user_id))

    async def mark_site_verified(self, uid, tid, method):
        if (uid, tid) in self.sites:
            self.sites[(uid, tid)]["is_owner"] = True
            self.sites[(uid, tid)]["verification_method"] = method
            return True
        return False

    async def create_ownership_verification(self, uid, tid, method, code):
        for v in self.ownership:
            if v["user_id"] == uid and v["target_id"] == tid and v["status"] == "pending":
                v["status"] = "expired"
        v = {"id": self.next_verif_id, "user_id": uid, "target_id": tid, "method": method,
             "code": code, "attempts": 0, "status": "pending", "expired": False}
        self.next_verif_id += 1
        self.ownership.append(v)
        return {"id": v["id"], "expires_at": None}

    async def get_pending_ownership_verification(self, uid, tid):
        for v in reversed(self.ownership):
            if (v["user_id"] == uid and v["target_id"] == tid and v["status"] == "pending"
                    and not v.get("expired") and v["attempts"] < 3):
                return {"id": v["id"], "code": v["code"], "attempts": v["attempts"],
                        "status": v["status"], "expires_at": None}
        return None

    async def bump_ownership_attempt(self, vid):
        for v in self.ownership:
            if v["id"] == vid:
                v["attempts"] += 1
                if v["attempts"] >= 3:
                    v["status"] = "failed"
                return v["attempts"]
        return 3

    async def mark_ownership_verified(self, vid):
        for v in self.ownership:
            if v["id"] == vid:
                v["status"] = "verified"

    async def get_target_owner(self, tid):
        for (u, t), v in self.sites.items():
            if t == tid and v.get("is_owner"):
                usr = self.by_id.get(u)
                return {"user_id": u, "email": usr["email"] if usr else None,
                        "verified_at": None, "verification_method": v.get("verification_method")}
        return None

    async def revoke_ownership(self, tid):
        n = 0
        for (u, t), v in self.sites.items():
            if t == tid and v.get("is_owner"):
                v["is_owner"] = False
                v["verification_method"] = None
                n += 1
        return n

    async def ownership_stats(self):
        owners = sum(1 for v in self.sites.values() if v.get("is_owner"))
        return {"verified_owners": owners, "by_method": {}, "verifications": {},
                "total_monitored": len(self.sites), "owner_rate": 0.0}

    async def list_user_sites_min(self):
        out = []
        for (u, t) in self.sites:
            tgt = self.targets.get(t) or {}
            out.append({"id": t, "user_id": u, "target_id": t,
                        "domain": tgt.get("domain") or tgt.get("url")})
        return out

    async def remove_user_sites_by_ids(self, ids):
        ids = set(ids)
        removed = 0
        for key in list(self.sites):
            if key[1] in ids:   # no fake, o `id` do vínculo == target_id
                del self.sites[key]
                removed += 1
        return removed

    # --- targets ---
    async def get_target_by_url(self, url):
        for t in self.targets.values():
            if t["url"] == url:
                return t
        return None

    async def get_target(self, tid):
        return self.targets.get(tid)

    async def register_target(self, url, domain=None, **kw):
        tid = self.next_id
        self.next_id += 1
        self.targets[tid] = {"id": tid, "url": url, "domain": domain, "contact_email": None, **kw}
        return tid

    async def get_targets_scanned_by_email(self, email, limit=10):
        return list(self.scanned_by.get(email.lower().strip(), []))[:limit]

    async def get_scan_history_for_email(self, email, limit=20):
        return self.scan_history[:limit]

    async def list_users_with_sites(self):
        return self.users_with_sites

    # KL-44: stubs de plano/assinatura NÃO-persistentes — o hook de trial no signup e o
    # enforcement de sites caem no fallback (users.max_sites), mantendo estes testes
    # determinísticos. A lógica de trial/limite por plano é testada em test_subscriptions.py.
    async def get_subscription_row(self, account_id):
        return None

    async def get_plan(self, plan_id):
        ms = {"free": 1, "pro": 5, "agency": 15}.get(plan_id, 1)
        return {"id": plan_id, "name": plan_id.capitalize(), "max_sites": ms}

    async def upsert_subscription(self, account_id, plan_id, status, trial_ends_at=None,
                                  expires_at=None, billing_cycle="monthly"):
        return {"account_id": account_id, "plan_id": plan_id, "status": status,
                "trial_ends_at": trial_ends_at}

    async def update_subscription(self, account_id, **fields):
        return {"account_id": account_id, **fields}

    async def log_subscription_change(self, *a, **k):
        return None


@pytest.fixture
def store(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "k" * 64)
    s = FakeStore()
    monkeypatch.setattr(m, "get_target_store", lambda: s)
    # require_user faz `from discovery.store import get_target_store` (lazy) — patch lá também
    monkeypatch.setattr("discovery.store.get_target_store", lambda: s)
    monkeypatch.setattr(auth_users, "_secret", lambda: "k" * 64)
    # zera os rate limits in-memory entre testes
    for bucket in (m._signup_attempts, m._forgot_attempts, m._reset_attempts,
                   m._send_report_attempts, m._ownership_attempts):
        bucket.clear()
    return s


@pytest.fixture
def client(store):
    return TestClient(m.app, raise_server_exceptions=False)


def _bearer(user):
    return {"Authorization": f"Bearer {auth_users.create_user_token(user)}"}


# --- senha + token (puro) --------------------------------------------------- #

def test_password_hash_roundtrip():
    h = auth_users.hash_password("segredo123")
    assert h != "segredo123"
    assert auth_users.verify_password("segredo123", h) is True
    assert auth_users.verify_password("errada", h) is False
    assert auth_users.verify_password("x", "não-é-hash") is False


def test_token_typ_separation(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "k" * 64)
    monkeypatch.setattr(auth_users, "_secret", lambda: "k" * 64)
    user_tok = auth_users.create_user_token({"id": 7, "email": "a@b.com.br", "plan": "free"})
    assert auth_users.verify_user_token(user_tok)["user_id"] == 7
    # token de usuário NÃO passa como admin
    with pytest.raises(Exception):
        m._verify_token(user_tok)
    # token de admin NÃO passa como usuário
    monkeypatch.setenv("ADMIN_USER", "op")
    admin_tok = m._create_token("op")
    with pytest.raises(Exception):
        auth_users.verify_user_token(admin_tok)


# --- signup ----------------------------------------------------------------- #

def test_signup_success_sets_cookie(client, store):
    r = client.post("/account/signup", json={"email": "joao@empresa.com.br", "password": "segredo123"})
    assert r.status_code == 200
    assert r.json()["user"]["email"] == "joao@empresa.com.br"
    assert "klarim_session" in r.cookies or "set-cookie" in {k.lower() for k in r.headers}


def test_signup_duplicate(client):
    client.post("/account/signup", json={"email": "dup@x.com.br", "password": "segredo123"})
    r = client.post("/account/signup", json={"email": "dup@x.com.br", "password": "outra1234"})
    assert r.status_code == 409


def test_signup_short_password(client):
    r = client.post("/account/signup", json={"email": "a@x.com.br", "password": "curta"})
    assert r.status_code == 400


def test_signup_bad_email(client):
    r = client.post("/account/signup", json={"email": "not-an-email", "password": "segredo123"})
    assert r.status_code == 400


def test_signup_links_scanned_site(client, store):
    url = m._norm_scan_url("https://meusite.com.br")
    store.targets[99] = {"id": 99, "url": url, "domain": "meusite.com.br",
                         "contact_email": "joao@meusite.com.br"}
    r = client.post("/account/signup", json={
        "email": "joao@meusite.com.br", "password": "segredo123", "url": "https://meusite.com.br"})
    assert r.status_code == 200
    uid = r.json()["user"]["id"]
    assert (uid, 99) in store.sites
    assert store.sites[(uid, 99)]["is_owner"] is True   # e-mail bate → dono


# --- signup com verificação de e-mail (KL-44 F-03b) ------------------------- #

class _FakeMailer:
    def __init__(self):
        self.sent = []

    async def send_signup_verification_code(self, to_email, code):
        self.sent.append((to_email, code))
        return {"ok": True}

    async def send_ownership_verification(self, to_email, domain, code):
        self.sent.append((to_email, code))
        return {"ok": True}


@pytest.fixture
def mailer(monkeypatch):
    fm = _FakeMailer()
    monkeypatch.setattr(m, "_email_enabled", lambda: True)
    monkeypatch.setattr(m, "_mailer", lambda: fm)
    return fm


def test_signup_unverified_requires_code(client, store, mailer):
    store.verified_scan_emails = set()   # nenhum e-mail verificado no scan (KL-25)
    r = client.post("/account/signup", json={"email": "novo@x.com.br", "password": "segredo123"})
    assert r.status_code == 200 and r.json()["status"] == "verification_sent"
    assert "novo@x.com.br" not in store.users          # conta NÃO criada ainda
    assert mailer.sent and mailer.sent[0][0] == "novo@x.com.br"   # código enviado


def test_signup_verify_creates_account(client, store, mailer):
    store.verified_scan_emails = set()
    client.post("/account/signup", json={"email": "novo@x.com.br", "password": "segredo123"})
    code = mailer.sent[0][1]
    r = client.post("/account/verify", json={"email": "novo@x.com.br", "code": code})
    assert r.status_code == 200 and r.json()["user"]["email"] == "novo@x.com.br"
    assert "novo@x.com.br" in store.users
    assert "klarim_session" in r.cookies or "set-cookie" in {k.lower() for k in r.headers}


def test_signup_verify_wrong_code(client, store, mailer):
    store.verified_scan_emails = set()
    client.post("/account/signup", json={"email": "novo@x.com.br", "password": "segredo123"})
    r = client.post("/account/verify", json={"email": "novo@x.com.br", "code": "000000"})
    assert r.status_code == 400
    assert "novo@x.com.br" not in store.users          # código errado → não cria


def test_signup_verify_no_pending(client, store, mailer):
    r = client.post("/account/verify", json={"email": "ninguem@x.com.br", "code": "123456"})
    assert r.status_code == 400


def test_signup_verified_scan_skips_code(client, store, mailer):
    # e-mail já verificado no scan → cria direto, mesmo com o mailer disponível.
    store.verified_scan_emails = {"javerificado@x.com.br"}
    r = client.post("/account/signup", json={"email": "javerificado@x.com.br", "password": "segredo123"})
    assert r.status_code == 200 and "user" in r.json()
    assert mailer.sent == []                            # nenhum código enviado


def test_signup_unverified_no_email_service(client, store, monkeypatch):
    # sem serviço de e-mail configurado, o caminho não-verificado degrada com 503
    # (não cria conta silenciosamente).
    store.verified_scan_emails = set()
    monkeypatch.setattr(m, "_email_enabled", lambda: False)
    r = client.post("/account/signup", json={"email": "novo@x.com.br", "password": "segredo123"})
    assert r.status_code == 503 and "novo@x.com.br" not in store.users


# --- login ------------------------------------------------------------------ #

def test_login_success_and_wrong(client, store):
    client.post("/account/signup", json={"email": "l@x.com.br", "password": "segredo123"})
    assert client.post("/account/login", json={"email": "l@x.com.br", "password": "segredo123"}).status_code == 200
    assert client.post("/account/login", json={"email": "l@x.com.br", "password": "errada99"}).status_code == 401
    assert client.post("/account/login", json={"email": "nope@x.com.br", "password": "segredo123"}).status_code == 401


# --- me --------------------------------------------------------------------- #

def test_me_requires_auth(client):
    assert client.get("/account/me").status_code == 401


def test_me_with_token(client, store):
    u = client.post("/account/signup", json={"email": "me@x.com.br", "password": "segredo123"}).json()["user"]
    r = client.get("/account/me", headers=_bearer(u))
    assert r.status_code == 200 and r.json()["user"]["email"] == "me@x.com.br"


def test_expired_user_token_401(client, store):
    payload = {"typ": "user", "user_id": 1, "exp": datetime.now(timezone.utc) - timedelta(hours=1)}
    tok = jwt.encode(payload, "k" * 64, algorithm="HS256")
    assert client.get("/account/me", headers={"Authorization": f"Bearer {tok}"}).status_code == 401


# --- forgot / reset --------------------------------------------------------- #

def test_forgot_is_generic(client, store):
    # e-mail inexistente ainda responde ok (anti-enumeração)
    r = client.post("/account/forgot", json={"email": "ghost@x.com.br"})
    assert r.status_code == 200 and r.json()["ok"] is True


def test_reset_flow(client, store):
    client.post("/account/signup", json={"email": "r@x.com.br", "password": "velha1234"})
    store.resets["r@x.com.br"] = "123456"   # simula o código enviado
    r = client.post("/account/reset", json={"email": "r@x.com.br", "code": "123456", "new_password": "nova12345"})
    assert r.status_code == 200
    # a senha nova funciona; a velha não
    assert client.post("/account/login", json={"email": "r@x.com.br", "password": "nova12345"}).status_code == 200
    assert client.post("/account/login", json={"email": "r@x.com.br", "password": "velha1234"}).status_code == 401


def test_reset_bad_code(client, store):
    client.post("/account/signup", json={"email": "r2@x.com.br", "password": "velha1234"})
    r = client.post("/account/reset", json={"email": "r2@x.com.br", "code": "000000", "new_password": "nova12345"})
    assert r.status_code == 400


# --- sites ------------------------------------------------------------------ #

def test_add_site_and_limit(client, store):
    u = client.post("/account/signup", json={"email": "s@x.com.br", "password": "segredo123"}).json()["user"]
    url = m._norm_scan_url("https://site1.com.br")
    store.targets[10] = {"id": 10, "url": url, "domain": "site1.com.br", "contact_email": None}
    r1 = client.post("/account/sites", json={"url": "https://site1.com.br"}, headers=_bearer(u))
    assert r1.status_code == 200 and r1.json()["target_id"] == 10
    # 2º site com plano free (max_sites=1) → 403 upgrade
    store.targets[11] = {"id": 11, "url": m._norm_scan_url("https://site2.com.br"),
                         "domain": "site2.com.br", "contact_email": None}
    r2 = client.post("/account/sites", json={"url": "https://site2.com.br"}, headers=_bearer(u))
    assert r2.status_code == 403


def test_list_and_remove_site(client, store):
    u = client.post("/account/signup", json={"email": "s2@x.com.br", "password": "segredo123"}).json()["user"]
    store.sites[(u["id"], 20)] = {"is_owner": False}
    lst = client.get("/account/sites", headers=_bearer(u))
    assert lst.status_code == 200 and len(lst.json()["sites"]) == 1
    rm = client.delete("/account/sites/20", headers=_bearer(u))
    assert rm.status_code == 200
    assert (u["id"], 20) not in store.sites
    assert client.delete("/account/sites/999", headers=_bearer(u)).status_code == 404


# --- fix UX (KL-51 f3): histórico no signup, mask, send-report ------------- #

# --- histórico de consultas + gestão de clientes (KL-51 f3 fix) ------------ #

def test_scan_history_requires_auth(client):
    assert client.get("/account/scan-history").status_code == 401


def test_scan_history_returns_scans(client, store):
    u = client.post("/account/signup", json={"email": "hst@x.com.br", "password": "segredo123"}).json()["user"]
    store.scan_history = [
        {"id": 5, "url": "https://a.com.br", "score": 82, "semaphore": "amarelo",
         "scanned_at": datetime(2026, 7, 12, tzinfo=timezone.utc)},
        {"id": 6, "url": "https://b.com.br", "score": 95, "semaphore": None,  # fallback por score
         "scanned_at": None},
    ]
    r = client.get("/account/scan-history", headers=_bearer(u))
    assert r.status_code == 200
    scans = r.json()["scans"]
    assert len(scans) == 2
    assert scans[0]["semaphore"] == "amarelo" and scans[0]["scanned_at"].startswith("2026-07-12")
    assert scans[1]["semaphore"] == "verde"   # score 95 → fallback verde


def test_admin_clients_requires_admin(client):
    # sem token → o middleware admin devolve 401
    assert client.get("/admin/clients").status_code == 401


def test_admin_clients_lists_accounts(client, store, monkeypatch):
    monkeypatch.setenv("ADMIN_USER", "op")
    monkeypatch.setenv("ADMIN_PASSWORD", "pw")
    store.users_with_sites = [
        {"id": 1, "email": "a@x.com.br", "plan": "free", "max_sites": 1, "is_active": True,
         "sites": [{"target_id": 9, "url": "https://a.com.br", "domain": "a.com.br",
                    "last_scan_score": 80, "last_semaphore": "amarelo", "is_owner": True}]},
        {"id": 2, "email": "b@x.com.br", "plan": "free", "max_sites": 1, "is_active": False, "sites": []},
    ]
    admin_tok = m._create_token("op")
    r = client.get("/admin/clients", headers={"Authorization": f"Bearer {admin_tok}"})
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 2 and body["active"] == 1 and body["total_sites"] == 1
    assert body["clients"][0]["sites"][0]["domain"] == "a.com.br"


def test_optional_user_never_raises(store, monkeypatch):
    # auth OPCIONAL: sem token → None; e qualquer erro do store → None (nunca levanta).
    from starlette.requests import Request as SRequest

    def _req(headers=None, cookies_hdr=""):
        hdrs = [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
        if cookies_hdr:
            hdrs.append((b"cookie", cookies_hdr.encode()))
        return SRequest({"type": "http", "headers": hdrs, "method": "GET", "path": "/scan/summary"})

    # sem token → None
    assert asyncio.run(auth_users.optional_user(_req())) is None
    # token válido mas store explode → None (não levanta)
    async def _boom(uid):
        raise RuntimeError("db down")
    monkeypatch.setattr(store, "get_user_by_id", _boom)
    tok = auth_users.create_user_token({"id": 1, "email": "a@b.com.br", "plan": "free"})
    assert asyncio.run(auth_users.optional_user(_req({"authorization": f"Bearer {tok}"}))) is None


def test_mask_email():
    assert m._mask_email("joao@empresa.com.br") == "j***o@empresa.com.br"
    assert m._mask_email("ab@x.com").endswith("@x.com")
    assert "***" in m._mask_email("joao@empresa.com.br")


def test_signup_links_previous_scans(client, store):
    # e-mail já escaneou o target 55 antes de criar conta → vincula ao dashboard
    store.targets[55] = {"id": 55, "url": "https://old.com.br", "domain": "old.com.br",
                         "contact_email": None}
    store.scanned_by["hist@x.com.br"] = [55]
    u = client.post("/account/signup", json={"email": "hist@x.com.br", "password": "segredo123"}).json()["user"]
    assert (u["id"], 55) in store.sites


def test_signup_history_respects_plan_limit(client, store):
    # free = 1 site: url do signup ocupa a vaga, o histórico não excede o limite
    store.targets[60] = {"id": 60, "url": m._norm_scan_url("https://novo.com.br"),
                         "domain": "novo.com.br", "contact_email": None}
    store.targets[61] = {"id": 61, "url": "https://antigo.com.br", "domain": "antigo.com.br",
                         "contact_email": None}
    store.scanned_by["cap@x.com.br"] = [61]
    u = client.post("/account/signup", json={
        "email": "cap@x.com.br", "password": "segredo123", "url": "https://novo.com.br"}).json()["user"]
    assert sum(1 for (uid, _) in store.sites if uid == u["id"]) == 1  # só 1 (limite free)


def test_send_report_masked(client, store, monkeypatch):
    monkeypatch.setattr(m, "_email_enabled", lambda: True)
    monkeypatch.setattr(m, "_spawn", lambda coro: coro.close())  # não roda o background
    r = client.post("/scan/send-report", json={"url": "https://x.com.br", "email": "joao@empresa.com.br"})
    assert r.status_code == 200
    assert r.json()["email"] == "j***o@empresa.com.br"


def test_send_report_bad_email(client, store, monkeypatch):
    monkeypatch.setattr(m, "_email_enabled", lambda: True)
    assert client.post("/scan/send-report", json={"url": "https://x.com.br", "email": "nope"}).status_code == 422


def test_send_report_uses_session_email(client, store, monkeypatch):
    monkeypatch.setattr(m, "_email_enabled", lambda: True)
    monkeypatch.setattr(m, "_spawn", lambda coro: coro.close())
    u = client.post("/account/signup", json={"email": "sess@x.com.br", "password": "segredo123"}).json()["user"]
    r = client.post("/scan/send-report", json={"url": "https://x.com.br"}, headers=_bearer(u))
    assert r.status_code == 200 and r.json()["email"].endswith("@x.com.br")


def test_send_report_rate_limit(client, store, monkeypatch):
    monkeypatch.setattr(m, "_email_enabled", lambda: True)
    monkeypatch.setattr(m, "_spawn", lambda coro: coro.close())
    for _ in range(3):
        assert client.post("/scan/send-report", json={"url": "https://x.com.br", "email": "rl@x.com.br"}).status_code == 200
    assert client.post("/scan/send-report", json={"url": "https://x.com.br", "email": "rl@x.com.br"}).status_code == 429


def test_claim_requires_email_match(client, store):
    u = client.post("/account/signup", json={"email": "owner@x.com.br", "password": "segredo123"}).json()["user"]
    store.targets[30] = {"id": 30, "url": "https://x.com.br", "domain": "x.com.br",
                         "contact_email": "outro@x.com.br"}
    store.sites[(u["id"], 30)] = {"is_owner": False}
    # e-mail não bate → 403
    assert client.post("/account/sites/30/claim", headers=_bearer(u)).status_code == 403
    # e-mail bate → 200 + is_owner
    store.targets[30]["contact_email"] = "owner@x.com.br"
    r = client.post("/account/sites/30/claim", headers=_bearer(u))
    assert r.status_code == 200 and r.json()["is_owner"] is True


# --- KL-68: reivindicação + verificação de propriedade ---------------------- #

from api import domain_guard  # noqa: E402


def test_domain_guard_classifies():
    assert domain_guard.is_blocked_domain("gmail.com") == (True, "public_domain")
    assert domain_guard.is_blocked_domain("mail.google.com")[0] is True
    assert domain_guard.is_blocked_domain("prefeitura.sp.gov.br")[0] is True
    assert domain_guard.is_blocked_domain("usecognato.com.br") == (False, None)
    assert domain_guard.is_blocked_domain("www.Python.org")[0] is True


def test_signup_claim_auto_verifies_when_email_matches(client, store):
    store.targets[10] = {"id": 10, "url": "https://minhaloja.com.br",
                         "domain": "minhaloja.com.br", "contact_email": "dono@minhaloja.com.br"}
    r = client.post("/account/signup", json={"email": "dono@minhaloja.com.br",
                                             "password": "segredo123", "url": "https://minhaloja.com.br"})
    assert r.status_code == 200
    claim = r.json().get("claim") or {}
    assert claim.get("site_added") is True and claim.get("is_owner") is True
    uid = r.json()["user"]["id"]
    assert store.sites[(uid, 10)]["is_owner"] is True
    assert store.sites[(uid, 10)]["verification_method"] == "auto_email"


def test_signup_claim_no_autoverify_when_email_differs(client, store):
    store.targets[11] = {"id": 11, "url": "https://loja.com.br", "domain": "loja.com.br",
                         "contact_email": "dono@loja.com.br"}
    r = client.post("/account/signup", json={"email": "visitante@x.com.br",
                                             "password": "segredo123", "url": "https://loja.com.br"})
    claim = r.json().get("claim") or {}
    assert claim.get("site_added") is True and claim.get("is_owner") is False
    assert claim.get("ownership_verification_available") is True


def test_signup_blocked_domain_not_added(client, store):
    r = client.post("/account/signup", json={"email": "a@x.com.br",
                                             "password": "segredo123", "url": "https://gmail.com"})
    assert r.status_code == 200
    claim = r.json().get("claim") or {}
    assert claim.get("site_added") is False and claim.get("blocked_domain") is True
    assert "a@x.com.br" in store.users     # conta criada mesmo assim (comportamento suave)
    assert not store.sites                  # nenhum site vinculado


def test_add_site_blocked_domain_422(client, store):
    u = client.post("/account/signup", json={"email": "u@x.com.br", "password": "segredo123"}).json()["user"]
    r = client.post("/account/sites", json={"url": "https://python.org"}, headers=_bearer(u))
    assert r.status_code == 422


def _seed_owned_site(store, uid, tid, contact="contato@alvo.com.br", owner=False):
    store.targets[tid] = {"id": tid, "url": "https://alvo.com.br", "domain": "alvo.com.br",
                          "contact_email": contact}
    store.sites[(uid, tid)] = {"is_owner": owner}


def test_ownership_request_and_verify(client, store, mailer):
    u = client.post("/account/signup", json={"email": "u@x.com.br", "password": "segredo123"}).json()["user"]
    _seed_owned_site(store, u["id"], 20)
    r = client.post("/account/ownership/request-verification", json={"target_id": 20}, headers=_bearer(u))
    assert r.status_code == 200 and r.json()["sent"] is True
    hint = r.json()["email_hint"]
    assert "@" in hint and "contato@alvo.com.br" not in hint       # e-mail mascarado
    code = mailer.sent[-1][1]
    r2 = client.post("/account/ownership/verify", json={"target_id": 20, "code": code}, headers=_bearer(u))
    assert r2.status_code == 200 and r2.json()["verified"] is True
    assert store.sites[(u["id"], 20)]["is_owner"] is True


def test_ownership_no_contact_email(client, store, mailer):
    u = client.post("/account/signup", json={"email": "u@x.com.br", "password": "segredo123"}).json()["user"]
    _seed_owned_site(store, u["id"], 24, contact=None)
    r = client.post("/account/ownership/request-verification", json={"target_id": 24}, headers=_bearer(u))
    assert r.status_code == 400   # sem contato público não há como verificar


def test_ownership_wrong_code_locks(client, store, mailer):
    u = client.post("/account/signup", json={"email": "u@x.com.br", "password": "segredo123"}).json()["user"]
    _seed_owned_site(store, u["id"], 21)
    client.post("/account/ownership/request-verification", json={"target_id": 21}, headers=_bearer(u))
    real = mailer.sent[-1][1]
    wrong = "111111" if real != "111111" else "222222"
    for _ in range(3):
        r = client.post("/account/ownership/verify", json={"target_id": 21, "code": wrong}, headers=_bearer(u))
        assert r.json()["verified"] is False
    # após 3 erros, o código pendente vira 'failed' → sem pendente válido
    r = client.post("/account/ownership/verify", json={"target_id": 21, "code": real}, headers=_bearer(u))
    assert r.json().get("error") == "expired"
    assert store.sites[(u["id"], 21)]["is_owner"] is False


def test_ownership_second_user_blocked(client, store, mailer):
    owner = client.post("/account/signup", json={"email": "owner@x.com.br", "password": "segredo123"}).json()["user"]
    intruder = client.post("/account/signup", json={"email": "intruso@x.com.br", "password": "segredo123"}).json()["user"]
    store.targets[22] = {"id": 22, "url": "https://alvo.com.br", "domain": "alvo.com.br",
                         "contact_email": "contato@alvo.com.br"}
    store.sites[(owner["id"], 22)] = {"is_owner": True}
    store.sites[(intruder["id"], 22)] = {"is_owner": False}
    r = client.post("/account/ownership/request-verification", json={"target_id": 22}, headers=_bearer(intruder))
    assert r.status_code == 409   # first-come-first-served


def test_ownership_status(client, store):
    u = client.post("/account/signup", json={"email": "u@x.com.br", "password": "segredo123"}).json()["user"]
    _seed_owned_site(store, u["id"], 23)
    j = client.get("/account/ownership/status?target_id=23", headers=_bearer(u)).json()
    assert j["monitored"] is True and j["is_owner"] is False and j["verification_available"] is True
