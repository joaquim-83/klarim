"""Fix do unsubscribe (2026-07-17): params ausentes → HTML branded (não 422 JSON),
one-click POST (RFC 8058) e headers List-Unsubscribe nos e-mails proativos. Offline."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import api.main as m
from notifier import email_client as ec
from notifier.email_client import list_unsubscribe_headers, unsubscribe_token, build_unsubscribe_link


class FakeStore:
    def __init__(self):
        self.unsubscribed = []

    async def mark_unsubscribed(self, email):
        self.unsubscribed.append(email.lower().strip())
        return 1


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("UNSUBSCRIBE_SECRET", "s" * 40)
    store = FakeStore()
    monkeypatch.setattr(m, "get_target_store", lambda: store)
    c = TestClient(m.app, raise_server_exceptions=False)
    c._store = store
    return c


# --------------------------------------------------------------------------- #
# T1A — endpoint
# --------------------------------------------------------------------------- #

def test_unsubscribe_no_params_returns_html_not_json(client):
    r = client.get("/unsubscribe")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert "Link incompleto" in r.text
    assert "detail" not in r.text and "\"loc\"" not in r.text   # não é o 422 JSON do FastAPI


def test_unsubscribe_only_email_no_token(client):
    r = client.get("/unsubscribe?email=a@b.com")
    assert r.status_code == 200 and "Link incompleto" in r.text


def test_unsubscribe_invalid_token(client):
    r = client.get("/unsubscribe?email=a@b.com&token=nope")
    assert r.status_code == 400 and "Link inválido" in r.text
    assert client._store.unsubscribed == []


def test_unsubscribe_valid_token(client):
    tok = unsubscribe_token("a@b.com", "s" * 40)
    r = client.get(f"/unsubscribe?email=a@b.com&token={tok}")
    assert r.status_code == 200 and "Descadastro concluído" in r.text
    assert client._store.unsubscribed == ["a@b.com"]


def test_unsubscribe_oneclick_post(client):
    tok = unsubscribe_token("c@d.com", "s" * 40)
    r = client.post(f"/unsubscribe?email=c@d.com&token={tok}")
    assert r.status_code == 200 and "Descadastro concluído" in r.text
    assert client._store.unsubscribed == ["c@d.com"]


def test_unsubscribe_post_no_params_html(client):
    r = client.post("/unsubscribe")
    assert r.status_code == 200 and "Link incompleto" in r.text


# --------------------------------------------------------------------------- #
# T1B — headers List-Unsubscribe (puro)
# --------------------------------------------------------------------------- #

def test_list_unsubscribe_headers():
    h = list_unsubscribe_headers("https://klarim.net/api/unsubscribe?email=a&token=b")
    assert h["List-Unsubscribe"] == "<https://klarim.net/api/unsubscribe?email=a&token=b>"
    assert h["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"
    assert list_unsubscribe_headers(None) == {}
    assert list_unsubscribe_headers("") == {}


def test_alert_params_has_list_unsubscribe(monkeypatch):
    monkeypatch.setenv("UNSUBSCRIBE_SECRET", "s" * 40)
    mailer = ec.KlarimMailer("re_test", "Klarim <x@klarimscan.com>", store=None)
    params = mailer._alert_params("dono@x.com.br", "https://x.com.br", 40, "vermelho", 3, {})
    assert "headers" in params
    assert params["headers"]["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"
    assert "unsubscribe?email=" in params["headers"]["List-Unsubscribe"]


@pytest.mark.asyncio
async def test_profile_view_has_list_unsubscribe(monkeypatch):
    monkeypatch.setenv("UNSUBSCRIBE_SECRET", "s" * 40)
    mailer = ec.KlarimMailer("re_test", "Klarim <x@klarimscan.com>", store=None)
    sent = {}

    async def fake_send(params, **kw):
        sent.update(params)
        return {"email_id": "e1"}

    monkeypatch.setattr(mailer, "_send", fake_send)
    await mailer.send_profile_view("dono@x.com.br", "x.com.br", 72, "amarelo", "https://klarim.net/cadastrar")
    assert "headers" in sent and sent["headers"]["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"
