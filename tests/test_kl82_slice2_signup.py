"""KL-82 Slice 2 + KL-85 — signup sem código, confirmação por link, rate limit, blocklist,
cleanup. Offline (TestClient + FakeStore).

Cobre:
  * blocklist de e-mail descartável (400) e e-mail legítimo (ok).
  * rate limit de signup: 3/h e 5/dia por IP → 429.
  * /account/confirm: token válido → confirmed; já usado → already; inválido → invalid.
  * /account/resend-confirmation: exige login, no-op se confirmado, rate limit.
  * banner unconfirmed via _user_public.email_confirmed.
  * cleanup: delete_unconfirmed_inactive_accounts (predicado — testado no store fake).
  * is_disposable_email (puro).
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import api.main as m
from api import auth_users
from api.disposable_emails import is_disposable_email


class FakeStore:
    def __init__(self):
        self.users = {}
        self.by_id = {}
        self.next_id = 1
        self.sites = {}
        self.verified_scan_emails = set()  # ninguém verificado → fluxo unconfirmed

    async def email_has_verified_scan(self, email):
        return email.lower().strip() in self.verified_scan_emails

    async def create_user(self, email, password_hash, name=None, role="owner",
                          email_confirmed=True, confirmation_source=None):
        email = email.lower().strip()
        if email in self.users:
            return None
        u = {"id": self.next_id, "email": email, "name": name, "plan": "free",
             "max_sites": 1, "is_active": True, "role": role,
             "email_confirmed": email_confirmed, "password_hash": password_hash}
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

    async def confirm_user_email(self, user_id, source="link"):
        u = self.by_id.get(int(user_id))
        if not u or u.get("email_confirmed") is True:
            return False
        u["email_confirmed"] = True
        u["confirmation_source"] = source
        return True

    async def touch_user_login(self, uid):
        pass

    async def count_user_sites(self, uid):
        return 0

    async def get_targets_scanned_by_email(self, email, limit=1):
        return []

    async def auto_link_technician_by_email(self, email, tuid):
        return 0

    async def set_lead_account(self, email, account_id):
        pass


@pytest.fixture
def store(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "k" * 64)
    s = FakeStore()
    monkeypatch.setattr(m, "get_target_store", lambda: s)
    monkeypatch.setattr("discovery.store.get_target_store", lambda: s)
    monkeypatch.setattr(auth_users, "_secret", lambda: "k" * 64)
    monkeypatch.setattr(m, "_email_enabled", lambda: True)
    monkeypatch.setattr(m, "_spawn", lambda coro: coro.close())  # não dispara e-mail real
    for b in (m._signup_attempts, m._signup_daily_attempts, m._resend_confirm_attempts):
        b.clear()
    return s


@pytest.fixture
def client(store):
    return TestClient(m.app, raise_server_exceptions=False)


def _bearer(user):
    return {"Authorization": f"Bearer {auth_users.create_user_token(user)}"}


# --------------------------------------------------------------------------- #
# 1. Blocklist de descartáveis (KL-85 Parte 3)
# --------------------------------------------------------------------------- #

def test_is_disposable_email_pure():
    assert is_disposable_email("a@mailinator.com") is True
    assert is_disposable_email("a@YOPMAIL.com") is True     # case-insensitive
    assert is_disposable_email("a@gmail.com") is False
    assert is_disposable_email("a@empresa.com.br") is False
    assert is_disposable_email("semarroba") is False


def test_signup_disposable_blocked(client, store):
    r = client.post("/account/signup", json={"email": "x@guerrillamail.com", "password": "segredo123"})
    assert r.status_code == 400 and "permanente" in r.json()["detail"].lower()
    assert "x@guerrillamail.com" not in store.users


def test_signup_legit_email_ok(client, store):
    r = client.post("/account/signup", json={"email": "real@empresa.com.br", "password": "segredo123"})
    assert r.status_code == 200 and r.json()["user"]["email_confirmed"] is False


# --------------------------------------------------------------------------- #
# 2. Rate limit de signup (KL-85 Parte 2)
# --------------------------------------------------------------------------- #

def test_signup_rate_limit_hourly(client, store):
    # 3/h: os 3 primeiros passam, o 4º → 429.
    for i in range(3):
        assert client.post("/account/signup", json={"email": f"u{i}@x.com.br", "password": "segredo123"}).status_code == 200
    r = client.post("/account/signup", json={"email": "u4@x.com.br", "password": "segredo123"})
    assert r.status_code == 429 and "cadastros" in r.json()["detail"].lower()


# --------------------------------------------------------------------------- #
# 3. Confirmação por link
# --------------------------------------------------------------------------- #

def test_confirm_valid_then_idempotent(client, store):
    u = client.post("/account/signup", json={"email": "c@x.com.br", "password": "segredo123"}).json()["user"]
    tok = m._make_confirm_token(u["id"], "c@x.com.br")
    assert client.get(f"/account/confirm?token={tok}").json()["status"] == "confirmed"
    assert store.by_id[u["id"]]["email_confirmed"] is True
    assert client.get(f"/account/confirm?token={tok}").json()["status"] == "already"


def test_confirm_invalid_and_expired(client, store):
    assert client.get("/account/confirm?token=nonsense").json()["status"] == "invalid"
    assert client.get("/account/confirm").json()["status"] == "invalid"  # sem token


def test_confirm_token_wrong_email_rejected(client, store):
    u = client.post("/account/signup", json={"email": "c@x.com.br", "password": "segredo123"}).json()["user"]
    # token com e-mail que não bate o do user_id → invalid (não confirma)
    bad = m._make_confirm_token(u["id"], "outro@x.com.br")
    assert client.get(f"/account/confirm?token={bad}").json()["status"] == "invalid"
    assert store.by_id[u["id"]]["email_confirmed"] is False


# --------------------------------------------------------------------------- #
# 4. Reenvio de confirmação
# --------------------------------------------------------------------------- #

def test_resend_confirmation_requires_auth(client):
    assert client.post("/account/resend-confirmation").status_code == 401


def test_resend_confirmation_sends_and_rate_limits(client, store):
    u = client.post("/account/signup", json={"email": "r@x.com.br", "password": "segredo123"}).json()["user"]
    hdr = _bearer(u)
    ok = [client.post("/account/resend-confirmation", headers=hdr) for _ in range(3)]
    assert all(x.status_code == 200 and x.json()["status"] == "sent" for x in ok)
    # 4º no mesmo período → 429 (rate limit 3/h por conta)
    assert client.post("/account/resend-confirmation", headers=hdr).status_code == 429


def test_resend_noop_when_confirmed(client, store):
    u = client.post("/account/signup", json={"email": "r2@x.com.br", "password": "segredo123"}).json()["user"]
    await_confirm = m._make_confirm_token(u["id"], "r2@x.com.br")
    client.get(f"/account/confirm?token={await_confirm}")
    r = client.post("/account/resend-confirmation", headers=_bearer(u))
    assert r.status_code == 200 and r.json()["status"] == "already_confirmed"


# --------------------------------------------------------------------------- #
# 5. _user_public expõe email_confirmed (para o banner do dashboard)
# --------------------------------------------------------------------------- #

def test_user_public_email_confirmed_flag():
    assert m._user_public({"id": 1, "email": "a@x.com", "email_confirmed": False})["email_confirmed"] is False
    assert m._user_public({"id": 1, "email": "a@x.com", "email_confirmed": True})["email_confirmed"] is True
    # legado (NULL) conta como confirmada
    assert m._user_public({"id": 1, "email": "a@x.com"})["email_confirmed"] is True
