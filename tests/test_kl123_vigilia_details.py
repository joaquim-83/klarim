"""KL-123 — detalhe expansível das vigílias no dashboard do dono.

Cobre os endpoints (`GET .../vigilias/{tipo}/details`, `POST .../dismiss/{alert_id}`,
`POST .../acknowledge`) — ownership/nível, tipo inválido, degradação graciosa sem dados —
e as funções PURAS de `api.vigilia_details`. Offline (FakeStore); o SQL é validado na VM.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import api.main as m
from api import auth_users
from api import vigilia_details as vd


# --------------------------------------------------------------------------- #
# FakeStore
# --------------------------------------------------------------------------- #

def _ssl_scan(days_left=87, cid="check_03_ssl"):
    return {"id": 100, "score": 90, "semaphore": "verde", "scanned_at": None,
            "checks_json": {"results": [
                {"check_id": cid, "name": "Certificado SSL válido", "status": "PASS",
                 "severity": "CRITICA",
                 "details": {"subject_cn": "site.com.br", "not_before": "2026-04-10T00:00:00+00:00",
                             "not_after": "2026-10-21T00:00:00+00:00", "days_left": days_left}},
            ]}}


class FakeStore:
    def __init__(self):
        self.users, self.links, self.targets = {}, {}, {}
        self.vigilias = {}          # (uid, domain, tipo) -> {last_status, last_data}
        self.typosquats = {}        # id -> row
        self.vig_alerts = []        # list of alert rows
        self.scans = {}             # target_id -> list of scans (recent first)
        self.profiles = {}

    async def get_user_by_id(self, uid):
        return self.users.get(int(uid))

    async def get_user_site(self, uid, tid):
        return self.links.get((int(uid), int(tid)))

    async def get_target(self, tid):
        return self.targets.get(int(tid))

    async def list_site_vigilias(self, uid, domain):
        out = []
        for (u, d, tipo), v in self.vigilias.items():
            if u == int(uid) and d == domain:
                out.append({"tipo": tipo, "enabled": True,
                            "last_status": v.get("last_status", "ok"),
                            "last_check_at": None, "next_check_at": None,
                            "last_data": v.get("last_data") or {}})
        return out

    async def get_site_vigilia_alerts(self, uid, domain, tipo, limit=10):
        return [a for a in self.vig_alerts
                if a["user_id"] == int(uid) and a["site_domain"] == domain
                and a["tipo"] == tipo][:limit]

    async def get_site_typosquat_alerts(self, target_id, user_id, limit=50):
        return [dict(r) for r in self.typosquats.values()
                if r["target_id"] == int(target_id) and r["user_id"] == int(user_id)][:limit]

    async def dismiss_typosquat_alert(self, alert_id, target_id, user_id):
        r = self.typosquats.get(int(alert_id))
        if r and r["target_id"] == int(target_id) and r["user_id"] == int(user_id):
            r["dismissed"] = True
            return True
        return False

    async def acknowledge_vigilia(self, uid, domain, tipo, ack):
        v = self.vigilias.get((int(uid), domain, tipo))
        if not v:
            return False
        v.setdefault("last_data", {})["acknowledged_at"] = ack
        return True

    async def get_recent_scans_with_checks(self, target_id, limit=2):
        return (self.scans.get(int(target_id)) or [])[:limit]

    async def get_site_profile(self, tid):
        return self.profiles.get(int(tid))


@pytest.fixture
def store():
    s = FakeStore()
    s.users[10] = {"id": 10, "email": "dono@site.com.br", "account_level": 2, "is_active": True}
    s.users[20] = {"id": 20, "email": "outro@x.com.br", "account_level": 2, "is_active": True}
    s.targets[1] = {"id": 1, "domain": "site.com.br", "url": "https://site.com.br",
                    "last_scan_score": 90}
    s.targets[2] = {"id": 2, "domain": "outro.com.br", "url": "https://outro.com.br"}
    s.links[(10, 1)] = {"id": 1, "user_id": 10, "target_id": 1, "is_owner": True}
    s.links[(20, 2)] = {"id": 2, "user_id": 20, "target_id": 2, "is_owner": True}
    # vigílias do site 1
    s.vigilias[(10, "site.com.br", "ssl")] = {
        "last_status": "ok", "last_data": {"days_left": 87, "expiry_date": "2026-10-21T00:00:00+00:00"}}
    s.vigilias[(10, "site.com.br", "phishing")] = {"last_status": "critical", "last_data": {}}
    s.vigilias[(10, "site.com.br", "reputation")] = {"last_status": "ok", "last_data": {"blacklisted": []}}
    s.vigilias[(10, "site.com.br", "score")] = {"last_status": "warning", "last_data": {}}
    s.profiles[1] = {"target_id": 1, "certificate_authority": "Let's Encrypt"}
    s.scans[1] = [_ssl_scan(87)]
    s.typosquats[42] = {"id": 42, "target_id": 1, "user_id": 10, "suspicious_domain": "lrim.com.br",
                        "similarity_type": "levenshtein", "distance": 2, "detected_at": None,
                        "notified": True, "dismissed": False}
    s.typosquats[43] = {"id": 43, "target_id": 1, "user_id": 10, "suspicious_domain": "larim.net",
                        "similarity_type": "tld_variant", "distance": None, "detected_at": None,
                        "notified": True, "dismissed": False}
    return s


@pytest.fixture
def client(store, monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "x" * 64)
    monkeypatch.setattr(m, "get_target_store", lambda: store)
    monkeypatch.setattr("discovery.store.get_target_store", lambda: store)
    return TestClient(m.app, raise_server_exceptions=False)


def _hdr(store, uid):
    return {"Authorization": f"Bearer {auth_users.create_user_token(store.users[uid])}"}


# --------------------------- endpoints: GET details ----------------------- #

def test_phishing_details_lists_alerts_and_pending(client, store):
    r = client.get("/account/sites/1/vigilias/phishing/details", headers=_hdr(store, 10))
    assert r.status_code == 200
    d = r.json()
    assert d["tipo"] == "phishing" and d["pending_count"] == 2
    domains = {a["suspicious_domain"] for a in d["data"]["alerts"]}
    assert domains == {"lrim.com.br", "larim.net"}
    assert d["status"] == "critical" and d["guidance"]


def test_ssl_details_has_days_issuer_guidance(client, store):
    r = client.get("/account/sites/1/vigilias/ssl/details", headers=_hdr(store, 10))
    assert r.status_code == 200
    d = r.json()
    assert d["data"]["days_left"] == 87
    assert d["data"]["issuer"] == "Let's Encrypt"
    assert d["data"]["subject_cn"] == "site.com.br"
    assert d["status"] == "ok" and "válido" in d["guidance"].lower()


def test_score_details_delta_and_checks_changed(client, store):
    # 2 scans: o mais recente perdeu o CSP (PASS→FAIL) → score caiu
    newer = {"id": 2, "score": 70, "semaphore": "amarelo", "scanned_at": None,
             "checks_json": {"results": [
                 {"check_id": "check_32_csp", "name": "Content-Security-Policy",
                  "status": "FAIL", "severity": "ALTA"}]}}
    older = {"id": 1, "score": 80, "semaphore": "amarelo", "scanned_at": None,
             "checks_json": {"results": [
                 {"check_id": "check_32_csp", "name": "Content-Security-Policy",
                  "status": "PASS", "severity": "ALTA"}]}}
    store.scans[1] = [newer, older]
    r = client.get("/account/sites/1/vigilias/score/details", headers=_hdr(store, 10))
    assert r.status_code == 200
    d = r.json()
    assert d["data"]["current_score"] == 70 and d["data"]["previous_score"] == 80
    assert d["data"]["delta"] == -10
    changed = d["data"]["checks_changed"]
    assert len(changed) == 1 and changed[0]["name"] == "Content-Security-Policy"
    assert changed[0]["from"] == "PASS" and changed[0]["to"] == "FAIL"


def test_details_graceful_without_last_data(client, store):
    # vigília domain sem last_data → payload "unknown", sem crash
    store.vigilias[(10, "site.com.br", "domain")] = {"last_status": "ok", "last_data": {}}
    r = client.get("/account/sites/1/vigilias/domain/details", headers=_hdr(store, 10))
    assert r.status_code == 200
    d = r.json()
    assert d["status"] == "unknown" and d["pending_count"] == 0


def test_details_invalid_tipo_404(client, store):
    r = client.get("/account/sites/1/vigilias/bogus/details", headers=_hdr(store, 10))
    assert r.status_code == 404


def test_details_other_user_404(client, store):
    # user 20 não tem vínculo com o site 1 → 404 (nunca vaza)
    r = client.get("/account/sites/1/vigilias/phishing/details", headers=_hdr(store, 20))
    assert r.status_code == 404


def test_details_requires_auth(client):
    assert client.get("/account/sites/1/vigilias/ssl/details").status_code == 401


# --------------------------- endpoints: ações ----------------------------- #

def test_dismiss_typosquat_marks_dismissed(client, store):
    r = client.post("/account/sites/1/vigilias/phishing/dismiss/42", headers=_hdr(store, 10))
    assert r.status_code == 200 and r.json()["dismissed"] is True
    assert store.typosquats[42]["dismissed"] is True
    # pending cai para 1
    d = client.get("/account/sites/1/vigilias/phishing/details", headers=_hdr(store, 10)).json()
    assert d["pending_count"] == 1


def test_dismiss_other_user_404(client, store):
    # o alerta 42 é do user 10; o user 20 não pode descartá-lo
    r = client.post("/account/sites/1/vigilias/phishing/dismiss/42", headers=_hdr(store, 20))
    assert r.status_code == 404
    assert store.typosquats[42]["dismissed"] is False


def test_dismiss_unknown_alert_404(client, store):
    r = client.post("/account/sites/1/vigilias/phishing/dismiss/999", headers=_hdr(store, 10))
    assert r.status_code == 404


def test_acknowledge_sets_flag(client, store):
    r = client.post("/account/sites/1/vigilias/phishing/acknowledge", headers=_hdr(store, 10))
    assert r.status_code == 200 and r.json()["acknowledged"] is True
    assert store.vigilias[(10, "site.com.br", "phishing")]["last_data"].get("acknowledged_at")


def test_acknowledge_invalid_tipo_404(client, store):
    assert client.post("/account/sites/1/vigilias/bogus/acknowledge",
                       headers=_hdr(store, 10)).status_code == 404


# --------------------------- funções PURAS -------------------------------- #

def test_build_phishing_pending_count():
    alerts = [{"id": 1, "suspicious_domain": "a.com", "similarity_type": "levenshtein",
               "distance": 2, "dismissed": False},
              {"id": 2, "suspicious_domain": "b.com", "similarity_type": "homoglyph",
               "distance": 1, "dismissed": True}]
    out = vd.build_phishing(alerts, "site.com.br")
    assert out["pending_count"] == 1 and out["status"] == "critical"
    # rótulo acessível da similaridade
    assert out["data"]["alerts"][0]["similarity_label"] == "letras trocadas"


def test_build_phishing_empty_is_ok():
    out = vd.build_phishing([], "site.com.br")
    assert out["status"] == "ok" and out["pending_count"] == 0


def test_build_ssl_urgency_bands():
    # status espelha o worker de vigília (crítico só <=1 dia; assim o detalhe não "vira
    # vermelho" ao expandir um card que o worker marcou amarelo).
    assert vd.build_ssl({"days_left": 1}, None, None, "x")["status"] == "critical"
    assert vd.build_ssl({"days_left": -2}, None, None, "x")["status"] == "critical"
    assert vd.build_ssl({"days_left": 3}, None, None, "x")["status"] == "warning"
    assert vd.build_ssl({"days_left": 20}, None, None, "x")["status"] == "warning"
    assert vd.build_ssl({"days_left": 200}, None, None, "x")["status"] == "ok"
    assert vd.build_ssl({}, None, None, "x")["status"] == "unknown"
    # a orientação é mais enfática que o semáforo quando resta pouco tempo
    assert "urgência" in vd.build_ssl({"days_left": 3}, None, None, "x")["summary"].lower()


def test_build_domain_bands():
    assert vd.build_domain({"days_left": 5}, "x")["status"] == "critical"
    assert vd.build_domain({"days_left": 40}, "x")["status"] == "warning"
    assert vd.build_domain({"days_left": 300}, "x")["status"] == "ok"


def test_build_reputation_listed_vs_clean():
    listed = vd.build_reputation({"blacklisted": ["Google Safe Browsing"]}, [], "x")
    assert listed["status"] == "critical" and listed["pending_count"] == 1
    clean = vd.build_reputation({"blacklisted": []}, [], "x")
    assert clean["status"] == "ok" and clean["pending_count"] == 0


def test_build_uptime_online_offline():
    on = vd.build_uptime({"consecutive_failures": 0, "last_response_code": 200,
                          "last_response_time_ms": 300}, "x")
    assert on["status"] == "ok" and on["data"]["status"] == "online"
    off = vd.build_uptime({"consecutive_failures": 3, "last_response_code": 0}, "x")
    assert off["status"] == "critical" and off["data"]["status"] == "offline"


def test_build_email_missing_dmarc_warns():
    checks = [{"check_id": "check_21_spf", "status": "PASS"},
              {"check_id": "check_23_dmarc", "status": "FAIL"}]
    out = vd.build_email(checks, "site.com.br")
    assert out["status"] == "warning"
    assert out["data"]["spf"]["status"] == "pass" and out["data"]["dmarc"]["status"] == "absent"


def test_acknowledge_clears_pending_for_ssl_warning():
    warn = vd.build_ssl({"days_left": 20}, None, None, "x")
    assert warn["pending_count"] == 1
    acked = vd.build_ssl({"days_left": 20, "acknowledged_at": "2026-07-28T00:00:00+00:00"}, None, None, "x")
    assert acked["pending_count"] == 0
