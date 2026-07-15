"""Testes das configurações editáveis + gestão de senha + rotação do token MCP (KL-44).

Offline (TestClient + FakeStore + JWT admin). Cobre: config CRUD (banco > env), validação
de faixa/whitelist, troca de senha (bcrypt no banco, força, senha errada), rotação do
token MCP (token antigo para de funcionar no middleware), verify_admin_password
(banco→env) e rate limits."""

from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

import api.main as m
import api.auth_users as auth_users
from mcp_server.auth import MCPAuthMiddleware

_ADMIN_PW = "senha-do-env-123"


class FakeStore:
    def __init__(self):
        self.settings = {}  # key -> {value, updated_by, updated_at}

    async def get_admin_setting(self, key):
        row = self.settings.get(key)
        return row["value"] if row else None

    async def upsert_admin_setting(self, key, value, updated_by="admin"):
        self.settings[key] = {"value": value, "updated_by": updated_by,
                              "updated_at": "2026-07-15T00:00:00+00:00"}

    async def delete_admin_setting(self, key):
        return self.settings.pop(key, None) is not None

    async def list_admin_settings(self):
        return {k: dict(v) for k, v in self.settings.items()}

    async def get_setting(self, key, default=None):
        row = self.settings.get(key)
        if row is not None:
            return row["value"]
        return os.environ.get(key, default)


@pytest.fixture
def store(monkeypatch):
    monkeypatch.setenv("ADMIN_USER", "op")
    monkeypatch.setenv("ADMIN_PASSWORD", _ADMIN_PW)
    monkeypatch.setenv("JWT_SECRET", "k" * 64)
    s = FakeStore()
    monkeypatch.setattr(m, "get_target_store", lambda: s)
    monkeypatch.setattr("discovery.store.get_target_store", lambda: s)
    return s


@pytest.fixture
def client(store):
    return TestClient(m.app, raise_server_exceptions=False)


def _auth(client):
    tok = client.post("/auth/login", json={"username": "op", "password": _ADMIN_PW}).json()["token"]
    return {"Authorization": f"Bearer {tok}"}


# --- protegido ------------------------------------------------------------- #

def test_config_endpoints_require_auth():
    c = TestClient(m.app, raise_server_exceptions=False)
    assert c.get("/admin/config").status_code == 401
    assert c.put("/admin/config/ALERT_BATCH_SIZE", json={"value": "10"}).status_code == 401
    assert c.patch("/admin/password", json={"current_password": "x", "new_password": "y",
                                            "confirm_password": "y"}).status_code == 401


# --- config CRUD ----------------------------------------------------------- #

def test_config_list_shows_params(client):
    r = client.get("/admin/config", headers=_auth(client))
    assert r.status_code == 200
    keys = {p["key"] for p in r.json()["params"]}
    assert "ALERT_BATCH_SIZE" in keys and "WORKER_MAX_SCANS_PER_HOUR" in keys
    row = next(p for p in r.json()["params"] if p["key"] == "ALERT_BATCH_SIZE")
    assert row["source"] in ("env", "default") and row["min"] == 1 and row["max"] == 100


def test_config_put_and_db_priority(client, store):
    r = client.put("/admin/config/ALERT_BATCH_SIZE", json={"value": "77"}, headers=_auth(client))
    assert r.status_code == 200 and r.json()["value"] == "77" and r.json()["source"] == "db"
    assert store.settings["ALERT_BATCH_SIZE"]["value"] == "77"
    # a listagem reflete o override do banco (prioridade)
    row = next(p for p in client.get("/admin/config", headers=_auth(client)).json()["params"]
               if p["key"] == "ALERT_BATCH_SIZE")
    assert row["value"] == "77" and row["source"] == "db"


def test_config_reset_removes_override(client, store):
    client.put("/admin/config/RESCAN_AGE_DAYS", json={"value": "45"}, headers=_auth(client))
    r = client.post("/admin/config/reset/RESCAN_AGE_DAYS", headers=_auth(client))
    assert r.status_code == 200 and r.json()["source"] == "env"
    assert "RESCAN_AGE_DAYS" not in store.settings


def test_config_put_out_of_range(client):
    assert client.put("/admin/config/ALERT_BATCH_SIZE", json={"value": "999"},
                      headers=_auth(client)).status_code == 400
    assert client.put("/admin/config/ALERT_BATCH_SIZE", json={"value": "0"},
                      headers=_auth(client)).status_code == 400
    assert client.put("/admin/config/ALERT_BATCH_SIZE", json={"value": "abc"},
                      headers=_auth(client)).status_code == 400


