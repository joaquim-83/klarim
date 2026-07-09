"""Testes do tratamento de bounce (KL-24) — assinatura, webhook, status. Offline."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json

from fastapi.testclient import TestClient

import api.main as m
from notifier import KlarimMailer, verify_resend_signature


# --- assinatura Svix do webhook -------------------------------------------- #

def _sign(secret: str, svix_id: str, ts: str, body: str) -> str:
    key = secret[len("whsec_"):] if secret.startswith("whsec_") else secret
    sig = base64.b64encode(
        hmac.new(base64.b64decode(key), f"{svix_id}.{ts}.{body}".encode(), hashlib.sha256).digest()
    ).decode()
    return f"v1,{sig}"


def test_verify_resend_signature_ok_and_tamper():
    secret = "whsec_" + base64.b64encode(b"supersecretkey-kl24").decode()
    body = '{"type":"email.bounced"}'
    headers = {"svix-id": "msg_1", "svix-timestamp": "1700000000",
               "svix-signature": _sign(secret, "msg_1", "1700000000", body)}
    assert verify_resend_signature(secret, headers, body.encode()) is True
    # body adulterado -> falha
    assert verify_resend_signature(secret, headers, b'{"type":"x"}') is False
    # sem secret -> falha
    assert verify_resend_signature("", headers, body.encode()) is False
    # header ausente -> falha
    assert verify_resend_signature(secret, {"svix-id": "msg_1"}, body.encode()) is False


def test_verify_resend_signature_multiple_versions():
    secret = "whsec_" + base64.b64encode(b"k").decode()
    body = "{}"
    good = _sign(secret, "id", "1", body).split(",", 1)[1]
    headers = {"svix-id": "id", "svix-timestamp": "1",
               "svix-signature": f"v1,invalidzz v1,{good}"}  # 1º inválido, 2º válido
    assert verify_resend_signature(secret, headers, body.encode()) is True


# --- get_email_event (Resend GET /emails/{id}) ----------------------------- #

def test_get_email_event_parses_last_event(monkeypatch):
    import httpx

    class FakeResp:
        status_code = 200

        def json(self):
            return {"last_event": "bounced"}

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, headers=None, timeout=None):
            return FakeResp()

    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **k: FakeClient())
    m2 = KlarimMailer("re_fake")
    assert asyncio.run(m2.get_email_event("abc")) == "bounced"


def test_get_email_event_none_on_error(monkeypatch):
    import httpx

    class FakeResp:
        status_code = 404

        def json(self):
            return {}

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, *a, **k):
            return FakeResp()

    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **k: FakeClient())
    assert asyncio.run(KlarimMailer("re_fake").get_email_event("x")) is None


# --- _bounce_status -------------------------------------------------------- #

def test_bounce_status_thresholds():
    assert m._bounce_status(0.0) == "ok"
    assert m._bounce_status(1.9) == "ok"
    assert m._bounce_status(2.0) == "warning"
    assert m._bounce_status(4.0) == "warning"
    assert m._bounce_status(4.1) == "critical"
    assert m._bounce_status(10.67) == "critical"


# --- webhook /webhooks/resend ---------------------------------------------- #

class FakeStore:
    def __init__(self):
        self.discarded = []
        self.blocked = []
        self.unsubscribed = []
        self.marked = []

    async def discard_target_by_email(self, email, reason="bounced"):
        self.discarded.append((email, reason))
        return 1

    async def block_email(self, email, reason="bounced"):
        self.blocked.append((email, reason))

    async def mark_unsubscribed(self, email):
        self.unsubscribed.append(email)
        return 1

    async def mark_alert_status_by_email_id(self, email_id, status):
        self.marked.append((email_id, status))
        return 1


def _client(monkeypatch, store):
    monkeypatch.setattr(m, "get_target_store", lambda: store)
    monkeypatch.delenv("RESEND_WEBHOOK_SECRET", raising=False)  # sem assinatura no teste
    return TestClient(m.app, raise_server_exceptions=False)


def test_webhook_resend_permanent_bounce_discards(monkeypatch):
    store = FakeStore()
    c = _client(monkeypatch, store)
    r = c.post("/webhooks/resend", json={
        "type": "email.bounced",
        "data": {"email_id": "e1", "to": ["contato@dominio.com.br"],
                 "bounce": {"type": "permanent", "message": "550 no such user"}},
    })
    assert r.status_code == 200
    assert store.discarded == [("contato@dominio.com.br", "bounced: 550 no such user")]
    assert store.blocked == [("contato@dominio.com.br", "bounced")]
    assert ("e1", "bounced") in store.marked


def test_webhook_resend_transient_bounce_kept(monkeypatch):
    store = FakeStore()
    c = _client(monkeypatch, store)
    r = c.post("/webhooks/resend", json={
        "type": "email.bounced",
        "data": {"email_id": "e2", "to": ["cheio@dominio.com.br"],
                 "bounce": {"type": "Transient", "message": "mailbox full"}},
    })
    assert r.status_code == 200
    assert store.discarded == [] and store.blocked == []  # transitório não descarta


def test_webhook_resend_complaint_unsubscribes(monkeypatch):
    store = FakeStore()
    c = _client(monkeypatch, store)
    r = c.post("/webhooks/resend", json={
        "type": "email.complained",
        "data": {"email_id": "e3", "to": ["reclamou@dominio.com.br"]},
    })
    assert r.status_code == 200
    assert store.unsubscribed == ["reclamou@dominio.com.br"]
    assert store.blocked == [("reclamou@dominio.com.br", "complained")]


def test_webhook_resend_bad_signature_401(monkeypatch):
    store = FakeStore()
    monkeypatch.setattr(m, "get_target_store", lambda: store)
    monkeypatch.setenv("RESEND_WEBHOOK_SECRET", "whsec_" + base64.b64encode(b"k").decode())
    c = TestClient(m.app, raise_server_exceptions=False)
    r = c.post("/webhooks/resend", json={"type": "email.bounced", "data": {}},
               headers={"svix-id": "x", "svix-timestamp": "1", "svix-signature": "v1,bad"})
    assert r.status_code == 401
    assert store.discarded == []


def test_webhook_resend_is_public():
    assert m._is_protected("/webhooks/resend") is False