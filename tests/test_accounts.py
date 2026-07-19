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
        self.tech_links = []         # KL-44 P3
        self.shared = {}             # code -> report
        self.bulletins = []
        self.scans = {}              # target_id -> scan dict
        self.next_link_id = 1

    # --- users ---
    async def confirm_user_email(self, user_id, source="link"):   # KL-82 Slice 2
        u = self.by_id.get(int(user_id))
        if not u or u.get("email_confirmed") is True:
            return False
        u["email_confirmed"] = True
        return True

    async def email_has_verified_scan(self, email):
        # KL-44 F-03b: por padrão todo e-mail conta como já verificado no scan (fluxo
        # scan→cadastro), então o signup cria direto. Para testar o caminho com código,
        # os testes setam `verified_scan_emails` a um conjunto explícito.
        if self.verified_scan_emails is None:
            return True
        return email.lower().strip() in self.verified_scan_emails

    async def create_user(self, email, password_hash, name=None, role="owner",
                          email_confirmed=True):
        email = email.lower().strip()
        if email in self.users:
            return None
        u = {"id": self.next_id, "email": email, "name": name, "plan": "free",
             "max_sites": 1, "is_active": True, "password_hash": password_hash,
             "email_confirmed": email_confirmed,
             "role": role if role in ("owner", "technician", "both") else "owner"}
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

    # --- gestão de usuários (KL-69) ---
    async def set_user_active(self, user_id, active):
        u = self.by_id.get(int(user_id))
        if not u:
            return False
        u["is_active"] = active
        return True

    async def mark_ownership_revoked(self, user_id, target_id):
        for v in self.ownership:
            if v["user_id"] == user_id and v["target_id"] == target_id and v["status"] in ("pending", "verified"):
                v["status"] = "revoked"

    # KL-71 Bug 8: desativa vigílias de um site do usuário (self-service removal)
    async def disable_user_site_vigilias(self, user_id, domain):
        self.disabled_site_vigilias = getattr(self, "disabled_site_vigilias", [])
        self.disabled_site_vigilias.append((user_id, domain))
        return 0

    # --- KL-44 P3: técnico + laudo + boletim ---
    async def create_technician_link(self, owner_user_id, target_id, technician_email, invite_code):
        email = technician_email.lower().strip()
        for l in self.tech_links:
            if l["owner_user_id"] == owner_user_id and l["target_id"] == target_id and l["technician_email"] == email:
                l.update(status="pending", invite_code=invite_code)
                return dict(l)
        link = {"id": self.next_link_id, "owner_user_id": owner_user_id, "target_id": target_id,
                "technician_email": email, "technician_user_id": None, "status": "pending",
                "invite_code": invite_code, "linked_at": None, "last_access_at": None}
        self.next_link_id += 1
        self.tech_links.append(link)
        return dict(link)

    async def get_technician_links(self, owner_user_id, target_id=None):
        return [dict(l) for l in self.tech_links
                if l["owner_user_id"] == owner_user_id and l["status"] != "revoked"
                and (target_id is None or l["target_id"] == target_id)]

    async def get_technician_link(self, link_id):
        for l in self.tech_links:
            if l["id"] == link_id:
                return dict(l)
        return None

    async def revoke_technician_link(self, link_id, owner_user_id):
        for l in self.tech_links:
            if l["id"] == link_id and l["owner_user_id"] == owner_user_id and l["status"] != "revoked":
                l["status"] = "revoked"
                return True
        return False

    async def accept_technician_invite(self, invite_code, technician_user_id):
        for l in self.tech_links:
            if l["invite_code"] == invite_code and l["status"] == "pending":
                l.update(status="active", technician_user_id=technician_user_id)
                return dict(l)
        return None

    async def auto_link_technician_by_email(self, email, technician_user_id):
        e = email.lower().strip()
        n = 0
        for l in self.tech_links:
            if l["technician_email"] == e and l["status"] == "pending":
                l.update(status="active", technician_user_id=technician_user_id)
                n += 1
        return n

    async def get_technician_clients(self, technician_user_id):
        out = []
        for l in self.tech_links:
            if l["technician_user_id"] == technician_user_id and l["status"] == "active":
                owner = self.by_id.get(l["owner_user_id"], {})
                out.append({"link_id": l["id"], "target_id": l["target_id"], "status": "active",
                            "owner_email": owner.get("email"), "domain": "alvo.com.br",
                            "last_scan_score": 70, "last_semaphore": "amarelo", "last_bulletin_at": None})
        return out

    async def get_active_technician_for_target(self, owner_user_id, target_id):
        for l in self.tech_links:
            if l["owner_user_id"] == owner_user_id and l["target_id"] == target_id and l["status"] == "active":
                return dict(l)
        return None

    async def search_technician_by_email(self, email):
        u = self.users.get(email.lower().strip())
        if u and u.get("role") in ("technician", "both") and u.get("is_active", True):
            return {"id": u["id"], "name": u.get("name"), "role": u["role"]}
        return None

    async def get_latest_scan_id(self, target_id):
        return self.scans.get(target_id)

    async def get_latest_scan_full(self, target_id):
        return self.scans.get(target_id)

    async def create_shared_report(self, target_id, owner_user_id, code, scan_id=None, technician_link_id=None):
        from datetime import datetime, timedelta, timezone
        row = {"id": len(self.shared) + 1, "code": code, "target_id": target_id,
               "owner_user_id": owner_user_id, "scan_id": scan_id,
               "technician_link_id": technician_link_id, "access_count": 0, "expired": False,
               "expires_at": datetime.now(timezone.utc) + timedelta(days=30),
               "domain": "alvo.com.br", "score": 73, "semaphore": "amarelo",
               "checks_json": [], "scanned_at": None}
        self.shared[code] = row
        return {"code": code, "expires_at": row["expires_at"], "created_at": None}

    async def get_shared_report_by_code(self, code):
        return self.shared.get(code)

    async def register_shared_report_access(self, code):
        if code in self.shared:
            self.shared[code]["access_count"] += 1

    async def create_bulletin(self, **kw):
        self.bulletins.append(kw)

    async def get_last_bulletin(self, user_id, target_id):
        rows = [b for b in self.bulletins if b.get("user_id") == user_id and b.get("target_id") == target_id]
        return rows[-1] if rows else None

    async def list_users_due_bulletin(self, frequency):
        return []

    async def bulletin_stats(self):
        return {"total": len(self.bulletins), "today": 0, "week": 0, "tech_notified": 0, "by_type": {}}

    async def list_technician_links_admin(self, limit=100):
        return [dict(l) for l in self.tech_links][:limit]

    async def get_user_target_vigilias(self, user_id, domain):
        return {}

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

    async def remove_scan_history(self, email, scan_id):   # UX fix 2026-07-17
        row = next((h for h in self.scan_history if h.get("id") == scan_id), None)
        if not row:
            return None
        self.scan_history = [h for h in self.scan_history if h.get("id") != scan_id]
        return row.get("url")

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
    for bucket in (m._signup_attempts, m._signup_daily_attempts, m._resend_confirm_attempts,
                   m._forgot_attempts, m._reset_attempts,
                   m._send_report_attempts, m._ownership_attempts, m._admin_action_attempts,
                   m._technician_attempts, m._laudo_attempts):
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

    async def send_welcome_confirmation(self, to_email, confirm_url):  # KL-82 Slice 2
        self.sent.append((to_email, "welcome_confirmation"))
        return {"ok": True}

    async def send_ownership_verification(self, to_email, domain, code):
        self.sent.append((to_email, code))
        return {"ok": True}

    async def send_site_removed(self, to_email, domain):
        self.sent.append((to_email, f"site_removed:{domain}"))
        return {"ok": True}

    async def send_account_deactivated(self, to_email):
        self.sent.append((to_email, "deactivated"))
        return {"ok": True}

    async def send_account_reactivated(self, to_email):
        self.sent.append((to_email, "reactivated"))
        return {"ok": True}

    async def send_technician_invite(self, to_email, domain, subject, text, target_id=None):
        self.sent.append((to_email, "technician_invite"))
        return {"ok": True}


