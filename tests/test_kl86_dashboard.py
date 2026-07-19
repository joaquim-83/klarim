"""KL-86 — dashboard agregado (/account/dashboard-summary) + helpers. Offline (TestClient
+ FakeStore).

Cobre:
  * helpers puros: _dashboard_categories, _ssl_expiry_days, _score_trend, _build_checklist,
    _vigilia_summary, _new_user_checklist.
  * endpoint: com site (todos os campos) e sem site; checklist reage a e-mail/queda/SSL/tudo-ok.
  * contact_email NUNCA no payload.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

import api.main as m
from api import auth_users


# --------------------------------------------------------------------------- #
# Helpers puros
# --------------------------------------------------------------------------- #

def _checks(fail_ids=(), ssl_days=None):
    """Monta uma lista de checks (checks_json) com alguns FAIL e evidência de SSL opcional."""
    metas = m.CHECK_META
    out = []
    for meta in metas:
        cid = meta["check_id"]
        st = "FAIL" if cid in fail_ids else "PASS"
        ev = ""
        if ssl_days is not None and cid == "check_42_cert_chain":
            ev = f"Certificado válido até 2026-10-05 ({ssl_days} dias). Cadeia completa."
        out.append({"check_id": cid, "name": meta["name"], "status": st,
                    "severity": "ALTA" if st == "FAIL" else "BAIXA", "evidence": ev})
    return out


def test_dashboard_categories_shape():
    cats = m._dashboard_categories(_checks(fail_ids=("check_01_https",)))
    assert len(cats) == 6
    ids = {c["id"] for c in cats}
    assert {"transport", "headers", "supply_chain", "dns_email", "content", "osint"} <= ids
    tls = next(c for c in cats if c["id"] == "transport")
    assert tls["total"] >= 1 and 0 <= tls["passed"] <= tls["total"]
    assert tls["status"] in ("ok", "warning", "critical")


def test_ssl_expiry_days_parses_evidence():
    assert m._ssl_expiry_days(_checks(ssl_days=10)) == 10
    assert m._ssl_expiry_days(_checks()) is None  # sem evidência de dias


def test_score_trend():
    assert m._score_trend({"score": 80}, {"score": 70}) == ("up", 10)
    assert m._score_trend({"score": 60}, {"score": 70}) == ("down", -10)
    assert m._score_trend({"score": 71}, {"score": 70}) == ("stable", 1)
    assert m._score_trend({"score": 80}, None) == ("stable", 0)


def test_vigilia_summary():
    v = m._vigilia_summary([
        {"enabled": True, "last_status": "ok", "alert_count": 0, "site_domain": "x.com.br"},
        {"enabled": True, "last_status": "error", "alert_count": 2, "site_domain": "x.com.br"},
        {"enabled": False, "last_status": "alert", "alert_count": 1, "site_domain": "outro.com.br"},
    ], "x.com.br")
    assert v["active"] == 2 and v["ok"] == 1 and v["error"] == 1 and v["alerts"] == 2


def test_new_user_checklist():
    items = m._new_user_checklist({"email_confirmed": False})
    ids = {i["id"] for i in items}
    assert "add_site" in ids and "confirm_email" in ids


def test_checklist_email_and_dropped_and_ssl():
    user = {"email_confirmed": False}
    target = {"id": 7}
    latest, prev = {"score": 60}, {"score": 70}
    checks = _checks(ssl_days=5)
    cl = m._build_checklist(user, target, latest, prev, {"company_name": "X"},
                            {"error": 0}, checks, top_risk=None)
    ids = {i["id"] for i in cl}
    assert "confirm_email" in ids
    assert "score_dropped" in ids               # 60 < 70-2
    assert "ssl_expiry" in ids                  # 5 <= 30
    ssl = next(i for i in cl if i["id"] == "ssl_expiry")
    assert ssl["priority"] == 1                 # <=7 dias → urgente
    assert cl == sorted(cl, key=lambda x: x["priority"])


def test_checklist_all_good():
    user = {"email_confirmed": True}
    cl = m._build_checklist(user, {"id": 1}, {"score": 95}, {"score": 95},
                            {"company_name": "X"}, {"error": 0}, _checks(), top_risk=None)
    assert cl[0]["id"] == "all_good" and cl[0]["completed"] is True


# --------------------------------------------------------------------------- #
# Endpoint
# --------------------------------------------------------------------------- #

class FakeStore:
    def __init__(self):
        self.users = {}
        self.by_id = {}
        self.next_id = 1
        self.sites = []          # list_user_sites rows
        self.target = {"id": 7, "url": "https://x.com.br", "domain": "x.com.br",
                       "sector": "hotel", "platform": "wordpress",
                       "last_scan_score": 60, "last_scan_at": datetime(2026, 7, 18, tzinfo=timezone.utc),
                       "contact_email": "dono@x.com.br"}  # NUNCA deve vazar
        self._scans = [
            {"id": 2, "score": 60, "semaphore": "amarelo", "fail_count": 2,
             "scanned_at": datetime(2026, 7, 18, tzinfo=timezone.utc)},
            {"id": 1, "score": 70, "semaphore": "amarelo", "fail_count": 1,
             "scanned_at": datetime(2026, 6, 18, tzinfo=timezone.utc)},
        ]
        self._checks = _checks(fail_ids=("check_01_https",), ssl_days=20)

    async def get_user_by_id(self, uid):
        u = self.by_id.get(int(uid))
        return {k: v for k, v in u.items() if k != "password_hash"} if u else None

    async def list_user_sites(self, uid):
        return list(self.sites)

    async def get_target(self, tid):
        return self.target if tid == 7 else None

    async def list_scans(self, target_id=None, limit=50, **kw):
        return list(self._scans)

    async def get_scan(self, sid):
        return {"id": sid, "checks_json": {"checks": self._checks,
                                           "score": {"score": 60, "semaphore": "amarelo"}}}

    async def get_site_profile(self, tid):
        return {"company_name": "Hotel X", "phone": "(11) 90000-0000", "edited_by_admin": False}

    async def sector_benchmark(self, sector, min_count=10):
        return {"sector": sector, "avg_score": 64, "count": 120, "median": 65,
                "min_score": 20, "max_score": 100, "distribution": {}}

    async def global_avg_score(self):
        return {"avg_score": 62, "count": 8000}

    async def get_sector_position(self, sector, tid):
        return {"position": 12, "total": 120}

    async def get_user_vigilias(self, uid):
        return []


@pytest.fixture
def store(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "k" * 64)
    s = FakeStore()
    u = {"id": 1, "email": "dono@x.com.br", "name": None, "plan": "free", "max_sites": 1,
         "is_active": True, "role": "owner", "email_confirmed": True}
    s.users[u["email"]] = u
    s.by_id[1] = u
    monkeypatch.setattr(m, "get_target_store", lambda: s)
    monkeypatch.setattr("discovery.store.get_target_store", lambda: s)
    monkeypatch.setattr(auth_users, "_secret", lambda: "k" * 64)

    async def _fake_sub(uid):
        return {"plan_id": "free", "plan_name": "Free", "status": "free",
                "trial_ends_at": None, "trial_days_left": None, "max_sites": 1,
                "plan": {"max_sites": 1, "scan_frequency": "monthly"}}
    monkeypatch.setattr(m.plans, "get_subscription", _fake_sub)
    return s


@pytest.fixture
def client(store):
    return TestClient(m.app, raise_server_exceptions=False)


def _bearer(uid=1):
    return {"Authorization": f"Bearer {auth_users.create_user_token({'id': uid, 'email': 'dono@x.com.br'})}"}


def test_dashboard_summary_requires_auth(client):
    assert client.get("/account/dashboard-summary").status_code == 401


def test_dashboard_summary_no_site(client, store):
    store.sites = []
    j = client.get("/account/dashboard-summary", headers=_bearer()).json()
    assert j["has_site"] is False and j["sites_count"] == 0
    assert any(i["id"] == "add_site" for i in j["checklist"])
    assert j["plan"]["name"] == "Free"


def test_dashboard_summary_with_site(client, store):
    store.sites = [{"target_id": 7, "domain": "x.com.br", "is_owner": True,
                    "last_scan_score": 60, "last_semaphore": "amarelo", "sector": "hotel"}]
    j = client.get("/account/dashboard-summary", headers=_bearer()).json()
    assert j["has_site"] is True and j["sites_count"] == 1
    assert j["site"]["domain"] == "x.com.br" and j["site"]["score"] == 60
    assert j["site"]["trend"] == "down" and j["site"]["trend_diff"] == -10   # 60 vs 70
    assert j["site"]["rank_position"] == 12 and j["site"]["rank_total"] == 120
    assert len(j["check_categories"]) == 6
    assert len(j["score_history"]) == 2 and j["score_history"][0]["score"] == 70  # ASC (antigo→novo)
    assert isinstance(j["risks"], list)
    assert j["profile"]["company_name"] == "Hotel X"
    # contact_email NUNCA no payload
    assert "dono@x.com.br" not in str(j) or "contact_email" not in str(j)
    assert "contact_email" not in str(j)


def test_dashboard_summary_checklist_has_ssl_item(client, store):
    store.sites = [{"target_id": 7, "domain": "x.com.br", "is_owner": True,
                    "last_semaphore": "amarelo"}]
    # SSL a 20 dias (do FakeStore._checks) → item de expiração presente
    j = client.get("/account/dashboard-summary", headers=_bearer()).json()
    assert any(i["id"] == "ssl_expiry" for i in j["checklist"])
