"""KL-152 P3 — avaliação de fornecedores (Enterprise): CRUD, gate Enterprise, redação, status,
notificação opt-in, monitoramento e PDF comparativo. Offline (FakeStore + engine/redis mockados)."""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

import api.main as m
from api import gate as g
from discovery.vendor_monitor_worker import VendorMonitorWorker
from security_gate.models import GateReport, Result, Severity, Status
from security_gate.vendor import calculate_vendor_status, build_vendor_scan_payload

SECRET = "k" * 64


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _now():
    return datetime.now(timezone.utc)


_ENT = {"id": 4, "name": "Enterprise", "slug": "enterprise", "scans_per_day": -1, "max_domains": -1,
        "checks_allowed": ["all"], "scan_third_party": True}
_FREE = {"id": 1, "name": "Free", "slug": "free", "scans_per_day": 5, "max_domains": 1,
         "checks_allowed": ["headers", "ssl"], "scan_third_party": False}


def _report():
    """3 findings sensíveis (2 críticos: credencial + .env; 1 alto: cors) + 1 pass."""
    rs = [
        Result("cred_generic", "credentials", "/app.js", Status.FAIL, Severity.CRITICAL, "secret ABC123XYZ"),
        Result("env_exposed", "exposure", "/.env", Status.FAIL, Severity.CRITICAL, "arquivo .env acessível"),
        Result("cors_reflect", "cors", "/", Status.FAIL, Severity.HIGH, "reflete origin"),
        Result("ssl_valid", "ssl", "/", Status.PASS, Severity.MEDIUM, "ok"),
    ]
    return GateReport(url="https://vendor.com.br", results=rs, duration_ms=1200)


class FakeMailer:
    def __init__(self):
        self.sent = []

    async def send_vendor_assessment(self, to_email, enterprise_name, vendor_url, vendor_domain, score):
        self.sent.append(("assessment", to_email, vendor_domain, score))
        return {"id": "e1"}

    async def send_vendor_score_drop(self, to_email, vendor_name, vendor_domain, score, threshold, previous):
        self.sent.append(("drop", to_email, vendor_domain, score))
        return {"id": "e2"}


class FakeRedis:
    def __init__(self):
        self.d = {}

    async def set(self, k, v, ex=None, nx=False):
        if nx and k in self.d:
            return None
        self.d[k] = v
        return True

    async def get(self, k):
        return self.d.get(k)


