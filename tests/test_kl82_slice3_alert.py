"""KL-82 Slice 3 — Fluxo 2 do alerta: alert-access (link HMAC) + sessão temporária +
signup-from-alert. Offline (TestClient + FakeStore).

Cobre:
  * contrato cross-módulo: link do email_client valida no api.main.
  * GET /alert-access: token válido → cookie + redirect ao /scan?url=; inválido → home.
  * /scan/result com cookie de alerta: acesso COMPLETO ao site da sessão; ESCOPADO
    (outro site → cai para anonymous, sem vazar checks); expõe só o hint mascarado.
  * POST /account/signup-from-alert: só senha → conta confirmed source='hmac' + site
    vinculado; e-mail já com conta → {existing_account}; sem sessão → 401.
"""

from __future__ import annotations

from types import SimpleNamespace
from urllib.parse import unquote

import pytest
from fastapi.testclient import TestClient

import api.main as m
import notifier.email_client as ec
from api import auth_users


def _fake_report():
    metas = m.CHECK_META
    results = []
    for i, meta in enumerate(metas[:6]):
        cid = meta["check_id"]
        fail = i == 0
        results.append(SimpleNamespace(
            check_id=cid, name=meta["name"], status="FAIL" if fail else "PASS",
            severity="CRITICA" if fail else "BAIXA",
            evidence=f"ev-{cid}" if fail else "", owasp="A02", cwe="CWE-319", lgpd="Art. 46"))
    score = SimpleNamespace(score=68, semaphore="amarelo", grade_icon="🟡",
                            failed=1, passed=5, inconclusive=0)
    return SimpleNamespace(url="https://x.com.br", started_at="", finished_at="2026-07-19T10:00:00Z",
                           duration_s=1.0, results=results, score=score, privacy=None)


class FakeStore:
    def __init__(self):
        self.users = {}
        self.by_id = {}
        self.next_id = 1
        self.sites = {}
        self.alert_sessions = []
        self.target = {"id": 42, "domain": "x.com.br", "url": "https://x.com.br",
                       "status": "descoberto", "sector": "outro", "contact_email": "dono@x.com.br"}

    async def get_target_by_url(self, url):
        return self.target

    async def get_site_profile(self, tid):
        return {"public_visible": True}

    async def global_avg_score(self):
        return {"avg_score": 64, "count": 8000}

    # alert sessions
    async def create_alert_session(self, token_hash, email, target_id, expires_at):
        self.alert_sessions.append({"token_hash": token_hash, "email": email,
                                    "target_id": target_id, "converted": False})

    async def mark_alert_session_converted(self, token_hash):
        for s in self.alert_sessions:
            if s["token_hash"] == token_hash:
                s["converted"] = True

    # users / claim
    async def email_has_verified_scan(self, email):
        return False

    async def create_user(self, email, password_hash, name=None, role="owner",
                          email_confirmed=True, confirmation_source=None):
        email = email.lower().strip()
        if email in self.users:
            return None
        u = {"id": self.next_id, "email": email, "name": name, "plan": "free",
             "max_sites": 1, "is_active": True, "role": role, "email_confirmed": email_confirmed,
             "confirmation_source": confirmation_source, "password_hash": password_hash}
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

    async def count_user_sites(self, uid):
        return sum(1 for (u, _t) in self.sites if u == uid)

    async def get_targets_scanned_by_email(self, email, limit=1):
        return []

    async def link_user_site(self, uid, tid, is_owner=False):
        if (uid, tid) in self.sites:
            return False
        self.sites[(uid, tid)] = {"is_owner": is_owner}
        return True

    async def get_user_site(self, uid, tid):
        v = self.sites.get((uid, tid))
        return {"id": 1, "user_id": uid, "target_id": tid, "is_owner": v["is_owner"]} if v else None

    async def mark_site_verified(self, uid, tid, method):
        self.sites.setdefault((uid, tid), {})["is_owner"] = True
        self.sites[(uid, tid)]["method"] = method
        return True

    async def site_has_owner(self, tid, exclude_user_id=None):
        return any(v.get("is_owner") for (u, t), v in self.sites.items()
                   if t == tid and (exclude_user_id is None or u != exclude_user_id))

    async def get_target(self, tid):
        return self.target

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
    monkeypatch.setattr(m, "_email_enabled", lambda: False)
    monkeypatch.setattr(m, "_spawn", lambda coro: coro.close())

    async def _fake_safe_scan(url, full=True, ingest_source=None, scanned_by_email=None):
        return _fake_report()
    monkeypatch.setattr(m, "_safe_scan", _fake_safe_scan)
    # KL-68 guard de domínio: x.com.br não é público/institucional → (blocked, reason)
    monkeypatch.setattr(m.domain_guard, "is_blocked_domain", lambda d: (False, None))
    for b in (m._scan_anon_hour, m._scan_anon_day, m._alert_access_attempts,
              m._signup_alert_attempts, m._signup_attempts, m._signup_daily_attempts):
        b.clear()
    return s


