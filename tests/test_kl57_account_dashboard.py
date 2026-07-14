"""Testes do KL-57 — gestão de conta (nome/senha/exclusão), dashboard-stats do admin
e `has_profile` no resultado do scan. Offline (TestClient + FakeStore, sem rede/DB).
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

import api.main as m
from api import auth_users


# --------------------------------------------------------------------------- #
# FakeStore — só os métodos que os endpoints do KL-57 usam.
# --------------------------------------------------------------------------- #
class FakeStore:
    def __init__(self):
        self.users = {}       # email -> user (com password_hash)
        self.by_id = {}       # id -> user
        self.next_id = 1
        self.sites = {}       # (user_id, target_id) -> {...}
        self.targets = {}     # target_id -> target dict
        self.profiles = {}    # target_id -> profile dict
        self.summary = {}     # canned dashboard_summary
        self.unread = 0

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

    async def update_user_name(self, uid, name):
        u = self.by_id.get(int(uid))
        if not u:
            return False
        u["name"] = (name or "").strip() or None
        return True

    async def delete_user(self, uid):
        u = self.by_id.pop(int(uid), None)
        if not u:
            return False
        self.users.pop(u["email"], None)
        # CASCADE: remove os vínculos do usuário (targets/scans permanecem)
        for key in [k for k in self.sites if k[0] == int(uid)]:
            self.sites.pop(key, None)
        return True

    async def count_user_sites(self, uid):
        return sum(1 for (u, _t) in self.sites if u == uid)

    # --- targets / profile (para _profile_info) ---
    async def get_target_by_url(self, url):
        for t in self.targets.values():
            if t["url"] == url:
                return t
        return None

    async def get_site_profile(self, tid):
        return self.profiles.get(tid)

    # --- dashboard ---
    async def dashboard_summary(self):
        return dict(self.summary)

    async def inbox_unread_count(self):
        return self.unread


@pytest.fixture
def store(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "k" * 64)
    s = FakeStore()
    monkeypatch.setattr(m, "get_target_store", lambda: s)
    monkeypatch.setattr("discovery.store.get_target_store", lambda: s)
    monkeypatch.setattr(auth_users, "_secret", lambda: "k" * 64)
    monkeypatch.setattr(m, "_email_enabled", lambda: False)  # sem e-mail nos testes
    for bucket in (m._signup_attempts, m._reset_attempts):
        bucket.clear()
    return s


@pytest.fixture
def client(store):
    return TestClient(m.app, raise_server_exceptions=False)


def _bearer(user):
    return {"Authorization": f"Bearer {auth_users.create_user_token(user)}"}


def _signup(client, email="user@x.com.br", pw="segredo123", name=None):
    body = {"email": email, "password": pw}
    if name:
        body["name"] = name
    return client.post("/account/signup", json=body).json()["user"]


# --- 1. gestão de conta: nome --------------------------------------------- #

def test_update_name(client, store):
    u = _signup(client)
    r = client.put("/account/me", json={"name": "João Silva"}, headers=_bearer(u))
    assert r.status_code == 200
    assert r.json()["user"]["name"] == "João Silva"
    assert store.by_id[u["id"]]["name"] == "João Silva"


def test_update_name_requires_auth(client):
    assert client.put("/account/me", json={"name": "X"}).status_code == 401


def test_update_name_sanitizes_html(client, store):
    u = _signup(client)
    r = client.put("/account/me", json={"name": "<b>João</b>"}, headers=_bearer(u))
    assert r.status_code == 200
    assert "<b>" not in r.json()["user"]["name"]


# --- 2. alterar senha ------------------------------------------------------ #

def test_change_password_success(client, store):
    u = _signup(client, email="cp@x.com.br", pw="velha1234")
    r = client.post("/account/change-password",
                    json={"current_password": "velha1234", "new_password": "nova12345"},
                    headers=_bearer(u))
    assert r.status_code == 200
    # a nova senha funciona; a velha não
    assert client.post("/account/login",
                       json={"email": "cp@x.com.br", "password": "nova12345"}).status_code == 200
    assert client.post("/account/login",
                       json={"email": "cp@x.com.br", "password": "velha1234"}).status_code == 401


def test_change_password_wrong_current(client, store):
    u = _signup(client, email="cp2@x.com.br", pw="velha1234")
    r = client.post("/account/change-password",
                    json={"current_password": "errada99", "new_password": "nova12345"},
                    headers=_bearer(u))
    assert r.status_code == 401


def test_change_password_short_new(client, store):
    u = _signup(client, email="cp3@x.com.br", pw="velha1234")
    r = client.post("/account/change-password",
                    json={"current_password": "velha1234", "new_password": "curta"},
                    headers=_bearer(u))
    assert r.status_code == 400


# --- 3. excluir conta ------------------------------------------------------ #

def test_delete_account(client, store):
    u = _signup(client, email="del@x.com.br", pw="segredo123")
    r = client.request("DELETE", "/account/me", json={"password": "segredo123"}, headers=_bearer(u))
    assert r.status_code == 200 and r.json()["ok"] is True
    assert u["email"] not in store.users            # usuário removido
    # cookie limpo (set-cookie de expiração)
    assert any("klarim_session" in v for k, v in r.headers.items() if k.lower() == "set-cookie")


def test_delete_account_wrong_password(client, store):
    u = _signup(client, email="del2@x.com.br", pw="segredo123")
    r = client.request("DELETE", "/account/me", json={"password": "errada99"}, headers=_bearer(u))
    assert r.status_code == 401
    assert u["email"] in store.users                # não removeu


def test_delete_account_cascade_sites(client, store):
    u = _signup(client, email="del3@x.com.br", pw="segredo123")
    store.sites[(u["id"], 7)] = {"is_owner": False}
    client.request("DELETE", "/account/me", json={"password": "segredo123"}, headers=_bearer(u))
    assert (u["id"], 7) not in store.sites          # vínculo removido (CASCADE)


def test_delete_account_preserves_targets(client, store):
    u = _signup(client, email="del4@x.com.br", pw="segredo123")
    store.targets[9] = {"id": 9, "url": "https://keep.com.br", "domain": "keep.com.br"}
    store.sites[(u["id"], 9)] = {"is_owner": True}
    client.request("DELETE", "/account/me", json={"password": "segredo123"}, headers=_bearer(u))
    assert 9 in store.targets                        # alvo do sistema permanece


# --- 4. dashboard-stats do admin ------------------------------------------- #

def _admin_headers(monkeypatch):
    monkeypatch.setenv("ADMIN_USER", "op")
    monkeypatch.setenv("ADMIN_PASSWORD", "pw")
    return {"Authorization": f"Bearer {m._create_token('op')}"}


def test_dashboard_stats_requires_admin(client):
    assert client.get("/admin/dashboard-stats").status_code == 401


def test_dashboard_stats_returns_all_fields(client, store, monkeypatch):
    store.summary = {
        "targets": {"total": 18592, "by_status": {"scanned": 2463}, "score_100": 121},
        "scans": {"total": 4355, "avg_score": 73, "by_semaphore": {"verde": 121},
                  "manual": 1155, "automated": 3200, "today": 45, "last_7_days": 312},
        "profiles": {"total": 2480, "public": 2478, "hidden": 2, "with_ai": 1200, "with_cnae": 800},
        "accounts": {"total": 3, "active": 3, "sites_monitored": 3},
        "alerts": {"total": 515, "today": 0},
    }
    store.unread = 2
    r = client.get("/admin/dashboard-stats", headers=_admin_headers(monkeypatch))
    assert r.status_code == 200
    body = r.json()
    # todos os blocos presentes + inbox mesclado pelo endpoint
    for key in ("targets", "scans", "profiles", "accounts", "alerts", "inbox"):
        assert key in body
    assert body["inbox"]["unread"] == 2
    # manual vs automatizado separados corretamente
    assert body["scans"]["manual"] == 1155 and body["scans"]["automated"] == 3200
    assert body["targets"]["score_100"] == 121


# --- 5. has_profile no resultado do scan (_profile_info) ------------------- #

def test_profile_info_true_when_profile_visible(store):
    url = m._norm_scan_url("https://tem.com.br")
    store.targets[1] = {"id": 1, "url": url, "domain": "tem.com.br", "status": "scanned"}
    store.profiles[1] = {"public_visible": True}
    info = asyncio.run(m._profile_info(url))
    assert info["has_profile"] is True
    assert info["profile_domain"] == "tem.com.br"


def test_profile_info_false_without_profile(store):
    url = m._norm_scan_url("https://sem.com.br")
    store.targets[2] = {"id": 2, "url": url, "domain": "sem.com.br", "status": "scanned"}
    info = asyncio.run(m._profile_info(url))
    assert info["has_profile"] is False           # perfil ainda sendo gerado (KL-51 f5)


def test_profile_info_false_when_hidden(store):
    url = m._norm_scan_url("https://oculto.com.br")
    store.targets[3] = {"id": 3, "url": url, "domain": "oculto.com.br", "status": "scanned"}
    store.profiles[3] = {"public_visible": False}   # landing desligada pelo operador
    info = asyncio.run(m._profile_info(url))
    assert info["has_profile"] is False


def test_profile_info_false_when_discarded(store):
    url = m._norm_scan_url("https://desc.com.br")
    store.targets[4] = {"id": 4, "url": url, "domain": "desc.com.br", "status": "descartado"}
    store.profiles[4] = {"public_visible": True}
    info = asyncio.run(m._profile_info(url))
    assert info["has_profile"] is False
