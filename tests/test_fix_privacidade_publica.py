"""Fix compliance urgente — os indicadores DETALHADOS de privacidade (✅/❌ por indicador
+ referência LGPD) NÃO podem vazar para superfícies PÚBLICAS. Offline (TestClient +
FakeStore, sem rede/DB).

Regra coberta:
  * Perfil público (`/public/profile/{domain}`): só `score`/`total`, sem `checks`.
  * Endpoint logado (`/account/privacy/{domain}`): exige JWT; devolve os detalhes.
  * Resumo gratuito do scan (`_summary_payload`, `full=False`): só `score`/`total`.
  * Selo (`/seal/{domain}`): só `privacy_score`/`privacy_total` (já coberto no KL-44 P5).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import api.main as m
from api import auth_users


_PRIVACY = {
    "score": 3,
    "total": 8,
    "checks": [
        {"id": "cookie_consent", "name": "Banner de Cookies", "status": "FAIL",
         "evidence": "sem CMP", "lgpd_ref": "Art. 8º", "severity": "high"},
        {"id": "https_forms", "name": "Formulários em HTTPS", "status": "PASS",
         "evidence": "ok", "lgpd_ref": "Art. 46", "severity": "high"},
    ],
    "disclaimer": "Não constitui assessoria jurídica.",
}


class FakeStore:
    def __init__(self):
        self.users = {}
        self.by_id = {}
        self.next_id = 1
        self.target = {"id": 1, "domain": "x.com.br", "url": "https://x.com.br",
                       "status": "descoberto", "sector": "outro",
                       "last_scan_score": 42, "last_scan_at": None, "platform": None}

    # --- perfil público / privacy ---
    async def get_target_by_domain(self, d):
        return self.target if d.lower().strip() == "x.com.br" else None

    async def get_site_profile(self, tid):
        return {"public_visible": True}

    async def get_target_classifications(self, tid):
        return []

    async def get_latest_scan_full(self, tid):
        return {"semaphore": "vermelho", "checks_json": {"privacy": _PRIVACY}}

    async def sector_benchmark(self, sector, min_count=10):
        return None

    async def global_avg_score(self):
        return {"avg_score": 60, "count": 8000}

    async def site_has_owner(self, tid):
        return False

    # --- users (para o JWT de /account/privacy) ---
    async def email_has_verified_scan(self, email):
        return True

    async def create_user(self, email, password_hash, name=None, role="owner"):
        email = email.lower().strip()
        if email in self.users:
            return None
        u = {"id": self.next_id, "email": email, "name": name, "plan": "free",
             "max_sites": 1, "is_active": True, "password_hash": password_hash, "role": role}
        self.users[email] = u
        self.by_id[u["id"]] = u
        self.next_id += 1
        return {k: v for k, v in u.items() if k != "password_hash"}

    async def get_user_by_id(self, uid):
        u = self.by_id.get(int(uid))
        return {k: v for k, v in u.items() if k != "password_hash"} if u else None

    async def get_user_by_email(self, email, with_hash=False):
        u = self.users.get(email.lower().strip())
        if not u:
            return None
        return dict(u) if with_hash else {k: v for k, v in u.items() if k != "password_hash"}

    async def touch_user_login(self, uid):
        pass

    async def count_user_sites(self, uid):
        return 0

    async def set_lead_account(self, email, account_id):
        pass

    async def auto_link_technician_by_email(self, email, technician_user_id):
        return 0


@pytest.fixture
def store(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "k" * 64)
    s = FakeStore()
    monkeypatch.setattr(m, "get_target_store", lambda: s)
    monkeypatch.setattr("discovery.store.get_target_store", lambda: s)
    monkeypatch.setattr(auth_users, "_secret", lambda: "k" * 64)
    monkeypatch.setattr(m, "_email_enabled", lambda: False)
    return s


@pytest.fixture
def client(store):
    return TestClient(m.app, raise_server_exceptions=False)


def _bearer(user):
    return {"Authorization": f"Bearer {auth_users.create_user_token(user)}"}


def _signup(client, email="user@x.com.br", pw="segredo123"):
    return client.post("/account/signup", json={"email": email, "password": pw}).json()["user"]


# --------------------------------------------------------------------------- #
# 1. Perfil público — só score/total, NUNCA os checks.
# --------------------------------------------------------------------------- #

def test_public_profile_hides_privacy_details(client):
    j = client.get("/public/profile/x.com.br").json()
    assert j["status"] == "ok"
    assert j["privacy"] == {"score": 3, "total": 8}
    # blindagem contra regressão: nenhum vestígio de detalhe no corpo público
    blob = str(j).lower()
    assert "checks" not in blob
    assert "lgpd_ref" not in blob and "art. 8" not in blob
    assert "disclaimer" not in blob and "assessoria" not in blob


def test_public_profile_privacy_none_when_no_scan(client, store):
    store.get_latest_scan_full = lambda tid: _async(None)
    j = client.get("/public/profile/x.com.br").json()
    assert j["privacy"] is None


# --------------------------------------------------------------------------- #
# 2. Endpoint logado — exige JWT e devolve os detalhes.
# --------------------------------------------------------------------------- #

def test_account_privacy_requires_auth(client):
    assert client.get("/account/privacy/x.com.br").status_code == 401


def test_account_privacy_returns_details_when_logged_in(client):
    u = _signup(client)
    j = client.get("/account/privacy/x.com.br", headers=_bearer(u)).json()
    assert j["status"] == "ok"
    assert j["privacy"]["score"] == 3 and j["privacy"]["total"] == 8
    assert isinstance(j["privacy"]["checks"], list) and len(j["privacy"]["checks"]) == 2
    assert j["privacy"]["checks"][0]["lgpd_ref"] == "Art. 8º"


def test_account_privacy_hidden_site_not_found(client, store):
    u = _signup(client)
    store.get_site_profile = lambda tid: _async({"public_visible": False})
    j = client.get("/account/privacy/x.com.br", headers=_bearer(u)).json()
    assert j["status"] == "not_found"


def test_account_privacy_unknown_domain(client):
    u = _signup(client)
    j = client.get("/account/privacy/naoexiste.com.br", headers=_bearer(u)).json()
    assert j["status"] == "not_found"


# --------------------------------------------------------------------------- #
# 3. Resumo gratuito do scan — só score/total; completo mantém detalhes.
# --------------------------------------------------------------------------- #

def _report():
    return SimpleNamespace(url="https://x.com.br", started_at="", finished_at="",
                           duration_s=0.0, results=[], score=None, privacy=dict(_PRIVACY))


def test_scan_summary_free_hides_privacy_details():
    payload = m._summary_payload(_report(), full=False)
    assert payload["privacy"] == {"score": 3, "total": 8}


def test_scan_summary_full_keeps_privacy_details():
    payload = m._summary_payload(_report(), full=True)
    assert "checks" in payload["privacy"] and len(payload["privacy"]["checks"]) == 2


# --------------------------------------------------------------------------- #
# helper — embrulha um valor síncrono num awaitable (monkeypatch de método async)
# --------------------------------------------------------------------------- #
async def _await(v):
    return v


def _async(v):
    return _await(v)