@pytest.fixture
def mailer(monkeypatch):
    fm = _FakeMailer()
    monkeypatch.setattr(m, "_email_enabled", lambda: True)
    monkeypatch.setattr(m, "_mailer", lambda: fm)
    return fm


def test_signup_unverified_creates_unconfirmed(client, store, mailer, monkeypatch):
    # KL-82 Slice 2: signup sem código → conta criada NA HORA (email_confirmed=false) +
    # e-mail de boas-vindas com link (fire-and-forget). Não exige mais código.
    monkeypatch.setattr(m, "_spawn", lambda coro: coro.close())
    store.verified_scan_emails = set()   # nenhum e-mail verificado no scan (KL-25)
    r = client.post("/account/signup", json={"email": "novo@x.com.br", "password": "segredo123"})
    assert r.status_code == 200
    assert r.json()["user"]["email"] == "novo@x.com.br"
    assert r.json()["user"]["email_confirmed"] is False       # conta NÃO confirmada
    assert "novo@x.com.br" in store.users                     # mas JÁ criada


def test_confirm_link_confirms_email(client, store, mailer, monkeypatch):
    # KL-82 Slice 2: signup unconfirmed → confirma pelo LINK (/account/confirm?token=).
    monkeypatch.setattr(m, "_spawn", lambda coro: coro.close())
    store.verified_scan_emails = set()
    u = client.post("/account/signup", json={"email": "novo@x.com.br", "password": "segredo123"}).json()["user"]
    assert u["email_confirmed"] is False
    token = m._make_confirm_token(u["id"], "novo@x.com.br")
    r = client.get(f"/account/confirm?token={token}")
    assert r.status_code == 200 and r.json()["status"] == "confirmed"
    assert store.by_id[u["id"]]["email_confirmed"] is True
    # 2ª vez → idempotente ("already"), sem erro
    r2 = client.get(f"/account/confirm?token={token}")
    assert r2.json()["status"] == "already"