class FakeStore:
    def __init__(self, plan=_ENT):
        self.plan = plan
        self.keys = []
        self.audits = []
        self.vendors = {}
        self.vid = 1
        self.scans = {}
        self.sid = 100
        self.contact = {"vendor.com.br": "dono@vendor.com.br"}
        self.users = {10: {"id": 10, "email": "ent@acme.com", "is_active": True, "account_level": 2,
                           "account_type": "developer", "name": "ACME Ltda", "company_cnpj": "12.345.678/0001-90",
                           "gate_plan_id": plan["id"], "gate_trial_ends_at": None}}

    # auth / plan
    async def get_gate_api_key_by_hash(self, h):
        return next((dict(k) for k in self.keys if k["key_hash"] == h), None)

    async def touch_gate_api_key(self, kid):
        pass

    async def get_account_gate_fields(self, aid):
        u = self.users.get(int(aid))
        return None if not u else {"id": aid, "gate_plan_id": u["gate_plan_id"],
                                   "gate_trial_ends_at": u.get("gate_trial_ends_at"),
                                   "account_type": u["account_type"]}

    async def get_gate_plan(self, pid):
        return dict(self.plan) if pid == self.plan["id"] else None

    async def get_gate_plan_by_slug(self, slug):
        return dict(self.plan) if self.plan["slug"] == slug else (dict(_FREE) if slug == "free" else None)

    async def list_gate_api_keys(self, aid):
        return [dict(k) for k in self.keys if k["account_id"] == aid]

    async def insert_gate_audit(self, account_id, action, key_id=None, target_domain=None,
                                detail=None, ip_address=None, user_agent=None):
        self.audits.append({"account_id": account_id, "action": action, "detail": detail or {}})

    async def get_user_by_id(self, uid):
        return self.users.get(int(uid))

    async def get_enterprise_profile(self, aid):
        u = self.users.get(int(aid))
        return None if not u else {"id": u["id"], "email": u["email"], "name": u["name"],
                                   "company_cnpj": u["company_cnpj"]}

    async def get_contact_email_for_domain(self, domain):
        return self.contact.get((domain or "").lower())

    # vendors
    async def create_gate_vendor(self, account_id, name, url, domain, approval_threshold=80,
                                 critical_threshold=0, notify_vendor=False, monitor_enabled=False,
                                 monitor_interval_days=30, next_monitor_at=None):
        v = {"id": self.vid, "account_id": account_id, "name": name, "url": url, "domain": domain,
             "status": "pending", "approval_threshold": approval_threshold,
             "critical_threshold": critical_threshold, "last_scan_id": None, "last_scan_score": None,
             "last_scan_at": None, "notify_vendor": notify_vendor, "monitor_enabled": monitor_enabled,
             "monitor_interval_days": monitor_interval_days, "next_monitor_at": next_monitor_at,
             "notes": None, "created_at": _now(), "updated_at": _now()}
        self.vendors[self.vid] = v
        self.vid += 1
        return dict(v)

    async def list_gate_vendors(self, account_id):
        return [dict(v) for v in self.vendors.values() if v["account_id"] == account_id]

    async def get_gate_vendor(self, vid, account_id):
        v = self.vendors.get(int(vid))
        return dict(v) if v and v["account_id"] == account_id else None

    async def update_gate_vendor(self, vid, account_id, **fields):
        v = self.vendors.get(int(vid))
        if not v or v["account_id"] != account_id:
            return None
        v.update({k: val for k, val in fields.items() if val is not None})
        return dict(v)

    async def delete_gate_vendor(self, vid, account_id):
        v = self.vendors.get(int(vid))
        if v and v["account_id"] == account_id:
            del self.vendors[int(vid)]
            return True
        return False

    async def create_gate_vendor_scan(self, vendor_id, account_id, score, passed, critical, high,
                                      medium, status, duration_ms, results, summary):
        sid = self.sid
        self.scans[sid] = {"id": sid, "vendor_id": vendor_id, "score": score, "passed": passed,
                           "critical": critical, "high": high, "medium": medium, "status": status,
                           "duration_ms": duration_ms, "results": results, "summary": summary,
                           "created_at": _now()}
        self.sid += 1
        return sid

    async def list_gate_vendor_scans(self, vendor_id, account_id, limit=20):
        return [dict(s) for s in sorted(self.scans.values(), key=lambda x: -x["id"])
                if s["vendor_id"] == vendor_id][:limit]

    async def apply_gate_vendor_scan(self, vendor_id, account_id, scan_id, score, status, next_monitor_at=None):
        v = self.vendors.get(int(vendor_id))
        if not v:
            return False
        v["last_scan_id"] = scan_id
        v["last_scan_score"] = score
        v["last_scan_at"] = _now()
        v["status"] = status
        if next_monitor_at is not None:
            v["next_monitor_at"] = next_monitor_at
        return True

    async def get_vendors_due_for_monitoring(self, now, limit=100):
        out = [dict(v) for v in self.vendors.values()
               if v["monitor_enabled"] and (v["next_monitor_at"] is None or v["next_monitor_at"] <= now)]
        return out[:limit]


def _key(store, account_id=10):
    full = "KLM_" + ("a" * 32)
    store.keys.append({"id": 1, "account_id": account_id, "key_prefix": full[:8],
                       "key_hash": g._hash_key(full), "name": "d", "is_active": True,
                       "created_at": _now(), "grace_expires_at": None})
    return full


@pytest.fixture
def store(monkeypatch):
    s = FakeStore()
    monkeypatch.setenv("JWT_SECRET", SECRET)
    monkeypatch.setattr(m, "get_target_store", lambda: s)
    monkeypatch.setattr(g, "get_target_store", lambda: s)
    import discovery.store as ds
    monkeypatch.setattr(ds, "get_target_store", lambda: s)
    monkeypatch.setattr(m, "_cache", None)
    monkeypatch.setattr(m, "_spawn", lambda coro: coro.close())
    monkeypatch.setattr(g, "_scan_redis", lambda: None)
    async def _fake_run_all(url, timeout=60, checks=None, config=None, deploy_ts=None):
        return _report()
    monkeypatch.setattr(g, "run_all", _fake_run_all)
    return s


@pytest.fixture
def client(store):
    return TestClient(m.app, raise_server_exceptions=False)


