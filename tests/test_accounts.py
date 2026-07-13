"""Testes das contas de usuário (KL-51 f3) — offline (TestClient + FakeStore).

Rotas autenticadas usam o token via `Authorization: Bearer` (o cookie é Secure e o
TestClient roda em http://testserver, então o cookie não voltaria). O `require_user`
aceita ambos, então o Bearer exercita o mesmo caminho.
"""

from __future__ import annotations

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

    # --- users ---
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
                   m._send_report_attempts):
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