def test_confirm_invalid_token(client, store, mailer):
    r = client.get("/account/confirm?token=garbage.sig")
    assert r.status_code == 200 and r.json()["status"] == "invalid"


def test_signup_verify_no_pending(client, store, mailer):
    # Fallback dormente: /account/verify sem pending → 400 (o signup não gera mais pending).
    r = client.post("/account/verify", json={"email": "ninguem@x.com.br", "code": "123456"})
    assert r.status_code == 400


def test_signup_verified_scan_creates_confirmed(client, store, mailer, monkeypatch):
    # e-mail já verificado no scan → nasce CONFIRMADA, sem e-mail de boas-vindas.
    monkeypatch.setattr(m, "_spawn", lambda coro: coro.close())
    store.verified_scan_emails = {"javerificado@x.com.br"}
    r = client.post("/account/signup", json={"email": "javerificado@x.com.br", "password": "segredo123"})
    assert r.status_code == 200 and r.json()["user"]["email_confirmed"] is True
    assert mailer.sent == []                            # nem código, nem boas-vindas


def test_signup_disposable_email_blocked(client, store):
    # KL-85 Parte 3: e-mail descartável → 400, conta não criada.
    r = client.post("/account/signup", json={"email": "x@mailinator.com", "password": "segredo123"})
    assert r.status_code == 400 and "permanente" in r.json()["detail"].lower()
    assert "x@mailinator.com" not in store.users


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


def test_remove_scan_history(client, store):
    u = client.post("/account/signup", json={"email": "rm@x.com.br", "password": "segredo123"}).json()["user"]
    store.scan_history = [{"id": 5, "url": "https://x.com.br", "score": 80,
                           "semaphore": "amarelo", "scanned_at": None}]
    r = client.delete("/account/scan-history/5", headers=_bearer(u))
    assert r.status_code == 200 and r.json()["removed"] is True and r.json()["domain"] == "x.com.br"
    assert store.scan_history == []   # item saiu do histórico


def test_remove_scan_history_not_found(client, store):
    u = client.post("/account/signup", json={"email": "rm2@x.com.br", "password": "segredo123"}).json()["user"]
    assert client.delete("/account/scan-history/999", headers=_bearer(u)).status_code == 404


def test_remove_scan_history_requires_auth(client):
    assert client.delete("/account/scan-history/5").status_code == 401


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


def test_signup_links_owned_previous_scans(client, store):
    # KL-78 item 9: só vincula scans anteriores de sites que o usuário COMPROVADAMENTE
    # possui (contact_email == e-mail do signup). Scan avulso ≠ monitoramento.
    store.targets[55] = {"id": 55, "url": "https://old.com.br", "domain": "old.com.br",
                         "contact_email": "hist@x.com.br"}  # e-mail == contact → é dono
    store.scanned_by["hist@x.com.br"] = [55]
    u = client.post("/account/signup", json={"email": "hist@x.com.br", "password": "segredo123"}).json()["user"]
    assert (u["id"], 55) in store.sites