def _h(key):
    return {"X-API-Key": key}


# =========================================================================== #
# Status (puro)
# =========================================================================== #

@pytest.mark.parametrize("score,crit,thr,maxc,exp", [
    (90, 0, 80, 0, "approved"),
    (65, 0, 80, 0, "attention"),
    (30, 3, 80, 0, "rejected"),
    (85, 1, 80, 0, "rejected"),   # crítico acima do máximo → reprova apesar do score
    (80, 0, 80, 0, "approved"),
    (59, 0, 80, 0, "rejected"),
])
def test_calculate_vendor_status(score, crit, thr, maxc, exp):
    assert calculate_vendor_status(score, crit, thr, maxc) == exp


def test_build_payload_redacts():
    p = build_vendor_scan_payload(_report(), 80, 0)
    blob = json.dumps(p)
    assert "ABC123XYZ" not in blob and "/.env" not in blob and "/app.js" not in blob
    assert p["summary"]["credentials"] >= 1 and p["summary"]["exposed_files"] >= 1
    assert p["critical"] == 2 and p["high"] == 1


# =========================================================================== #
# CRUD + gate Enterprise
# =========================================================================== #

def test_create_vendor_enterprise(client, store):
    key = _key(store)
    r = client.post("/gate/vendors", json={"name": "SaaS A", "url": "https://vendor.com.br"}, headers=_h(key))
    assert r.status_code == 200
    d = r.json()
    assert d["vendor_id"] == 1
    assert d["scan"]["critical"] == 2
    # redação no payload de resposta
    blob = json.dumps(d)
    assert "ABC123XYZ" not in blob and "/.env" not in blob


def test_create_vendor_non_enterprise_403(client, store):
    store.plan = _FREE
    store.users[10]["gate_plan_id"] = _FREE["id"]
    key = _key(store)
    r = client.post("/gate/vendors", json={"name": "X", "url": "https://vendor.com.br"}, headers=_h(key))
    assert r.status_code == 403


def test_list_vendors(client, store):
    key = _key(store)
    client.post("/gate/vendors", json={"name": "A", "url": "https://vendor.com.br"}, headers=_h(key))
    r = client.get("/gate/vendors", headers=_h(key))
    assert r.status_code == 200
    vs = r.json()["vendors"]
    assert len(vs) == 1 and vs[0]["last_scan_score"] is not None


def test_rescan_vendor_updates_status(client, store):
    key = _key(store)
    vid = client.post("/gate/vendors", json={"name": "A", "url": "https://vendor.com.br"}, headers=_h(key)).json()["vendor_id"]
    r = client.post(f"/gate/vendors/{vid}/scan", headers=_h(key))
    assert r.status_code == 200
    assert store.vendors[vid]["status"] in ("approved", "attention", "rejected")


def test_vendor_detail_has_no_paths(client, store):
    key = _key(store)
    vid = client.post("/gate/vendors", json={"name": "A", "url": "https://vendor.com.br"}, headers=_h(key)).json()["vendor_id"]
    r = client.get(f"/gate/vendors/{vid}", headers=_h(key))
    assert r.status_code == 200
    blob = json.dumps(r.json())
    assert "ABC123XYZ" not in blob and "/.env" not in blob
    assert r.json()["scans"][0]["summary"]["credentials"] >= 1


def test_update_and_delete_vendor(client, store):
    key = _key(store)
    vid = client.post("/gate/vendors", json={"name": "A", "url": "https://vendor.com.br"}, headers=_h(key)).json()["vendor_id"]
    r = client.put(f"/gate/vendors/{vid}", json={"approval_threshold": 95, "notes": "revisar"}, headers=_h(key))
    assert r.status_code == 200 and r.json()["vendor"]["approval_threshold"] == 95
    assert client.delete(f"/gate/vendors/{vid}", headers=_h(key)).status_code == 200
    assert vid not in store.vendors


# =========================================================================== #
# Notificação opt-in
# =========================================================================== #

def test_notify_vendor_sends(store, monkeypatch):
    mailer = FakeMailer()
    monkeypatch.setattr(m, "_mailer", lambda: mailer)
    monkeypatch.setattr(g, "_scan_redis", lambda: None)
    vendor = {"id": 1, "domain": "vendor.com.br", "url": "https://vendor.com.br"}
    _run(g._notify_vendor(10, vendor, 55, 100))
    assert mailer.sent and mailer.sent[0][0] == "assessment" and mailer.sent[0][1] == "dono@vendor.com.br"


