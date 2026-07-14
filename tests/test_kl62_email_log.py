"""Testes do KL-62 — rastreabilidade unificada de e-mails (email_log + blocklist).

Offline: KlarimMailer com store injetado (log + blocklist centralizados) + store SQL
com cursor falso + endpoints via TestClient + FakeStore + MCP get_email_log.
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

import api.main as m
from notifier import KlarimMailer, KlarimMailerError, EMAIL_TYPES
from discovery.store import TargetStore


def _run(coro):
    return asyncio.run(coro)


# ============================ 1. KlarimMailer (centralização) ============== #

class FakeMailerStore:
    def __init__(self, blocked=()):
        self.blocked = {e.lower() for e in blocked}
        self.logs = []

    async def is_email_blocked(self, email):
        return (email or "").lower() in self.blocked

    async def log_email(self, **kw):
        self.logs.append(kw)


def _mailer(store, ok_id="em_1"):
    mailer = KlarimMailer("re_test", store=store)
    mailer._send_sync = lambda params: {"email_id": ok_id, "raw": {}}
    return mailer


def test_send_logs_sent():
    store = FakeMailerStore()
    mailer = _mailer(store)
    res = _run(mailer._send({"to": ["A@B.com"], "subject": "S"}, email_type="alert",
                            source="alert_worker", target_id=7, domain="b.com"))
    assert res["email_id"] == "em_1"
    log = store.logs[-1]
    assert log["status"] == "sent" and log["email_type"] == "alert"
    assert log["to_email"] == "A@B.com" and log["target_id"] == 7 and log["source"] == "alert_worker"


def test_send_blocked_does_not_send_and_logs_blocked():
    store = FakeMailerStore(blocked={"bad@x.com"})
    mailer = KlarimMailer("re_test", store=store)
    calls = {"n": 0}

    def _sync(params):
        calls["n"] += 1
        return {"email_id": "x", "raw": {}}

    mailer._send_sync = _sync
    res = _run(mailer._send({"to": ["bad@x.com"], "subject": "S"}, email_type="profile_view"))
    assert res.get("blocked") is True and res["email_id"] is None
    assert calls["n"] == 0  # não chegou a enviar
    assert store.logs[-1]["status"] == "blocked" and store.logs[-1]["blocked_reason"] == "blocklist"


def test_send_skip_blocklist_sends_anyway():
    store = FakeMailerStore(blocked={"user@x.com"})
    mailer = _mailer(store, ok_id="em_2")
    res = _run(mailer._send({"to": ["user@x.com"], "subject": "S"},
                            email_type="verification_code", skip_blocklist=True))
    assert res["email_id"] == "em_2" and store.logs[-1]["status"] == "sent"


def test_send_failure_logs_failed_and_raises():
    store = FakeMailerStore()
    mailer = KlarimMailer("re_test", store=store)

    def _boom(params):
        raise KlarimMailerError("Resend down")

    mailer._send_sync = _boom
    with pytest.raises(KlarimMailerError):
        _run(mailer._send({"to": ["a@b.com"], "subject": "S"}, email_type="alert"))
    assert store.logs[-1]["status"] == "failed" and "Resend down" in store.logs[-1]["error"]


def test_log_email_never_raises():
    class BadStore:
        async def is_email_blocked(self, email):
            return False

        async def log_email(self, **kw):
            raise RuntimeError("db down")

    mailer = _mailer(BadStore(), ok_id="em_3")
    res = _run(mailer._send({"to": ["a@b.com"], "subject": "S"}, email_type="alert"))
    assert res["email_id"] == "em_3"  # log falhou mas o envio saiu


def test_is_blocked_fail_open():
    class BadStore:
        async def is_email_blocked(self, email):
            raise RuntimeError("db down")

    mailer = KlarimMailer("re_test", store=BadStore())
    assert _run(mailer._is_blocked("a@b.com")) is False  # fail-open: não bloqueia


def test_no_store_still_sends():
    # sem store injetado e sem singleton disponível → envia (fail-open), sem log
    mailer = KlarimMailer("re_test", store=None)
    mailer._get_store = lambda: None
    mailer._send_sync = lambda params: {"email_id": "em_x", "raw": {}}
    res = _run(mailer._send({"to": ["a@b.com"], "subject": "S"}, email_type="alert"))
    assert res["email_id"] == "em_x"


def test_send_batch_filters_blocklist_and_preserves_alignment():
    store = FakeMailerStore(blocked={"bad@x.com"})
    mailer = KlarimMailer("re_test", store=store)

    async def _fake_raw(payloads, key):
        return {"data": [{"id": f"id_{i}"} for i in range(len(payloads))]}

    mailer._send_batch_raw = _fake_raw
    payloads = [{"to": ["a@x.com"], "subject": "1"},
                {"to": ["bad@x.com"], "subject": "2"},
                {"to": ["c@x.com"], "subject": "3"}]
    items = [{"to_email": "a@x.com", "target_id": 1, "target_url": "https://a.com"},
             {"to_email": "bad@x.com", "target_id": 2},
             {"to_email": "c@x.com", "target_id": 3}]
    res = _run(mailer._send_batch(payloads, items, email_type="alert", source="alert_worker"))
    # bloqueado no índice 1 → None nessa posição (alinhamento preservado p/ o AlertWorker)
    assert res["ids"] == ["id_0", None, "id_1"]
    assert res["sent"] == 2 and res["failed"] == 1
    statuses = [l["status"] for l in store.logs]
    assert statuses.count("blocked") == 1 and statuses.count("sent") == 2
    assert all(l.get("batch_id") for l in store.logs)


def test_send_batch_per_item_types():
    store = FakeMailerStore()
    mailer = KlarimMailer("re_test", store=store)

    async def _fake_raw(payloads, key):
        return {"data": [{"id": f"id_{i}"} for i in range(len(payloads))]}

    mailer._send_batch_raw = _fake_raw
    payloads = [{"to": ["a@x.com"], "subject": "1"}, {"to": ["b@x.com"], "subject": "2"}]
    items = [{"to_email": "a@x.com"}, {"to_email": "b@x.com"}]
    _run(mailer._send_batch(payloads, items, email_type="alert",
                            types=["alert", "alert_score100"]))
    types = [l["email_type"] for l in store.logs]
    assert types == ["alert", "alert_score100"]


def test_send_alert_email_type_default_and_score100():
    store = FakeMailerStore()
    mailer = _mailer(store)
    _run(mailer.send_alert("a@x.com", "https://x.com", 65, "amarelo", 3, {}))
    assert store.logs[-1]["email_type"] == "alert" and store.logs[-1]["domain"] == "x.com"
    _run(mailer.send_alert("b@x.com", "https://y.com", 100, "verde", 0, {}))
    assert store.logs[-1]["email_type"] == "alert_score100"


def test_verification_code_skips_blocklist():
    store = FakeMailerStore(blocked={"u@x.com"})
    mailer = _mailer(store, ok_id="vc")
    res = _run(mailer.send_verification_code("u@x.com", "123456", "x.com"))
    assert res["email_id"] == "vc"  # transacional: envia apesar da blocklist
    assert store.logs[-1]["email_type"] == "verification_code" and store.logs[-1]["status"] == "sent"


def test_profile_view_respects_blocklist():
    store = FakeMailerStore(blocked={"owner@x.com"})
    mailer = _mailer(store)
    res = _run(mailer.send_profile_view("owner@x.com", "x.com", 80, "amarelo", "https://k/cta"))
    assert res.get("blocked") is True
    assert store.logs[-1]["status"] == "blocked" and store.logs[-1]["email_type"] == "profile_view"


def test_report_transactional_skips_blocklist():
    store = FakeMailerStore(blocked={"buyer@x.com"})
    mailer = _mailer(store, ok_id="rp")
    res = _run(mailer.send_report("buyer@x.com", "https://x.com", 70, b"pdf1", b"pdf2"))
    assert res["email_id"] == "rp"
    assert store.logs[-1]["email_type"] == "report_delivery" and store.logs[-1]["status"] == "sent"


def test_email_types_constant_covers_paths():
    for t in ("alert", "alert_score100", "evolution", "verification_code", "profile_view",
              "report_delivery", "report_send", "password_reset", "account_deleted",
              "account_evolution", "monitor_offer", "monitor_alert", "monitor_restored",
              "recovery", "contact", "test", "admin_alert", "admin_report"):
        assert t in EMAIL_TYPES


# ============================ 2. Store (cursor falso) ===================== #

class _RecCur:
    def __init__(self, one=None, all_rows=None, description=None):
        self.executed = []
        self._one = one
        self._all = all_rows or []
        self.description = description or [("id",), ("email_type",)]
        self.rowcount = 0

    def execute(self, sql, params=None):
        self.executed.append((sql, params))

    def fetchone(self):
        return self._one

    def fetchall(self):
        return self._all


def test_log_email_insert(monkeypatch):
    cur = _RecCur()
    store = TargetStore()
    monkeypatch.setattr(store, "_run", lambda fn: fn(cur))
    _run(store.log_email(email_id="em1", to_email="A@B.com", email_type="alert",
                         status="sent", source="alert_worker", target_id=3))
    sql, params = cur.executed[-1]
    assert "INSERT INTO email_log" in sql
    assert params[1] == "a@b.com"  # to_email normalizado (lower)
    assert params[2] == "alert" and params[6] == "sent"


def test_log_email_ignores_empty_recipient(monkeypatch):
    ran = {"v": False}
    store = TargetStore()
    monkeypatch.setattr(store, "_run", lambda fn: ran.__setitem__("v", True))
    _run(store.log_email(email_id="x", to_email="", email_type="alert"))
    assert ran["v"] is False


def test_email_metrics_reads_email_log(monkeypatch):
    cur = _RecCur(one=(5,), all_rows=[("alert", 3), ("profile_view", 2)])
    store = TargetStore()
    monkeypatch.setattr(store, "_run", lambda fn: fn(cur))
    out = _run(store.email_metrics())
    joined = " ".join(s for s, _ in cur.executed)
    assert "email_log" in joined and "alert_log" not in joined
    assert out["sent_today"] == 5 and out["blocked_today"] == 5 and out["failed_today"] == 5
    assert out["by_type"][0]["email_type"] == "alert"


def test_email_health_reads_email_log(monkeypatch):
    cur = _RecCur(one=(100, 4, 1))
    store = TargetStore()
    monkeypatch.setattr(store, "_run", lambda fn: fn(cur))
    out = _run(store.email_health())
    joined = " ".join(s for s, _ in cur.executed)
    assert "FROM email_log" in joined
    assert out["total"] == 100 and out["bounced"] == 4 and out["complained"] == 1


def test_list_email_log_filters(monkeypatch):
    cur = _RecCur(one=(2,), all_rows=[("sent", 2)],
                  description=[("id",), ("email_type",), ("status",)])
    store = TargetStore()
    monkeypatch.setattr(store, "_run", lambda fn: fn(cur))
    out = _run(store.list_email_log(email_type="alert", status="sent", to_email="loja", limit=10))
    assert out["total"] == 2
    joined = " ".join(s for s, _ in cur.executed)
    assert "email_type = %s" in joined and "status = %s" in joined and "LOWER(to_email) LIKE" in joined


def test_mark_email_status_by_email_id(monkeypatch):
    cur = _RecCur()
    cur.rowcount = 1
    store = TargetStore()
    monkeypatch.setattr(store, "_run", lambda fn: fn(cur))
    n = _run(store.mark_email_status_by_email_id("em1", "bounced"))
    sql, params = cur.executed[-1]
    assert "UPDATE email_log SET status" in sql and params == ("bounced", "em1")
    assert n == 1


def test_migrate_email_log_idempotent_guard(monkeypatch):
    cur = _RecCur()
    store = TargetStore()
    monkeypatch.setattr(store, "_run", lambda fn: fn(cur))
    res = _run(store.migrate_email_log())
    joined = " ".join(s for s, _ in cur.executed)
    assert "NOT EXISTS" in joined and "alert_log" in joined and "rescan_log" in joined
    assert res == {"alert_log": 0, "rescan_log": 0}  # rowcount 0 no cursor falso


def test_get_sent_emails_for_bounce_check_from_email_log(monkeypatch):
    cur = _RecCur(all_rows=[("em1", "a@x.com")], description=[("email_id",), ("contact_email",)])
    store = TargetStore()
    monkeypatch.setattr(store, "_run", lambda fn: fn(cur))
    rows = _run(store.get_sent_emails_for_bounce_check(limit=50))
    joined = " ".join(s for s, _ in cur.executed)
    assert "FROM email_log" in joined and rows[0]["email_id"] == "em1"


# ============================ 3. API (FakeStore) ========================== #

class FakeStore:
    def __init__(self):
        self.marks = []

    async def list_email_log(self, **kw):
        self.kw = kw
        return {"emails": [{"id": 1, "email_type": "profile_view", "to_email": "a@x.com",
                            "status": "sent"}],
                "total": 1, "by_status": {"sent": 1}}

    async def list_email_activity(self, limit=50):
        return [{"email_type": "verification_code", "to_email": "u@x.com", "status": "sent",
                 "domain": "x.com", "blocked_reason": None, "sent_at": "2026-07-14T16:00:00"},
                {"email_type": "profile_view", "to_email": "b@x.com", "status": "blocked",
                 "domain": "y.com", "blocked_reason": "blocklist", "sent_at": "2026-07-14T15:00:00"}]

    async def list_alerts(self, **kw):
        return []

    async def list_rescans(self, **kw):
        return []

    async def list_scans(self, **kw):
        return []

    # webhook bounce
    async def mark_alert_status_by_email_id(self, email_id, status):
        self.marks.append(("alert", email_id, status))
        return 1

    async def mark_email_status_by_email_id(self, email_id, status):
        self.marks.append(("email", email_id, status))
        return 1

    async def discard_target_by_email(self, email, reason=""):
        pass

    async def block_email(self, email, reason="bounced"):
        pass


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("ADMIN_USER", "admin")
    monkeypatch.setenv("ADMIN_PASSWORD", "s3nha-forte")
    monkeypatch.setenv("JWT_SECRET", "x" * 64)
    monkeypatch.delenv("RESEND_WEBHOOK_SECRET", raising=False)
    store = FakeStore()
    monkeypatch.setattr(m, "get_target_store", lambda: store)
    c = TestClient(m.app, raise_server_exceptions=False)
    c._store = store
    return c


def _auth(client):
    tok = client.post("/auth/login",
                      json={"username": "admin", "password": "s3nha-forte"}).json()["token"]
    return {"Authorization": f"Bearer {tok}"}


def test_email_log_endpoint_protected():
    assert m._is_protected("/email/log") is True
    c = TestClient(m.app, raise_server_exceptions=False)
    assert c.get("/email/log").status_code == 401


def test_email_log_endpoint_lists(client):
    r = client.get("/email/log?email_type=profile_view&status=sent&to_email=a", headers=_auth(client))
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 1 and body["by_status"]["sent"] == 1
    assert "types" in body and body["types"]["alert"]
    assert client._store.kw["email_type"] == "profile_view" and client._store.kw["status"] == "sent"


def test_email_log_bad_type_ignored(client):
    client.get("/email/log?email_type=banana", headers=_auth(client))
    assert client._store.kw["email_type"] is None


def test_webhook_bounce_marks_email_log(client):
    payload = {"type": "email.bounced", "data": {"email_id": "em9", "to": ["x@y.com"],
                                                 "bounce": {"type": "permanent"}}}
    r = client.post("/webhooks/resend", json=payload)
    assert r.status_code == 200
    kinds = {(k, s) for k, _e, s in client._store.marks}
    assert ("email", "bounced") in kinds and ("alert", "bounced") in kinds


def test_system_activity_includes_emails(client):
    r = client.get("/system/activity?limit=20", headers=_auth(client))
    assert r.status_code == 200
    types = {e["type"] for e in r.json()["activity"]}
    assert "email" in types and "email_blocked" in types


# ============================ 4. MCP get_email_log ======================== #

def test_mcp_get_email_log(monkeypatch):
    import mcp_server.tools.system as system_tools

    class St:
        async def list_email_log(self, **kw):
            self.kw = kw
            return {"emails": [{"id": 1}], "total": 1, "by_status": {"sent": 1}}

    st = St()
    monkeypatch.setattr("mcp_server._base.get_target_store", lambda: st)
    res = _run(system_tools.get_email_log(email_type="alert", status="sent", limit=5))
    assert res["total"] == 1 and st.kw["email_type"] == "alert" and st.kw["status"] == "sent"