def test_signup_does_not_link_unowned_previous_scans(client, store):
    # KL-78 item 9 (bug catho): site apenas escaneado (não possuído) NÃO vira monitoramento.
    store.targets[55] = {"id": 55, "url": "https://catho.com.br", "domain": "catho.com.br",
                         "contact_email": None}  # não é dono
    store.scanned_by["visitante@x.com.br"] = [55]
    u = client.post("/account/signup", json={"email": "visitante@x.com.br", "password": "segredo123"}).json()["user"]
    assert (u["id"], 55) not in store.sites
    assert not store.sites


def test_signup_history_respects_plan_limit(client, store):
    # free = 1 site: url do signup (site próprio) ocupa a vaga; o histórico próprio não excede.
    store.targets[60] = {"id": 60, "url": m._norm_scan_url("https://novo.com.br"),
                         "domain": "novo.com.br", "contact_email": "cap@x.com.br"}
    store.targets[61] = {"id": 61, "url": "https://antigo.com.br", "domain": "antigo.com.br",
                         "contact_email": "cap@x.com.br"}
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
    # KL-71: o e-mail do usuário NÃO bate por e-mail nem por domínio (outrodom.com.br ≠ x.com.br)
    u = client.post("/account/signup", json={"email": "owner@outrodom.com.br", "password": "segredo123"}).json()["user"]
    store.targets[30] = {"id": 30, "url": "https://x.com.br", "domain": "x.com.br",
                         "contact_email": "contato@x.com.br"}
    store.sites[(u["id"], 30)] = {"is_owner": False}
    # e-mail não bate (nem exato nem domínio) → 403
    assert client.post("/account/sites/30/claim", headers=_bearer(u)).status_code == 403
    # e-mail bate exato → 200 + is_owner
    store.targets[30]["contact_email"] = "owner@outrodom.com.br"
    r = client.post("/account/sites/30/claim", headers=_bearer(u))
    assert r.status_code == 200 and r.json()["is_owner"] is True


def test_claim_by_domain_match(client, store):
    # KL-71 Bug 1: o e-mail bate por DOMÍNIO (owner@x.com.br → x.com.br), sem contact_email
    u = client.post("/account/signup", json={"email": "owner@x.com.br", "password": "segredo123"}).json()["user"]
    store.targets[31] = {"id": 31, "url": "https://x.com.br", "domain": "x.com.br",
                         "contact_email": "terceiro@gmail.com"}
    store.sites[(u["id"], 31)] = {"is_owner": False}
    r = client.post("/account/sites/31/claim", headers=_bearer(u))
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


def test_signup_claim_unowned_not_monitored(client, store):
    # KL-78 item 9: reivindicar (signup com url) um site que NÃO é do usuário não o
    # auto-monitora — só sinaliza que pode monitorar explicitamente (botão "Monitorar").
    store.targets[11] = {"id": 11, "url": "https://loja.com.br", "domain": "loja.com.br",
                         "contact_email": "dono@loja.com.br"}
    r = client.post("/account/signup", json={"email": "visitante@x.com.br",
                                             "password": "segredo123", "url": "https://loja.com.br"})
    claim = r.json().get("claim") or {}
    assert claim.get("site_added") is False and claim.get("is_owner") is not True
    assert claim.get("can_monitor") is True
    assert not store.sites  # não vinculou (não é dono)


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


# --- KL-69: gestão de usuários (ações admin) -------------------------------- #

def _admin():
    return {"Authorization": f"Bearer {m._create_token('admin')}"}


def test_admin_remove_user_site(client, store, mailer):
    u = client.post("/account/signup", json={"email": "u@x.com.br", "password": "segredo123"}).json()["user"]
    store.targets[40] = {"id": 40, "url": "https://alvo.com.br", "domain": "alvo.com.br",
                         "contact_email": "c@alvo.com.br"}
    store.sites[(u["id"], 40)] = {"is_owner": True}
    r = client.post(f"/admin/users/{u['id']}/remove-site",
                    json={"target_id": 40, "notify": True}, headers=_admin())
    assert r.status_code == 200 and r.json()["removed"] is True and r.json()["notified"] is True
    assert (u["id"], 40) not in store.sites
    assert mailer.sent[-1] == ("u@x.com.br", "site_removed:alvo.com.br")


