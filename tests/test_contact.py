"""Testes do endpoint de contato do site (público) — offline."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import api.main as m


@pytest.fixture
def client(monkeypatch):
    # E-mail habilitado + mailer falso (não bate na rede).
    monkeypatch.setenv("RESEND_API_KEY", "re_test")
    sent = []

    class FakeMailer:
        async def send_contact(self, name, email, message, to_address="scan@klarim.net"):
            sent.append({"name": name, "email": email, "message": message, "to": to_address})
            return {"email_id": "em_1"}

    monkeypatch.setattr(m, "_mailer", lambda: FakeMailer())
    c = TestClient(m.app, raise_server_exceptions=False)
    c._sent = sent
    return c


def test_contact_public():
    assert m._is_protected("/contact") is False
    # sem RESEND configurado, endpoint responde 503 (não 401 — é público)
    assert TestClient(m.app, raise_server_exceptions=False).post(
        "/contact", json={"email": "a@b.com", "message": "oi"}).status_code in (503, 200)


def test_contact_sends_and_sanitizes(client):
    r = client.post("/contact", json={
        "name": "<b>João</b>", "email": "joao@example.com",
        "message": "<script>alert(1)</script> quero saber do relatório",
    })
    assert r.status_code == 200 and r.json() == {"ok": True}
    msg = client._sent[0]
    assert msg["email"] == "joao@example.com"
    assert "<script>" not in msg["message"] and "<b>" not in msg["name"]
    assert "relatório" in msg["message"]


def test_contact_validates_email(client):
    assert client.post("/contact", json={"email": "nao-eh-email", "message": "oi"}).status_code == 422


def test_contact_requires_message(client):
    assert client.post("/contact", json={"email": "a@b.com", "message": "   "}).status_code == 422


def test_contact_rate_limited(client):
    body = {"email": "a@b.com", "message": "teste"}
    codes = [client.post("/contact", json=body, headers={"X-Real-IP": "9.9.9.9"}).status_code
             for _ in range(4)]
    assert codes == [200, 200, 200, 429], codes
    # outro IP continua livre
    assert client.post("/contact", json=body, headers={"X-Real-IP": "8.8.8.8"}).status_code == 200