def test_notify_vendor_dedup(store, monkeypatch):
    mailer = FakeMailer()
    redis = FakeRedis()
    monkeypatch.setattr(m, "_mailer", lambda: mailer)
    monkeypatch.setattr(g, "_scan_redis", lambda: redis)
    vendor = {"id": 1, "domain": "vendor.com.br", "url": "https://vendor.com.br"}
    _run(g._notify_vendor(10, vendor, 55, 100))
    _run(g._notify_vendor(10, vendor, 55, 100))   # mesmo scan_id → dedup
    assert len([s for s in mailer.sent if s[0] == "assessment"]) == 1


def test_notify_vendor_no_contact(store, monkeypatch):
    mailer = FakeMailer()
    monkeypatch.setattr(m, "_mailer", lambda: mailer)
    monkeypatch.setattr(g, "_scan_redis", lambda: None)
    vendor = {"id": 1, "domain": "sem-contato.com.br", "url": "https://sem-contato.com.br"}
    _run(g._notify_vendor(10, vendor, 55, 100))
    assert mailer.sent == []


# =========================================================================== #
# Monitoramento
# =========================================================================== #

def test_monitor_scans_and_alerts_on_drop(store):
    v = _run(store.create_gate_vendor(10, "A", "https://vendor.com.br", "vendor.com.br",
                                      approval_threshold=80, monitor_enabled=True,
                                      next_monitor_at=_now() - timedelta(days=1)))
    mailer = FakeMailer()

    async def fake_scan(account_id, vendor):
        return {"score": 40, "status": "rejected"}

    w = VendorMonitorWorker(store=store, scan_fn=fake_scan, mailer_fn=lambda: mailer)
    res = _run(w.run_cycle(_now()))
    assert res["scanned"] == 1 and res["alerted"] == 1
    assert mailer.sent[0][0] == "drop"


def test_monitor_no_alert_when_ok(store):
    _run(store.create_gate_vendor(10, "A", "https://vendor.com.br", "vendor.com.br",
                                  approval_threshold=80, monitor_enabled=True,
                                  next_monitor_at=_now() - timedelta(days=1)))
    mailer = FakeMailer()

    async def fake_scan(account_id, vendor):
        return {"score": 95, "status": "approved"}

    w = VendorMonitorWorker(store=store, scan_fn=fake_scan, mailer_fn=lambda: mailer)
    res = _run(w.run_cycle(_now()))
    assert res["scanned"] == 1 and res["alerted"] == 0 and mailer.sent == []


def test_run_vendor_scan_sets_next_monitor(store, monkeypatch):
    monkeypatch.setattr(m, "_spawn", lambda coro: coro.close())
    v = _run(store.create_gate_vendor(10, "A", "https://vendor.com.br", "vendor.com.br",
                                      approval_threshold=80, monitor_enabled=True,
                                      monitor_interval_days=30))
    _run(g.run_vendor_scan(10, v))
    updated = store.vendors[v["id"]]
    assert updated["last_scan_score"] is not None
    assert updated["next_monitor_at"] is not None   # reprogramado


# =========================================================================== #
# PDF comparativo (HTML puro — sem WeasyPrint)
# =========================================================================== #

def test_report_endpoint(client, store, monkeypatch):
    key = _key(store)
    client.post("/gate/vendors", json={"name": "SaaS A", "url": "https://vendor.com.br"}, headers=_h(key))
    async def fake_pdf(ctx):
        # valida que o contexto tem os dados certos, sem chamar WeasyPrint
        from reporter.gate_report import build_vendor_report_html
        html = build_vendor_report_html(ctx)
        assert "12.345.678/0001-90" in html and "Não constitui pentest" in html
        return b"%PDF-1.4 fake"
    monkeypatch.setattr("reporter.gate_report.generate_vendor_report_pdf", fake_pdf)
    r = client.post("/gate/vendors/report", json={"vendor_ids": [1], "title": "Q3"}, headers=_h(key))
    assert r.status_code == 200
    rid = r.json()["report_id"]
    # download (fallback in-memory, pois _scan_redis=None)
    d = client.get(f"/gate/vendors/report/{rid}", headers=_h(key))
    assert d.status_code == 200 and d.headers["content-type"] == "application/pdf"