def test_admin_remove_user_site_404(client, store):
    u = client.post("/account/signup", json={"email": "u@x.com.br", "password": "segredo123"}).json()["user"]
    r = client.post(f"/admin/users/{u['id']}/remove-site", json={"target_id": 999}, headers=_admin())
    assert r.status_code == 404


def test_admin_deactivate_reactivate(client, store, mailer):
    u = client.post("/account/signup", json={"email": "u@x.com.br", "password": "segredo123"}).json()["user"]
    r = client.post(f"/admin/users/{u['id']}/deactivate", json={"notify": True}, headers=_admin())
    assert r.status_code == 200 and r.json()["deactivated"] is True and r.json()["notified"] is True
    assert store.by_id[u["id"]]["is_active"] is False
    r2 = client.post(f"/admin/users/{u['id']}/reactivate", json={"notify": True}, headers=_admin())
    assert r2.status_code == 200 and r2.json()["reactivated"] is True
    assert store.by_id[u["id"]]["is_active"] is True


def test_login_deactivated_403(client, store):
    u = client.post("/account/signup", json={"email": "dead@x.com.br", "password": "segredo123"}).json()["user"]
    store.by_id[u["id"]]["is_active"] = False
    r = client.post("/account/login", json={"email": "dead@x.com.br", "password": "segredo123"})
    assert r.status_code == 403 and "desativada" in r.json()["detail"].lower()


def test_admin_action_requires_jwt(client, store):
    u = client.post("/account/signup", json={"email": "u@x.com.br", "password": "segredo123"}).json()["user"]
    assert client.post(f"/admin/users/{u['id']}/deactivate", json={}).status_code == 401


def test_clean_blocked_sites_dry_and_apply(client, store, mailer):
    u1 = client.post("/account/signup", json={"email": "a@x.com.br", "password": "segredo123"}).json()["user"]
    u2 = client.post("/account/signup", json={"email": "b@x.com.br", "password": "segredo123"}).json()["user"]
    store.targets[50] = {"id": 50, "url": "https://gmail.com", "domain": "gmail.com"}
    store.targets[51] = {"id": 51, "url": "https://ok.com.br", "domain": "ok.com.br"}
    store.sites[(u1["id"], 50)] = {"is_owner": False}   # bloqueado
    store.sites[(u2["id"], 51)] = {"is_owner": False}   # ok
    dry = client.post("/admin/clean-blocked-sites?dry_run=1", headers=_admin()).json()
    assert dry["found"] == 1 and dry["removed"] == 0 and dry["dry_run"] is True
    assert dry["items"][0]["domain"] == "gmail.com" and dry["items"][0]["email"] == "a@x.com.br"
    assert (u1["id"], 50) in store.sites                # dry-run não removeu
    res = client.post("/admin/clean-blocked-sites?dry_run=0", headers=_admin()).json()
    assert res["removed"] == 1 and res["notified"] == 1
    assert (u1["id"], 50) not in store.sites
    assert (u2["id"], 51) in store.sites                # o site legítimo permanece


# --- KL-44 P3: técnico vinculado + laudo compartilhável --------------------- #

def test_signup_role_technician(client, store):
    r = client.post("/account/signup", json={"email": "tec@x.com.br", "password": "segredo123",
                                             "role": "technician"})
    assert r.status_code == 200 and r.json()["user"]["role"] == "technician"


def test_signup_auto_links_pending_invite(client, store):
    # dono convida um e-mail; quando esse e-mail cria conta, o vínculo vira active.
    owner = client.post("/account/signup", json={"email": "dono@x.com.br", "password": "segredo123"}).json()["user"]
    store.tech_links.append({"id": 1, "owner_user_id": owner["id"], "target_id": 5,
                             "technician_email": "tec@x.com.br", "technician_user_id": None,
                             "status": "pending", "invite_code": "ABC12345", "linked_at": None,
                             "last_access_at": None})
    client.post("/account/signup", json={"email": "tec@x.com.br", "password": "segredo123", "role": "technician"})
    assert store.tech_links[0]["status"] == "active"


