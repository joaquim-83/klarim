"""KL-90 — Dashboard v2 (`GET /account/dashboard-summary`). Offline (TestClient + FakeStore).

Cobre:
  * funções puras de `api/dashboard.py` (categorias, riscos, trend, checklist, plano,
    monitoramento, benchmark, seleção de site);
  * endpoint: com site (shape v2 completa), sem site (reduzido), `?site_id=`, 404,
    401, ordenação de riscos por severidade, 6 categorias e performance;
  * `contact_email` NUNCA no payload.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

import api.main as m
import api.dashboard as dv
import api.plans as plans_mod
from api import auth_users

UTC = timezone.utc


# --------------------------------------------------------------------------- #
# Fixtures de dados
# --------------------------------------------------------------------------- #

def _mk_checks(fails: dict) -> list:
    """48 checks (a partir de `api.main.CHECK_META`); `fails` = {full_check_id: severity}.
    Todo o resto PASS. Um check de cert com evidência de dias é incluído p/ o teste de SSL."""
    out = []
    for meta in m.CHECK_META:
        cid, name = meta["check_id"], meta["name"]
        if cid in fails:
            st, sev, ev = "FAIL", fails[cid], f"{name}: ausente."
        else:
            st, sev, ev = "PASS", "BAIXA", ""
        if cid == "check_42_cert_chain":
            ev = "Certificado válido até 2026-10-05 (200 dias). Cadeia completa."
        out.append({"check_id": cid, "name": name, "status": st, "severity": sev, "evidence": ev})
    return out


class FakeStore:
    def __init__(self):
        self.users = {}
        self.by_id = {}
        self.sites = [
            {"target_id": 7, "domain": "hotel.com.br", "is_owner": True,
             "last_scan_score": 83, "last_semaphore": "amarelo", "sector": "hotelaria"},
            {"target_id": 8, "domain": "loja.com.br", "is_owner": True,
             "last_scan_score": 42, "last_semaphore": "vermelho", "sector": "ecommerce"},
        ]
        self.targets = {
            7: {"id": 7, "domain": "hotel.com.br", "sector": "hotelaria", "platform": "wordpress",
                "site_type": "institucional", "last_scan_score": 83,
                "last_scan_at": datetime(2026, 7, 21, 10, tzinfo=UTC),
                "contact_email": "dono@hotel.com.br"},  # NUNCA deve vazar
            8: {"id": 8, "domain": "loja.com.br", "sector": "ecommerce", "platform": "unknown",
                "site_type": "ecommerce", "last_scan_score": 42,
                "last_scan_at": datetime(2026, 7, 20, 10, tzinfo=UTC),
                "contact_email": "dono@loja.com.br"},
        }
        # histórico (mais novo -> mais antigo)
        self.scans = {
            7: [{"id": 72, "score": 83, "semaphore": "amarelo",
                 "scanned_at": datetime(2026, 7, 21, tzinfo=UTC)},
                {"id": 71, "score": 78, "semaphore": "amarelo",
                 "scanned_at": datetime(2026, 7, 14, tzinfo=UTC)}],
            8: [{"id": 82, "score": 42, "semaphore": "vermelho",
                 "scanned_at": datetime(2026, 7, 20, tzinfo=UTC)}],
        }
        self.checks = {
            7: _mk_checks({"check_05_csp": "ALTA", "check_02_hsts": "MEDIA",
                           "check_10_sensitive": "CRITICA"}),
            8: _mk_checks({"check_21_spf": "ALTA", "check_05_csp": "ALTA"}),
        }
        self.technician = None

    async def get_user_by_id(self, uid):
        u = self.by_id.get(int(uid))
        return {k: v for k, v in u.items() if k != "password_hash"} if u else None

    async def list_user_sites(self, uid):
        return list(self.sites)

    async def get_target(self, tid):
        return self.targets.get(tid)

    async def list_scans(self, target_id=None, limit=50, **kw):
        return list(self.scans.get(target_id, []))

    async def get_scan(self, sid):
        tid = 7 if sid in (71, 72) else 8
        return {"id": sid, "checks_json": {"checks": self.checks[tid],
                                           "score": {"score": 83}, "status": "ok"}}

    async def get_site_profile(self, tid):
        return {"company_name": "Hotel X", "phone": "(41) 3333-4444", "edited_by_admin": True}

    async def get_user_vigilias(self, uid):
        return [{"tipo": "ssl", "site_domain": "hotel.com.br", "enabled": True,
                 "last_status": "ok", "last_data": {"ssl_days_remaining": 247}},
                {"tipo": "score", "site_domain": "hotel.com.br", "enabled": True,
                 "last_status": "ok", "last_data": {}}]

    async def get_active_technician_for_target(self, uid, tid):
        return self.technician

    async def get_technician_clients(self, uid):
        return list(getattr(self, "_tech_clients", []))

    async def sector_benchmark(self, sector, min_count=10):
        return {"sector": sector, "avg_score": 72, "count": 120}

    async def get_sector_position(self, sector, tid):
        return {"position": 89, "total": 1827}

    async def global_avg_score(self):
        return {"avg_score": 62, "count": 8000}


@pytest.fixture
def store(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "k" * 64)
    s = FakeStore()
    u = {"id": 1, "email": "dono@x.com.br", "name": "João", "plan": "pro", "max_sites": 5,
         "is_active": True, "role": "owner", "email_confirmed": True}
    s.users[u["email"]] = u
    s.by_id[1] = u
    monkeypatch.setattr(m, "get_target_store", lambda: s)
    monkeypatch.setattr("discovery.store.get_target_store", lambda: s)
    monkeypatch.setattr(auth_users, "_secret", lambda: "k" * 64)

    async def _fake_sub(uid):
        return {"plan_id": "pro", "plan_name": "Pro", "status": "trial",
                "trial_ends_at": datetime(2026, 8, 14, tzinfo=UTC), "trial_days_left": 24,
                "max_sites": 5, "plan": {"max_sites": 5, "scan_frequency": "weekly",
                                         "bulletin_frequency": "weekly"}}
    monkeypatch.setattr(plans_mod, "get_subscription", _fake_sub)
    return s


@pytest.fixture
def client(store):
    return TestClient(m.app, raise_server_exceptions=False)


def _bearer(uid=1):
    return {"Authorization": f"Bearer {auth_users.create_user_token({'id': uid, 'email': 'dono@x.com.br'})}"}


# --------------------------------------------------------------------------- #
# Funções puras
# --------------------------------------------------------------------------- #

def test_check_num_and_short_id():
    assert dv.check_num("check_05_csp") == 5
    assert dv.short_id("check_05_csp") == "check_05"
    assert dv.short_id("check_21") == "check_21"


def test_norm_status_and_severity():
    assert dv.norm_status("PASS") == "pass" and dv.norm_status("FAIL") == "fail"
    assert dv.norm_status("INCONCLUSO") == "inconclusive"
    assert dv.norm_severity("CRITICA") == "critica"


def test_build_categories_six_and_status():
    cats = dv.build_categories(_mk_checks({"check_10_sensitive": "CRITICA",
                                           "check_11_dirlist": "ALTA", "check_09_sourcemaps": "MEDIA"}))
    assert [c["slug"] for c in cats] == ["tls", "headers", "supply", "dns", "content", "osint"]
    supply = next(c for c in cats if c["slug"] == "supply")
    assert supply["status"] == "critical"   # 3 FAILs no supply chain
    assert supply["passed"] + 3 == supply["total"]
    tls = next(c for c in cats if c["slug"] == "tls")
    assert tls["status"] == "ok"            # sem FAIL


def test_build_categories_check_fields():
    cats = dv.build_categories(_mk_checks({"check_05_csp": "ALTA"}))
    headers = next(c for c in cats if c["slug"] == "headers")
    csp = next(c for c in headers["checks"] if c["id"] == "check_05")
    assert csp["status"] == "fail" and csp["severity"] == "alta"
    assert csp["risk_message"] and csp["fix_inline"]["nginx"]
    # um PASS não carrega risk_message nem fix_inline
    passed = next(c for c in headers["checks"] if c["status"] == "pass")
    assert passed["risk_message"] is None and passed["fix_inline"] is None


def test_build_risks_sorted_by_severity():
    risks = dv.build_risks(_mk_checks({"check_02_hsts": "MEDIA", "check_10_sensitive": "CRITICA",
                                       "check_05_csp": "ALTA"}))
    assert [r["severity"] for r in risks] == ["critica", "alta", "media"]
    assert risks[0]["check_id"] == "check_10"
    assert set(risks[0]["fix_inline"]) == {"wordpress", "nginx", "apache"}
    assert risks[0]["title"] and risks[0]["description"]


def test_build_trend():
    assert dv.build_trend([{"score": 70}, {"score": 83}]) == ("subindo", 13)
    assert dv.build_trend([{"score": 60}, {"score": 42}]) == ("caindo", -18)
    assert dv.build_trend([{"score": 83}, {"score": 83}]) == ("estavel", 0)
    assert dv.build_trend([{"score": 83}]) == ("primeiro", 0)


def test_build_score_history_ascending():
    scans = [{"score": 83, "scanned_at": datetime(2026, 7, 21, tzinfo=UTC)},
             {"score": 78, "scanned_at": datetime(2026, 7, 14, tzinfo=UTC)}]
    h = dv.build_score_history(scans)
    assert [p["score"] for p in h] == [78, 83]           # antigo -> novo
    assert h[0]["date"] == "2026-07-14"


def test_build_checklist_priorities():
    checks = _mk_checks({"check_10_sensitive": "CRITICA", "check_05_csp": "ALTA",
                         "check_02_hsts": "MEDIA"})
    cl = dv.build_checklist(checks, {"company_name": "X"}, 83, {"email_confirmed": True}, False)
    ids = [i["id"] for i in cl]
    assert ids[0] == "fix_check_10"          # crítica antes de alta
    assert "fix_check_05" in ids
    assert "fix_check_02" not in ids          # média não entra
    assert "share_score" in ids               # score >= 80
    assert "activate_seal" in ids and len(cl) <= 5


def test_build_plan():
    p = dv.build_plan({"plan_id": "pro", "plan_name": "Pro", "status": "trial",
                       "trial_ends_at": datetime(2026, 8, 14, tzinfo=UTC), "trial_days_left": 24})
    assert p["name"] == "Pro" and p["days_remaining"] == 24
    assert "48 checks" in p["features"]


def test_build_monitoring():
    vig = [{"tipo": "ssl", "site_domain": "x.com.br", "enabled": True, "last_status": "ok"},
           {"tipo": "score", "site_domain": "x.com.br", "enabled": True, "last_status": "critical"}]
    mon = dv.build_monitoring(vig, "x.com.br", {"plan": {"bulletin_frequency": "monthly"}}, False, True)
    assert mon["vigilias_active"] == 2 and mon["vigilias_ok"] == 1 and mon["vigilias_critical"] == 1
    assert mon["bulletin_frequency"] == "mensal" and mon["technician_linked"] is True


def test_build_benchmark_above_average_and_fallback():
    b = dv.build_benchmark("hotelaria", 83, {"avg_score": 72}, {"position": 89, "total": 1827}, None)
    assert b["above_average"] is True and b["rank_position"] == 89 and b["sector_avg"] == 72
    # sem benchmark setorial → cai na média global
    b2 = dv.build_benchmark("outro", 50, None, None, {"avg_score": 60})
    assert b2["sector_avg"] == 60 and b2["above_average"] is False


def test_pick_site_and_404():
    sites = [{"target_id": 7}, {"target_id": 8}]
    assert dv._pick_site(sites, None)["target_id"] == 7      # primário
    assert dv._pick_site(sites, 8)["target_id"] == 8
    with pytest.raises(Exception):
        dv._pick_site(sites, 999)


# --------------------------------------------------------------------------- #
# Endpoint
# --------------------------------------------------------------------------- #

def test_requires_auth(client):
    assert client.get("/account/dashboard-summary").status_code == 401


def test_no_site(client, store):
    store.sites = []
    j = client.get("/account/dashboard-summary", headers=_bearer()).json()
    assert j["has_site"] is False and j["sites"] == [] and j["selected_site_id"] is None
    assert any(i["id"] == "add_site" for i in j["checklist"])
    assert j["plan"]["name"] == "Pro"       # plano vem da assinatura mesmo sem site


def test_with_site_full_shape(client):
    j = client.get("/account/dashboard-summary", headers=_bearer()).json()
    assert j["has_site"] is True
    assert j["selected_site_id"] == 7 and len(j["sites"]) == 2
    assert j["site"]["domain"] == "hotel.com.br" and j["site"]["score"] == 83
    assert j["site"]["site_type"] == "wordpress" and j["site"]["ssl_days_remaining"] == 247
    assert j["site"]["is_online"] is True
    assert j["site"]["trend"] == "subindo" and j["site"]["trend_delta"] == 5   # 78 -> 83
    assert len(j["categories"]) == 6
    assert j["benchmark"]["rank_position"] == 89 and j["benchmark"]["above_average"] is True
    assert len(j["score_history"]) == 2 and j["score_history"][0]["score"] == 78
    assert j["plan"]["name"] == "Pro" and "48 checks" in j["plan"]["features"]
    assert j["monitoring"]["vigilias_active"] == 2 and j["monitoring"]["bulletin_frequency"] == "semanal"
    assert j["profile"]["company_name"] == "Hotel X" and j["profile"]["confirmed"] is True
    # contact_email NUNCA no payload
    assert "contact_email" not in str(j) and "dono@hotel.com.br" not in str(j)


def test_risks_sorted_by_severity(client):
    j = client.get("/account/dashboard-summary", headers=_bearer()).json()
    sev = [r["severity"] for r in j["risks"]]
    assert sev == sorted(sev, key=lambda s: {"critica": 0, "alta": 1, "media": 2, "baixa": 3}[s])
    assert j["risks"][0]["severity"] == "critica"          # check_10_sensitive
    assert j["risks"][0]["fix_inline"]["wordpress"]


def test_categories_counts(client):
    j = client.get("/account/dashboard-summary", headers=_bearer()).json()
    cats = {c["slug"]: c for c in j["categories"]}
    # check_10_sensitive (supply) é FAIL crítica
    assert cats["supply"]["status"] in ("warning", "critical")
    for c in j["categories"]:
        assert 0 <= c["passed"] <= c["total"] and c["status"] in ("ok", "warning", "critical")


def test_site_id_selection(client):
    j = client.get("/account/dashboard-summary?site_id=8", headers=_bearer()).json()
    assert j["selected_site_id"] == 8 and j["site"]["domain"] == "loja.com.br"
    assert j["site"]["score"] == 42
    # loja tem SPF FAIL → risco de e-mail presente
    assert any(r["check_id"] == "check_21" for r in j["risks"])


def test_site_id_invalid_404(client):
    assert client.get("/account/dashboard-summary?site_id=999999", headers=_bearer()).status_code == 404


def test_performance_under_1s(client):
    t0 = time.perf_counter()
    r = client.get("/account/dashboard-summary", headers=_bearer())
    assert r.status_code == 200 and (time.perf_counter() - t0) < 1.0


# --------------------------------------------------------------------------- #
# Modo técnico (KL-90)
# --------------------------------------------------------------------------- #

def test_technician_mode(client, store):
    """Técnico vê o dashboard TÉCNICO de um site de cliente vinculado: technician_mode,
    dono mascarado, checks com evidência, SEM plano/checklist/conta do dono."""
    store.sites = []  # o técnico não tem site próprio
    store._tech_clients = [{
        "link_id": 1, "target_id": 7, "status": "active", "owner_user_id": 99,
        "owner_email": "dono@cliente.com.br", "domain": "hotel.com.br",
        "last_scan_score": 83, "receive_alerts": True,
    }]
    j = client.get("/account/dashboard-summary?site_id=7", headers=_bearer()).json()
    assert j["technician_mode"] is True
    assert j["owner_email"] == "d***o@cliente.com.br"        # SEMPRE mascarado
    assert j["can_receive_alerts"] is True
    assert "plan" not in j and "checklist" not in j          # nunca a conta do dono
    assert j["site"]["domain"] == "hotel.com.br" and j["site"]["score"] == 83
    assert len(j["categories"]) == 6
    # a evidência técnica está presente nos checks FAIL
    fails = [c for cat in j["categories"] for c in cat["checks"] if c["status"] == "fail"]
    assert any(c.get("evidence") for c in fails)


def test_technician_mode_unlinked_404(client, store):
    """Técnico NÃO pode ver um site que não é vinculado a ele (segurança)."""
    store.sites = []
    store._tech_clients = []   # nenhum vínculo
    assert client.get("/account/dashboard-summary?site_id=7", headers=_bearer()).status_code == 404