@pytest.fixture
def client(store):
    return TestClient(m.app, raise_server_exceptions=False)


def _access_token():
    return ec.alert_access_token("dono@x.com.br", 42, "x.com.br", "k" * 64)


# --------------------------------------------------------------------------- #
# 1. Contrato cross-módulo
# --------------------------------------------------------------------------- #

def test_email_link_verifies_in_api(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "k" * 64)   # api.main._scan_token_secret() lê do env
    link = ec.build_alert_access_link("dono@x.com.br", 42, "x.com.br", "k" * 64)
    assert "/api/alert-access?token=" in link
    tok = unquote(link.split("token=")[1])
    p = m._verify_alert_access_token(tok)
    assert p and p["email"] == "dono@x.com.br" and p["tid"] == 42 and p["domain"] == "x.com.br"


# --------------------------------------------------------------------------- #
# 2. /alert-access
# --------------------------------------------------------------------------- #

def test_alert_access_sets_cookie_and_redirects(client, store):
    r = client.get(f"/alert-access?token={_access_token()}", follow_redirects=False)
    assert r.status_code == 302
    assert "/scan?url=" in r.headers["location"]
    assert m._ALERT_COOKIE in r.cookies or "set-cookie" in {k.lower() for k in r.headers}
    assert len(store.alert_sessions) == 1 and store.alert_sessions[0]["target_id"] == 42


def test_alert_access_invalid_token_redirects_home(client):
    r = client.get("/alert-access?token=garbage.sig", follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"] == "/"


# --------------------------------------------------------------------------- #
# 3. /scan/result com a sessão do alerta
# --------------------------------------------------------------------------- #

def _cookie_client(store):
    # O cookie Secure não round-trip no TestClient (http://testserver) — seta manualmente e
    # semeia a linha de alert_sessions (que o /alert-access criaria) para o teste de conversão.
    import hashlib
    c = TestClient(m.app, raise_server_exceptions=False)
    tok = m._make_alert_session_token("dono@x.com.br", 42, "x.com.br")
    c.cookies.set(m._ALERT_COOKIE, tok)
    store.alert_sessions.append({"token_hash": hashlib.sha256(tok.encode()).hexdigest(),
                                 "email": "dono@x.com.br", "target_id": 42, "converted": False})
    return c


def test_scan_result_alert_session_full_access(store):
    c = _cookie_client(store)
    j = c.get("/scan/result?url=https://x.com.br").json()
    assert j["access_level"] == "alert_session"
    assert j["pdf_available"] is True and isinstance(j["checks"], list)
    assert any("ev-check_" in (ch.get("evidence") or "") for ch in j["checks"])
    assert j["alert_signup"] is True
    assert j["alert_email_hint"] and "@" in j["alert_email_hint"] and "*" in j["alert_email_hint"]


def test_scan_result_alert_session_scoped_to_target(store):
    # a sessão é do x.com.br; pedir OUTRO site → cai para anonymous (escopo respeitado).
    # KL-89: o anônimo agora vê checks por NOME, mas NUNCA evidência técnica nem LGPD do outro site.
    c = _cookie_client(store)
    store.target = {"id": 99, "domain": "outro.com.br", "url": "https://outro.com.br",
                    "status": "descoberto", "sector": "outro"}
    j = c.get("/scan/result?url=https://outro.com.br").json()
    assert j["access_level"] == "anonymous"
    assert j["checks_names_only"] is True
    assert "ev-check_" not in str(j) and "privacy_indicators" not in j


# --------------------------------------------------------------------------- #
# 4. /account/signup-from-alert
# --------------------------------------------------------------------------- #

def test_signup_from_alert_creates_confirmed_account(store):
    c = _cookie_client(store)
    r = c.post("/account/signup-from-alert", json={"password": "segredo123"})
    assert r.status_code == 200
    u = r.json()["user"]
    assert u["email"] == "dono@x.com.br" and u["email_confirmed"] is True
    assert store.by_id[u["id"]]["confirmation_source"] == "hmac"
    # site vinculado + posse auto-verificada (e-mail == contact_email → Tier 1)
    assert (u["id"], 42) in store.sites and store.sites[(u["id"], 42)]["is_owner"] is True
    assert store.alert_sessions and store.alert_sessions[0]["converted"] is True


def test_signup_from_alert_existing_account(store):
    store.users["dono@x.com.br"] = {"id": 1, "email": "dono@x.com.br"}
    c = _cookie_client(store)
    r = c.post("/account/signup-from-alert", json={"password": "segredo123"})
    assert r.status_code == 200 and r.json().get("existing_account") is True


def test_signup_from_alert_requires_session(client):
    r = client.post("/account/signup-from-alert", json={"password": "segredo123"})
    assert r.status_code == 401


def test_signup_from_alert_short_password(store):
    c = _cookie_client(store)
    r = c.post("/account/signup-from-alert", json={"password": "curta"})
    assert r.status_code == 400