def test_technician_invite_and_revoke(client, store, mailer):
    owner = client.post("/account/signup", json={"email": "o@x.com.br", "password": "segredo123"}).json()["user"]
    store.sites[(owner["id"], 60)] = {"is_owner": True}
    store.targets[60] = {"id": 60, "url": "https://alvo.com.br", "domain": "alvo.com.br", "last_scan_score": 73}
    store.scans[60] = {"id": 900, "score": 73, "semaphore": "amarelo"}
    r = client.post("/account/technician/invite",
                    json={"target_id": 60, "technician_email": "tec@x.com.br"}, headers=_bearer(owner))
    assert r.status_code == 200 and r.json()["invited"] is True and r.json()["invite_code"]
    assert mailer.sent[-1][1] == "technician_invite"
    link_id = store.tech_links[-1]["id"]
    rev = client.post("/account/technician/revoke", json={"link_id": link_id}, headers=_bearer(owner))
    assert rev.status_code == 200 and store.tech_links[-1]["status"] == "revoked"


def test_technician_invite_site_not_owned(client, store, mailer):
    owner = client.post("/account/signup", json={"email": "o@x.com.br", "password": "segredo123"}).json()["user"]
    r = client.post("/account/technician/invite",
                    json={"target_id": 999, "technician_email": "t@x.com.br"}, headers=_bearer(owner))
    assert r.status_code == 404


def test_technician_search(client, store):
    u = client.post("/account/signup", json={"email": "o@x.com.br", "password": "segredo123"}).json()["user"]
    client.post("/account/signup", json={"email": "tec@x.com.br", "password": "segredo123", "role": "technician"})
    found = client.get("/account/technician/search?email=tec@x.com.br", headers=_bearer(u)).json()
    assert found["found"] is True and "user_id" in found
    nope = client.get("/account/technician/search?email=nobody@x.com.br", headers=_bearer(u)).json()
    assert nope["found"] is False


def test_shared_report_create(client, store):
    owner = client.post("/account/signup", json={"email": "o@x.com.br", "password": "segredo123"}).json()["user"]
    store.sites[(owner["id"], 61)] = {"is_owner": True}
    store.targets[61] = {"id": 61, "url": "https://alvo.com.br", "domain": "alvo.com.br", "last_scan_score": 73}
    store.scans[61] = {"id": 901, "score": 73, "semaphore": "amarelo"}
    r = client.post("/account/shared-report/create", json={"target_id": 61}, headers=_bearer(owner)).json()
    assert r["code"] and r["url"].endswith(r["code"]) and "wa.me" in r["whatsapp_url"]


def test_public_laudo(client, store):
    owner = client.post("/account/signup", json={"email": "o@x.com.br", "password": "segredo123"}).json()["user"]
    store.shared["A7K2M9"] = {"id": 1, "code": "A7K2M9", "target_id": 62, "owner_user_id": owner["id"],
                              "scan_id": 902, "expired": False, "domain": "alvo.com.br",
                              "score": 73, "semaphore": "amarelo", "technician_link_id": None,
                              "checks_json": [{"check_id": "check_02", "name": "HSTS", "status": "FAIL",
                                               "severity": "ALTA", "evidence": "sem header"}],
                              "scanned_at": None}
    r = client.get("/public/laudo/A7K2M9").json()
    assert r["status"] == "ok" and r["domain"] == "alvo.com.br"
    assert r["fail_count"] == 1 and r["top_action"]["name"] == "HSTS"
    # sem dado interno do dono
    assert "owner_email" not in r and "contact_email" not in str(r)


def test_public_laudo_expired_and_missing(client, store):
    store.shared["EXP1234"] = {"code": "EXP1234", "target_id": 1, "owner_user_id": 1, "expired": True,
                               "domain": "x.com.br", "checks_json": []}
    assert client.get("/public/laudo/EXP1234").json()["status"] == "expired"
    assert client.get("/public/laudo/NOPE9999").json()["status"] == "not_found"


# --------------------------------------------------------------------------- #
# KL-71 — fixes ownership/técnico/landing
# --------------------------------------------------------------------------- #

def _reg_target(store, tid, domain, contact=None):
    store.targets[tid] = {"id": tid, "url": f"https://{domain}", "domain": domain,
                          "contact_email": contact, "last_scan_score": 80}


# Bug 1 — _ownership_method (Tier 1: e-mail exato OU domínio; nunca provedor público)

def test_ownership_method_exact_email(store):
    _reg_target(store, 70, "igoove.com", contact="contato@igoove.com")
    assert asyncio.run(m._ownership_method("contato@igoove.com", 70)) == "auto_email"


