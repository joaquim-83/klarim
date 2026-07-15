"""Testes das vigílias core (KL-44 P2) — lógica dos 5 checks, dispatcher, worker
cycle (enforcement de plano + worker_control), endpoints admin/usuário (auth + IDOR)
e a renderização dos templates. Offline (sem rede, sem Postgres)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

import api.main as m
import api.vigilias as vig
from discovery import worker_control


def _now():
    return datetime.now(timezone.utc)


def _scan(scan_id, score, results):
    return {"id": scan_id, "score": score, "semaphore": "amarelo",
            "checks_json": {"results": results}, "scanned_at": _now()}


def _chk(check_id, status, details=None):
    return {"check_id": check_id, "name": check_id, "status": status,
            "severity": "ALTA", "evidence": "", "details": details or {}}


# --------------------------------------------------------------------------- #
# FakeStore
# --------------------------------------------------------------------------- #

class FakeStore:
    def __init__(self):
        self.targets = {}       # domain -> target
        self.scans = {}         # target_id -> [scan] (mais recente 1º)
        self.vigilias = {}      # id -> vigilia
        self.alerts = []        # vigilia_alert dicts
        self._vid = 1
        self._aid = 1
        self.disabled_calls = []

    # scans / targets
    async def get_target_by_domain(self, domain):
        return self.targets.get(domain.lower().strip())

    async def get_recent_scans_with_checks(self, target_id, limit=2):
        return self.scans.get(target_id, [])[:limit]

    # vigilia CRUD usado pelo worker
    async def get_due_vigilias(self, limit=100):
        return [v for v in self.vigilias.values() if v.get("enabled", True)][:limit]

    async def update_vigilia_after_check(self, vid, status, data, next_at, alerted=False):
        v = self.vigilias.get(vid)
        if v:
            v["last_status"] = status
            v["last_data"] = data
            v["next_check_at"] = next_at
            if alerted:
                v["alert_count"] = v.get("alert_count", 0) + 1

    async def create_vigilia_alert(self, vigilia_id, user_id, site_domain, tipo, severity,
                                   title, message, action_text=None, data=None):
        aid = self._aid
        self._aid += 1
        self.alerts.append({"id": aid, "vigilia_id": vigilia_id, "user_id": user_id,
                            "site_domain": site_domain, "tipo": tipo, "severity": severity,
                            "title": title, "message": message, "email_sent": False})
        return aid

    async def mark_vigilia_alert_sent(self, alert_id, email_id):
        for a in self.alerts:
            if a["id"] == alert_id:
                a["email_sent"] = True

    async def disable_user_vigilias_except(self, user_id, keep_types):
        self.disabled_calls.append((user_id, list(keep_types)))
        return 0

    async def upsert_vigilia(self, user_id, site_domain, tipo, next_check_at=None):
        vid = self._vid
        self._vid += 1
        self.vigilias[vid] = {"id": vid, "user_id": user_id, "site_domain": site_domain,
                              "tipo": tipo, "enabled": True, "next_check_at": next_check_at,
                              "last_data": {}, "alert_count": 0, "last_status": "ok"}
        return vid

    async def get_all_monitored_sites(self):
        return []

    async def ensure_schema(self):
        pass

    # admin/user list endpoints
    async def vigilia_stats(self):
        return {"total_vigilias": len(self.vigilias), "by_type": {"ssl": 1},
                "by_status": {"ok": 1}, "alerts_today": 0, "alerts_7d": 0, "alerts_30d": 0}

    async def list_vigilias(self, **kw):
        return [{"id": v["id"], "user_id": v["user_id"], "site_domain": v["site_domain"],
                 "tipo": v["tipo"], "enabled": v["enabled"], "last_status": v["last_status"],
                 "last_data": v["last_data"], "alert_count": v["alert_count"],
                 "user_email": f"u{v['user_id']}@x.com"} for v in self.vigilias.values()]

    async def get_vigilia(self, vid):
        v = self.vigilias.get(vid)
        if not v:
            return None
        return {**v, "user_email": f"u{v['user_id']}@x.com",
                "alerts": [a for a in self.alerts if a["vigilia_id"] == vid]}

    async def list_vigilia_alerts(self, **kw):
        return list(self.alerts)

    async def get_user_vigilias(self, user_id):
        return [v for v in self.vigilias.values()
                if v["user_id"] == user_id and v.get("enabled")]

    async def get_user_vigilia_alerts(self, user_id, limit=50):
        return [a for a in self.alerts if a["user_id"] == user_id][:limit]

    async def get_user_by_id(self, uid):  # usado por require_user
        return {"id": int(uid), "email": f"u{uid}@x.com", "is_active": True, "plan": "pro"}


# --------------------------------------------------------------------------- #
# A) Lógica dos 5 checks
# --------------------------------------------------------------------------- #

@pytest.fixture
def store():
    return FakeStore()


@pytest.mark.asyncio
async def test_ssl_alert_and_antispam(store):
    store.targets["x.com.br"] = {"id": 1, "domain": "x.com.br"}
    store.scans[1] = [_scan(9, 90, [_chk("check_03_ssl", "PASS",
                       {"days_left": 7, "not_after": "2026-07-25T00:00:00+00:00"})])]
    r = await vig.check_ssl(store, "x.com.br", {})
    assert r["should_alert"] and r["severity"] == "warning"
    assert "expira em 7 dias" in r["subject"]
    assert 7 in r["data"]["alerted_thresholds"]
    # anti-spam: mesmo estado não re-alerta
    r2 = await vig.check_ssl(store, "x.com.br", r["data"])
    assert r2["should_alert"] is False


@pytest.mark.asyncio
async def test_ssl_critical_1_day(store):
    store.targets["x.com.br"] = {"id": 1, "domain": "x.com.br"}
    store.scans[1] = [_scan(9, 90, [_chk("check_03_ssl", "PASS", {"days_left": 1})])]
    r = await vig.check_ssl(store, "x.com.br", {})
    assert r["should_alert"] and r["severity"] == "critical" and r["status"] == "critical"


@pytest.mark.asyncio
async def test_ssl_healthy_no_alert(store):
    store.targets["x.com.br"] = {"id": 1, "domain": "x.com.br"}
    store.scans[1] = [_scan(9, 90, [_chk("check_03_ssl", "PASS", {"days_left": 200})])]
    r = await vig.check_ssl(store, "x.com.br", {})
    assert r["should_alert"] is False and r["status"] == "ok"


@pytest.mark.asyncio
async def test_score_drop_alert_and_antispam(store):
    store.targets["x.com.br"] = {"id": 1, "domain": "x.com.br"}
    store.scans[1] = [_scan(2, 78, []), _scan(1, 85, [])]
    r = await vig.check_score_change(store, "x.com.br", {})
    assert r["should_alert"] and r["data"]["delta"] == -7 and r["severity"] == "warning"
    assert r["data"]["last_alerted_scan_id"] == 2
    r2 = await vig.check_score_change(store, "x.com.br", r["data"])
    assert r2["should_alert"] is False  # mesmo scan → não repete


@pytest.mark.asyncio
async def test_score_leaving_green_alert(store):
    store.targets["x.com.br"] = {"id": 1, "domain": "x.com.br"}
    store.scans[1] = [_scan(2, 88, []), _scan(1, 92, [])]  # queda de 4 mas sai do verde
    r = await vig.check_score_change(store, "x.com.br", {})
    assert r["should_alert"] is True


@pytest.mark.asyncio
async def test_score_first_scan_no_alert(store):
    store.targets["x.com.br"] = {"id": 1, "domain": "x.com.br"}
    store.scans[1] = [_scan(1, 85, [])]
    r = await vig.check_score_change(store, "x.com.br", {})
    assert r["should_alert"] is False and r["status"] == "ok"


@pytest.mark.asyncio
async def test_email_security_regression_alert(store):
    store.targets["x.com.br"] = {"id": 1, "domain": "x.com.br"}
    store.scans[1] = [_scan(2, 80, [_chk("check_21_spf", "FAIL")]),
                      _scan(1, 80, [_chk("check_21_spf", "PASS")])]
    r = await vig.check_email_security(store, "x.com.br", {})
    assert r["should_alert"] and "SPF" in r["data"]["changed_checks"]


@pytest.mark.asyncio
async def test_email_security_no_change(store):
    store.targets["x.com.br"] = {"id": 1, "domain": "x.com.br"}
    store.scans[1] = [_scan(2, 80, [_chk("check_21_spf", "PASS")]),
                      _scan(1, 80, [_chk("check_21_spf", "PASS")])]
    r = await vig.check_email_security(store, "x.com.br", {})
    assert r["should_alert"] is False


@pytest.mark.asyncio
async def test_reputation_alert_and_antispam(store):
    store.targets["x.com.br"] = {"id": 1, "domain": "x.com.br"}
    store.scans[1] = [_scan(9, 40, [_chk("check_29_safe_browsing", "FAIL")])]
    r = await vig.check_reputation(store, "x.com.br", {})
    assert r["should_alert"] and r["severity"] == "critical"
    r2 = await vig.check_reputation(store, "x.com.br", r["data"])
    assert r2["should_alert"] is False  # mesma blacklist → não repete


@pytest.mark.asyncio
async def test_domain_expiry_alert(store, monkeypatch):
    async def fake_rdap(domain):
        return _now() + timedelta(days=5, hours=12)  # .days == 5 → threshold 7 (critical)
    monkeypatch.setattr(vig, "_rdap_expiry", fake_rdap)
    r = await vig.check_domain_expiry(store, "x.com.br", {}, redis=None)
    assert r["should_alert"] and r["severity"] == "critical"
    assert "expira em" in r["subject"]


@pytest.mark.asyncio
async def test_domain_rdap_unavailable(store, monkeypatch):
    async def fake_rdap(domain):
        return None
    monkeypatch.setattr(vig, "_rdap_expiry", fake_rdap)
    r = await vig.check_domain_expiry(store, "x.com.br", {}, redis=None)
    assert r["status"] == "error" and r["should_alert"] is False


@pytest.mark.asyncio
async def test_dispatcher_invalid_type(store):
    r = await vig.run_vigilia_check(store, {"tipo": "banana", "site_domain": "x.com.br"})
    assert r["status"] == "error" and r["should_alert"] is False


@pytest.mark.asyncio
async def test_dispatcher_never_raises(store):
    # alvo inexistente → erro tratado, nunca levanta
    r = await vig.run_vigilia_check(store, {"tipo": "ssl", "site_domain": "naoexiste.com.br",
                                            "last_data": {}})
    assert r["status"] == "error"


# --------------------------------------------------------------------------- #
# B) Worker cycle
# --------------------------------------------------------------------------- #

def _plan(**flags):
    base = {f"vigilia_{t}": False for t in vig.VIGILIA_TYPES}
    base.update(flags)
    return {"plan": base}


@pytest.mark.asyncio
async def test_worker_cycle_creates_alert(monkeypatch):
    from discovery import vigilia_worker as vw
    store = FakeStore()
    store.targets["x.com.br"] = {"id": 1, "domain": "x.com.br"}
    store.scans[1] = [_scan(2, 70, []), _scan(1, 85, [])]  # queda 15 → alerta
    store.vigilias[10] = {"id": 10, "user_id": 5, "site_domain": "x.com.br",
                          "tipo": "score", "enabled": True, "last_data": {},
                          "user_email": "u5@x.com", "alert_count": 0, "last_status": "ok"}
    monkeypatch.setattr(vw, "get_target_store", lambda: store)
    monkeypatch.setattr(vw._plans, "get_subscription",
                        _async_ret(_plan(vigilia_score=True)))
    monkeypatch.setattr(vw.worker_control, "is_enabled", lambda w: True)
    worker = vw.VigiliaWorker()
    stats = await worker.run_cycle()
    assert stats["checked"] == 1 and stats["alerts"] == 1
    assert len(store.alerts) == 1 and store.alerts[0]["tipo"] == "score"


@pytest.mark.asyncio
async def test_worker_respects_plan_enforcement(monkeypatch):
    from discovery import vigilia_worker as vw
    store = FakeStore()
    store.targets["x.com.br"] = {"id": 1, "domain": "x.com.br"}
    store.scans[1] = [_scan(2, 70, []), _scan(1, 85, [])]
    store.vigilias[10] = {"id": 10, "user_id": 5, "site_domain": "x.com.br",
                          "tipo": "score", "enabled": True, "last_data": {},
                          "user_email": "u5@x.com", "alert_count": 0, "last_status": "ok"}
    monkeypatch.setattr(vw, "get_target_store", lambda: store)
    # plano NÃO permite score → não checa, desabilita
    monkeypatch.setattr(vw._plans, "get_subscription", _async_ret(_plan()))
    monkeypatch.setattr(vw.worker_control, "is_enabled", lambda w: True)
    worker = vw.VigiliaWorker()
    stats = await worker.run_cycle()
    assert stats["checked"] == 0 and stats["skipped_plan"] == 1 and len(store.alerts) == 0
    assert store.disabled_calls  # chamou disable_user_vigilias_except


@pytest.mark.asyncio
async def test_worker_paused_skips(monkeypatch):
    from discovery import vigilia_worker as vw
    store = FakeStore()
    monkeypatch.setattr(vw, "get_target_store", lambda: store)
    monkeypatch.setattr(vw.worker_control, "is_enabled", lambda w: False)
    worker = vw.VigiliaWorker()
    stats = await worker.run_cycle()
    assert stats.get("disabled") is True and stats["checked"] == 0


def _async_ret(value):
    async def _fn(*a, **k):
        return value
    return _fn


# --------------------------------------------------------------------------- #
# C) worker_control conhece "vigilia"
# --------------------------------------------------------------------------- #

def test_worker_control_knows_vigilia():
    assert "vigilia" in worker_control.WORKERS
    ctrl = worker_control.default_control()
    assert ctrl["vigilia"]["enabled"] is True
    # pause/resume aceitam 'vigilia' (não levanta ValueError)
    assert worker_control._targets("vigilia") == ["vigilia"]


# --------------------------------------------------------------------------- #
# D) Endpoints admin + usuário
# --------------------------------------------------------------------------- #

@pytest.fixture
def api_client(monkeypatch):
    monkeypatch.setenv("ADMIN_USER", "admin")
    monkeypatch.setenv("ADMIN_PASSWORD", "s3nha-forte")
    monkeypatch.setenv("JWT_SECRET", "x" * 64)
    store = FakeStore()
    store.vigilias[1] = {"id": 1, "user_id": 7, "site_domain": "a.com.br", "tipo": "ssl",
                         "enabled": True, "last_data": {"days_left": 5}, "alert_count": 1,
                         "last_status": "warning", "next_check_at": None}
    store.vigilias[2] = {"id": 2, "user_id": 8, "site_domain": "b.com.br", "tipo": "score",
                         "enabled": True, "last_data": {}, "alert_count": 0,
                         "last_status": "ok", "next_check_at": None}
    monkeypatch.setattr(m, "get_target_store", lambda: store)
    monkeypatch.setattr("discovery.store.get_target_store", lambda: store)
    c = TestClient(m.app, raise_server_exceptions=False)
    c._store = store
    return c


def _admin(client):
    tok = client.post("/auth/login",
                      json={"username": "admin", "password": "s3nha-forte"}).json()["token"]
    return {"Authorization": f"Bearer {tok}"}


def test_admin_vigilias_require_auth():
    assert m._is_protected("/admin/vigilias") is True
    c = TestClient(m.app, raise_server_exceptions=False)
    assert c.get("/admin/vigilias").status_code == 401
    assert c.get("/admin/vigilias/stats").status_code == 401
    assert c.get("/admin/vigilia-alerts").status_code == 401


def test_admin_vigilias_list(api_client):
    r = api_client.get("/admin/vigilias", headers=_admin(api_client))
    assert r.status_code == 200
    doms = {v["site_domain"] for v in r.json()["vigilias"]}
    assert doms == {"a.com.br", "b.com.br"}


def test_admin_vigilia_stats(api_client):
    r = api_client.get("/admin/vigilias/stats", headers=_admin(api_client))
    assert r.status_code == 200 and "by_status" in r.json()


def test_admin_vigilia_detail_and_404(api_client):
    assert api_client.get("/admin/vigilias/1", headers=_admin(api_client)).status_code == 200
    assert api_client.get("/admin/vigilias/999", headers=_admin(api_client)).status_code == 404


def test_user_vigilias_idor(api_client, monkeypatch):
    # usuário 7 só vê a própria vigília (a.com.br), nunca a do usuário 8
    import api.auth_users as au
    monkeypatch.setattr(au, "_secret", lambda: "x" * 64)
    tok = au.create_user_token({"id": 7, "email": "u7@x.com"})
    r = api_client.get("/account/vigilias", headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 200
    doms = {v["site_domain"] for v in r.json()["vigilias"]}
    assert doms == {"a.com.br"}  # NÃO inclui b.com.br (user 8)


# --------------------------------------------------------------------------- #
# E) Mailer render (todos os templates via send_vigilia_alert)
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_send_vigilia_alert_renders_all(monkeypatch):
    from notifier.email_client import KlarimMailer
    mailer = KlarimMailer("re_test", "Klarim <x@klarim.net>", store=None)
    sent = {}

    async def fake_send(params, **kw):
        sent["subject"] = params["subject"]
        sent["html"] = params["html"]
        sent["email_type"] = kw.get("email_type")
        return {"email_id": "e1"}

    monkeypatch.setattr(mailer, "_send", fake_send)
    for tipo, data in [("ssl", {"days_left": 7, "expiry_date": "2026-07-25"}),
                       ("domain", {"days_left": 14, "expiry_date": "2026-08-01"}),
                       ("score", {"previous_score": 85, "current_score": 78, "delta": -7}),
                       ("email", {"changed_checks": ["SPF"]}),
                       ("reputation", {"blacklisted": ["Google Safe Browsing"]})]:
        await mailer.send_vigilia_alert(
            to_email="dono@x.com", tipo=tipo, domain="x.com.br",
            subject="assunto", title="titulo", message="mensagem",
            action_text="faça X", severity="warning", data=data)
        assert "{{" not in sent["html"] and sent["email_type"] == f"vigilia_{tipo}"
        assert "x.com.br" in sent["html"]
