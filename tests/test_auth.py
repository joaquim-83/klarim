"""Testes da autenticação do dashboard admin (KL-14) — offline."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import jwt
import pytest
from fastapi.testclient import TestClient

import api.main as m


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("ADMIN_USER", "admin")
    monkeypatch.setenv("ADMIN_PASSWORD", "s3nha-forte")
    monkeypatch.setenv("JWT_SECRET", "x" * 64)
    return TestClient(m.app, raise_server_exceptions=False)


def _login(client, user="admin", pw="s3nha-forte"):
    return client.post("/auth/login", json={"username": user, "password": pw})


# --- login ----------------------------------------------------------------- #

def test_login_success(client):
    r = _login(client)
    assert r.status_code == 200
    body = r.json()
    assert body["expires_in"] == 86400 and body["token"]


def test_login_wrong_password(client):
    assert _login(client, pw="errada").status_code == 401


def test_login_wrong_user(client):
    assert _login(client, user="root").status_code == 401


def test_login_not_configured(monkeypatch):
    monkeypatch.delenv("ADMIN_USER", raising=False)
    monkeypatch.delenv("JWT_SECRET", raising=False)
    c = TestClient(m.app, raise_server_exceptions=False)
    assert c.post("/auth/login", json={"username": "a", "password": "b"}).status_code == 503


# --- proteção de rotas ----------------------------------------------------- #

def test_protected_without_token(client):
    for path in ("/targets", "/scans", "/alerts", "/rescans", "/payments/list",
                 "/email/test", "/discovery/status"):
        assert client.get(path).status_code == 401, path


def test_discovery_status_with_token(client):
    token = _login(client).json()["token"]
    r = client.get("/discovery/status", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    body = r.json()
    assert "source" in body and "cycles_completed" in body


def test_protected_with_invalid_token(client):
    r = client.get("/targets", headers={"Authorization": "Bearer not-a-jwt"})
    assert r.status_code == 401


def test_protected_with_expired_token(client):
    payload = {"sub": "admin", "exp": datetime.now(timezone.utc) - timedelta(hours=1)}
    token = jwt.encode(payload, "x" * 64, algorithm="HS256")
    r = client.get("/targets", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 401


def test_valid_token_passes_middleware(client):
    # Com token válido a request atravessa o middleware (não é 401); sem DB o
    # handler dá 500 — o que basta para provar que a auth liberou.
    token = _login(client).json()["token"]
    r = client.get("/targets", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code != 401


# --- rotas públicas ficam livres ------------------------------------------- #

def test_public_routes_open(client):
    assert client.get("/health").status_code == 200
    # /scan (singular) e /payment (singular) não são protegidos como /scans /payments
    assert m._is_protected("/scan/summary") is False
    assert m._is_protected("/payment/create") is False
    assert m._is_protected("/scans") is True
    assert m._is_protected("/payments/list") is True


def test_token_roundtrip(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "y" * 64)
    tok = m._create_token("admin")
    assert m._verify_token(tok)["sub"] == "admin"