def test_ownership_method_domain_match(store):
    # e-mail@igoove.com + site igoove.com → auto_domain (mesmo com contact_email diferente)
    _reg_target(store, 71, "igoove.com", contact="jscidinei@gmail.com")
    assert asyncio.run(m._ownership_method("cidinei@igoove.com", 71)) == "auto_domain"


def test_ownership_method_domain_match_strips_www(store):
    _reg_target(store, 72, "www.igoove.com", contact=None)
    assert asyncio.run(m._ownership_method("cidinei@igoove.com", 72)) == "auto_domain"


def test_ownership_method_public_provider_rejected(store):
    # e-mail@gmail.com + site gmail.com → NÃO auto-verifica (provedor público)
    _reg_target(store, 73, "gmail.com", contact=None)
    assert asyncio.run(m._ownership_method("alguem@gmail.com", 73)) is None


def test_ownership_method_different_domain(store):
    _reg_target(store, 74, "igoove.com", contact="contato@igoove.com")
    assert asyncio.run(m._ownership_method("outra@empresa.com.br", 74)) is None


# Bug 1 — auto-verificação por domínio no add-site, respeitando first-come

def test_add_site_domain_match_auto_verifies(client, store):
    u = client.post("/account/signup", json={"email": "cidinei@igoove.com", "password": "segredo123"}).json()["user"]
    _reg_target(store, 80, "igoove.com", contact="jscidinei@gmail.com")
    # força o resolve para o target 80
    m._resolve_or_create_target  # noqa: B018
    store.targets_by_url = {"https://igoove.com": 80}

    async def fake_resolve(url, source="dashboard"):
        return 80
    import api.main as _m
    orig = _m._resolve_or_create_target
    _m._resolve_or_create_target = fake_resolve
    try:
        r = client.post("/account/sites", json={"url": "https://igoove.com"}, headers=_bearer(u))
    finally:
        _m._resolve_or_create_target = orig
    assert r.status_code == 200
    assert store.sites[(u["id"], 80)]["is_owner"] is True
    assert store.sites[(u["id"], 80)]["verification_method"] == "auto_domain"


def test_add_site_domain_match_respects_first_come(client, store):
    owner = client.post("/account/signup", json={"email": "first@igoove.com", "password": "segredo123"}).json()["user"]
    _reg_target(store, 81, "igoove.com", contact=None)
    store.sites[(owner["id"], 81)] = {"is_owner": True}  # já tem dono
    u2 = client.post("/account/signup", json={"email": "second@igoove.com", "password": "segredo123"}).json()["user"]

    async def fake_resolve(url, source="dashboard"):
        return 81
    import api.main as _m
    orig = _m._resolve_or_create_target
    _m._resolve_or_create_target = fake_resolve
    try:
        r = client.post("/account/sites", json={"url": "https://igoove.com"}, headers=_bearer(u2))
    finally:
        _m._resolve_or_create_target = orig
    assert r.status_code == 200
    assert store.sites[(u2["id"], 81)]["is_owner"] is False  # NÃO virou dono (first-come)


# Bug 3 — ownership_status expõe has_other_owner

def test_ownership_status_has_other_owner(client, store):
    owner = client.post("/account/signup", json={"email": "own@igoove.com", "password": "segredo123"}).json()["user"]
    monitor = client.post("/account/signup", json={"email": "mon@x.com.br", "password": "segredo123"}).json()["user"]
    _reg_target(store, 90, "igoove.com", contact=None)
    store.sites[(owner["id"], 90)] = {"is_owner": True}
    store.sites[(monitor["id"], 90)] = {"is_owner": False}
    r = client.get("/account/ownership/status?target_id=90", headers=_bearer(monitor)).json()
    assert r["has_other_owner"] is True and r["is_owner"] is False
    assert r["verification_available"] is False


# Bug 4 — convite de técnico cria laudo e link /laudo/{code}

def test_technician_invite_creates_laudo(client, store):
    owner = client.post("/account/signup", json={"email": "dono@x.com.br", "password": "segredo123"}).json()["user"]
    store.sites[(owner["id"], 95)] = {"is_owner": True}
    _reg_target(store, 95, "alvo.com.br", contact=None)
    store.scans[95] = {"id": 950, "score": 73, "semaphore": "amarelo"}
    r = client.post("/account/technician/invite",
                    json={"target_id": 95, "technician_email": "tec@empresa.com.br"},
                    headers=_bearer(owner))
    assert r.status_code == 200
    body = r.json()
    assert body["invited"] is True and body["laudo_code"]
    # o laudo existe e é acessível pelo código
    assert store.shared.get(body["laudo_code"]) is not None