def test_config_put_non_editable_key(client):
    assert client.put("/admin/config/ADMIN_PASSWORD_HASH", json={"value": "x"},
                      headers=_auth(client)).status_code == 400
    assert client.put("/admin/config/RANDOM_KEY", json={"value": "1"},
                      headers=_auth(client)).status_code == 400


# --- senha ----------------------------------------------------------------- #

def test_change_password_success_and_db_hash(client, store):
    r = client.patch("/admin/password", headers=_auth(client), json={
        "current_password": _ADMIN_PW, "new_password": "NovaSenha123", "confirm_password": "NovaSenha123"})
    assert r.status_code == 200 and r.json()["ok"] is True
    # gravou um hash bcrypt no banco (não a senha em texto)
    h = store.settings["ADMIN_PASSWORD_HASH"]["value"]
    assert h.startswith("$2") and "NovaSenha123" not in h
    assert auth_users.verify_password("NovaSenha123", h) is True


def test_change_password_wrong_current(client):
    r = client.patch("/admin/password", headers=_auth(client), json={
        "current_password": "errada", "new_password": "NovaSenha123", "confirm_password": "NovaSenha123"})
    assert r.status_code == 401


def test_change_password_weak(client):
    for weak in ("curta1A", "semnumeromaiuscula", "SEM MINUSCULA 123", "toda-minuscula-123"):
        m._password_attempts.clear()  # isola a validação de força do rate limit (3/min)
        r = client.patch("/admin/password", headers=_auth(client), json={
            "current_password": _ADMIN_PW, "new_password": weak, "confirm_password": weak})
        assert r.status_code == 400, weak


def test_change_password_mismatch(client):
    r = client.patch("/admin/password", headers=_auth(client), json={
        "current_password": _ADMIN_PW, "new_password": "NovaSenha123", "confirm_password": "Outra123ABC"})
    assert r.status_code == 400


def test_change_password_rate_limit(client):
    # limite 3/min: a 4ª tentativa (mesmo com senha errada) → 429
    codes = []
    for _ in range(4):
        codes.append(client.patch("/admin/password", headers=_auth(client), json={
            "current_password": "errada", "new_password": "NovaSenha123",
            "confirm_password": "NovaSenha123"}).status_code)
    assert codes[:3] == [401, 401, 401] and codes[3] == 429


# --- verify_admin_password (banco > env) ----------------------------------- #

@pytest.mark.asyncio
async def test_verify_admin_password_db_over_env(store):
    # sem hash no banco → usa o env
    assert await m.verify_admin_password(_ADMIN_PW) is True
    assert await m.verify_admin_password("errada") is False
    # com hash no banco → o banco tem prioridade (env deixa de valer)
    await store.upsert_admin_setting("ADMIN_PASSWORD_HASH", auth_users.hash_password("DaBase123"))
    assert await m.verify_admin_password("DaBase123") is True
    assert await m.verify_admin_password(_ADMIN_PW) is False


# --- rotação do token MCP -------------------------------------------------- #

def test_rotate_mcp_token(client, store, monkeypatch):
    monkeypatch.setenv("MCP_API_KEY", "chave-antiga-0123456789")
    r = client.post("/admin/rotate-mcp-token", headers=_auth(client),
                    json={"current_password": _ADMIN_PW})
    assert r.status_code == 200
    new_token = r.json()["new_token"]
    assert new_token and len(new_token) == 64
    # gravou no banco e aplicou no os.environ (o middleware pega em runtime)
    assert store.settings["MCP_API_KEY"]["value"] == new_token
    assert os.environ["MCP_API_KEY"] == new_token
    # o middleware aceita o token novo e REJEITA o antigo
    mw = MCPAuthMiddleware(None)
    ok_scope = {"type": "http", "path": "/sse", "query_string": f"token={new_token}".encode(), "headers": []}
    old_scope = {"type": "http", "path": "/sse", "query_string": b"token=chave-antiga-0123456789", "headers": []}
    assert mw._check(ok_scope) is True
    assert mw._check(old_scope) is False


def test_rotate_mcp_token_wrong_password(client):
    r = client.post("/admin/rotate-mcp-token", headers=_auth(client),
                    json={"current_password": "errada"})
    assert r.status_code == 401


# --- system-info ----------------------------------------------------------- #

def test_system_info(client):
    r = client.get("/admin/system-info", headers=_auth(client))
    assert r.status_code == 200
    body = r.json()
    assert "uptime_seconds" in body and "redis_connected" in body and "version" in body