def test_technician_invite_link_template_uses_laudo():
    from notifier import bulletin as bl
    txt = bl.build_technician_invite({"domain": "alvo.com.br", "score": 73, "semaphore": "amarelo",
                                      "owner_masked": "d***o@x.com.br", "code": "ABC123", "invite_code": "XY12"})
    assert "https://klarim.net/laudo/ABC123" in txt


def test_technician_invite_link_falls_back_to_profile_without_scan():
    from notifier import bulletin as bl
    txt = bl.build_technician_invite({"domain": "alvo.com.br", "score": None, "semaphore": None,
                                      "owner_masked": "d***o@x.com.br", "code": "", "invite_code": "XY12"})
    assert "https://klarim.net/site/alvo.com.br" in txt and "/laudo/" not in txt


# Bug 6 — validação de conflito de papel

def test_technician_invite_self_invite_422(client, store):
    owner = client.post("/account/signup", json={"email": "dono@x.com.br", "password": "segredo123"}).json()["user"]
    store.sites[(owner["id"], 96)] = {"is_owner": True}
    _reg_target(store, 96, "alvo.com.br")
    r = client.post("/account/technician/invite",
                    json={"target_id": 96, "technician_email": "dono@x.com.br"},
                    headers=_bearer(owner))
    assert r.status_code == 422 and "convidar" in r.json()["detail"]


def test_technician_invite_owner_as_tech_422(client, store):
    owner = client.post("/account/signup", json={"email": "dono@igoove.com", "password": "segredo123"}).json()["user"]
    store.sites[(owner["id"], 97)] = {"is_owner": True}
    _reg_target(store, 97, "igoove.com")
    # convidar o próprio dono verificado (por outro e-mail owner) — aqui o dono é o mesmo user
    other = client.post("/account/signup", json={"email": "outro@x.com.br", "password": "segredo123"}).json()["user"]
    store.sites[(other["id"], 97)] = {"is_owner": False}
    store.sites[(other["id"], 97)] = {"is_owner": False}
    r = client.post("/account/technician/invite",
                    json={"target_id": 97, "technician_email": "dono@igoove.com"},
                    headers=_bearer(other))
    assert r.status_code == 422 and "dono" in r.json()["detail"].lower()


def test_technician_invite_already_linked_422(client, store):
    owner = client.post("/account/signup", json={"email": "dono@x.com.br", "password": "segredo123"}).json()["user"]
    store.sites[(owner["id"], 98)] = {"is_owner": True}
    _reg_target(store, 98, "alvo.com.br")
    store.scans[98] = {"id": 980, "score": 50, "semaphore": "vermelho"}
    # 1º convite cria o vínculo (pending) → marca como active manualmente
    client.post("/account/technician/invite",
                json={"target_id": 98, "technician_email": "tec@empresa.com.br"}, headers=_bearer(owner))
    for l in store.tech_links:
        l["status"] = "active"
    # 2º convite ao mesmo e-mail → 422
    r = client.post("/account/technician/invite",
                    json={"target_id": 98, "technician_email": "tec@empresa.com.br"},
                    headers=_bearer(owner))
    assert r.status_code == 422 and "vinculado" in r.json()["detail"]


# Bug 8 — remoção self-service de um site

def test_remove_site_self_service(client, store):
    u = client.post("/account/signup", json={"email": "u@x.com.br", "password": "segredo123"}).json()["user"]
    _reg_target(store, 99, "alvo.com.br")
    store.sites[(u["id"], 99)] = {"is_owner": True}
    r = client.delete("/account/sites/99", headers=_bearer(u))
    assert r.status_code == 200 and r.json()["removed"] is True
    assert (u["id"], 99) not in store.sites
    assert ("alvo.com.br" in [d for (_uid, d) in getattr(store, "disabled_site_vigilias", [])])


def test_remove_site_not_found(client, store):
    u = client.post("/account/signup", json={"email": "u@x.com.br", "password": "segredo123"}).json()["user"]
    assert client.delete("/account/sites/12345", headers=_bearer(u)).status_code == 404
