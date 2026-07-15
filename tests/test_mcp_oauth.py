"""Testes do OAuth 2.1 + PKCE do MCP (KL-63) — fluxo completo offline.

Metadata (RFC 9728/8414), Dynamic Client Registration (RFC 7591), authorization code
+ PKCE S256, refresh com rotação, e o middleware (JWT OAuth + token estático fallback +
isenção das rotas OAuth). Usa TestClient + um Redis fake (o oauth reusa `api.main._cache`)."""

from __future__ import annotations

import base64
import hashlib
import secrets
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient

import api.main as m
import mcp_server.oauth as oauth
from mcp_server.auth import MCPAuthMiddleware

_ADMIN_PW = "s3nha-do-admin"
_STATIC = "static-mcp-key-0123456789abcdef"
_REDIRECT = "http://localhost:8400/callback"


class FakeRedis:
    def __init__(self):
        self.store = {}

    async def get(self, k):
        return self.store.get(k)

    async def set(self, k, v, ex=None):
        self.store[k] = v
        return True

    async def delete(self, k):
        return 1 if self.store.pop(k, None) is not None else 0

    async def incr(self, k):
        self.store[k] = int(self.store.get(k, 0)) + 1
        return self.store[k]

    async def expire(self, k, s):
        return True

    async def ttl(self, k):
        return 60


@pytest.fixture
def redis():
    return FakeRedis()


@pytest.fixture
def client(monkeypatch, redis):
    monkeypatch.setenv("JWT_SECRET", "k" * 64)
    monkeypatch.setenv("MCP_ISSUER", "https://klarim.net")
    monkeypatch.setenv("ADMIN_PASSWORD", _ADMIN_PW)
    monkeypatch.setenv("MCP_API_KEY", _STATIC)
    monkeypatch.delenv("MCP_JWT_SECRET", raising=False)
    monkeypatch.setattr(m, "_cache", SimpleNamespace(redis=redis))
    return TestClient(m.app, raise_server_exceptions=False)


def _pkce():
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


def _authz_params(client_id, challenge, state="xyz-state"):
    return {"response_type": "code", "client_id": client_id, "redirect_uri": _REDIRECT,
            "code_challenge": challenge, "code_challenge_method": "S256",
            "state": state, "scope": "mcp:admin"}


def _register(client, uris=None):
    r = client.post("/mcp/register", json={
        "client_name": "Test Client", "redirect_uris": uris or [_REDIRECT]})
    return r


# --- metadata -------------------------------------------------------------- #

def test_protected_resource_metadata(client):
    r = client.get("/.well-known/oauth-protected-resource")
    assert r.status_code == 200
    body = r.json()
    assert body["resource"] == "https://klarim.net/mcp/sse"
    assert body["authorization_servers"] == ["https://klarim.net"]
    assert body["scopes_supported"] == ["mcp:admin"]
    assert r.headers.get("access-control-allow-origin") == "*"


def test_authorization_server_metadata(client):
    body = client.get("/.well-known/oauth-authorization-server").json()
    assert body["issuer"] == "https://klarim.net"
    assert body["authorization_endpoint"] == "https://klarim.net/mcp/authorize"
    assert body["token_endpoint"] == "https://klarim.net/mcp/token"
    assert body["registration_endpoint"] == "https://klarim.net/mcp/register"
    assert body["code_challenge_methods_supported"] == ["S256"]
    assert body["grant_types_supported"] == ["authorization_code", "refresh_token"]


# --- dynamic client registration ------------------------------------------- #

def test_register_client(client):
    r = _register(client)
    assert r.status_code == 201
    body = r.json()
    assert body["client_id"] and body["token_endpoint_auth_method"] == "none"
    assert body["redirect_uris"] == [_REDIRECT]


def test_register_rejects_bad_redirect(client):
    assert _register(client, ["http://evil.example.com/cb"]).status_code == 400
    assert _register(client, ["javascript:alert(1)"]).status_code == 400
    assert client.post("/mcp/register", json={"client_name": "x"}).status_code == 400


def test_register_rate_limit(client):
    codes = [_register(client).status_code for _ in range(6)]
    assert codes[:5] == [201] * 5 and codes[5] == 429


# --- authorization endpoint ------------------------------------------------ #

def test_authorize_get_shows_login(client):
    cid = _register(client).json()["client_id"]
    _, challenge = _pkce()
    r = client.get("/mcp/authorize", params=_authz_params(cid, challenge))
    assert r.status_code == 200 and "Autorizar" in r.text and "password" in r.text
    assert "Test Client" in r.text  # nome do client aparece


def test_authorize_invalid_params(client):
    cid = _register(client).json()["client_id"]
    _, challenge = _pkce()
    # client_id inexistente
    p = _authz_params("nao-existe", challenge)
    assert client.get("/mcp/authorize", params=p).status_code == 400
    # method != S256
    p = _authz_params(cid, challenge); p["code_challenge_method"] = "plain"
    assert client.get("/mcp/authorize", params=p).status_code == 400
    # redirect_uri não registrada
    p = _authz_params(cid, challenge); p["redirect_uri"] = "http://localhost:9999/x"
    assert client.get("/mcp/authorize", params=p).status_code == 400


def test_authorize_wrong_password(client):
    cid = _register(client).json()["client_id"]
    _, challenge = _pkce()
    p = _authz_params(cid, challenge)
    r = client.post("/mcp/authorize", data={**p, "password": "errada"})
    assert r.status_code == 401 and "incorreta" in r.text.lower()


def test_authorize_correct_password_redirects(client):
    cid = _register(client).json()["client_id"]
    _, challenge = _pkce()
    p = _authz_params(cid, challenge)
    r = client.post("/mcp/authorize", data={**p, "password": _ADMIN_PW},
                    follow_redirects=False)
    assert r.status_code == 302
    loc = urlparse(r.headers["location"])
    q = parse_qs(loc.query)
    assert loc.path == "/callback" and q["state"] == ["xyz-state"] and q["code"]


# --- token endpoint (full flow) -------------------------------------------- #

def _get_code(client, cid, challenge, state="xyz-state"):
    p = _authz_params(cid, challenge, state)
    r = client.post("/mcp/authorize", data={**p, "password": _ADMIN_PW},
                    follow_redirects=False)
    return parse_qs(urlparse(r.headers["location"]).query)["code"][0]


def test_token_exchange_and_pkce(client):
    cid = _register(client).json()["client_id"]
    verifier, challenge = _pkce()
    code = _get_code(client, cid, challenge)
    r = client.post("/mcp/token", data={
        "grant_type": "authorization_code", "code": code, "client_id": cid,
        "redirect_uri": _REDIRECT, "code_verifier": verifier})
    assert r.status_code == 200
    body = r.json()
    assert body["token_type"] == "Bearer" and body["expires_in"] == 3600
    assert body["scope"] == "mcp:admin"
    assert oauth.validate_access_token(body["access_token"]) is True
    assert body["refresh_token"]


def test_token_wrong_pkce_rejected(client):
    cid = _register(client).json()["client_id"]
    _, challenge = _pkce()
    code = _get_code(client, cid, challenge)
    r = client.post("/mcp/token", data={
        "grant_type": "authorization_code", "code": code, "client_id": cid,
        "redirect_uri": _REDIRECT, "code_verifier": "verifier-errado"})
    assert r.status_code == 400 and r.json()["error"] == "invalid_grant"


def test_token_code_is_one_time(client):
    cid = _register(client).json()["client_id"]
    verifier, challenge = _pkce()
    code = _get_code(client, cid, challenge)
    data = {"grant_type": "authorization_code", "code": code, "client_id": cid,
            "redirect_uri": _REDIRECT, "code_verifier": verifier}
    assert client.post("/mcp/token", data=data).status_code == 200
    # reuso do mesmo code → erro
    assert client.post("/mcp/token", data=data).status_code == 400


def test_refresh_token_rotation(client):
    cid = _register(client).json()["client_id"]
    verifier, challenge = _pkce()
    code = _get_code(client, cid, challenge)
    first = client.post("/mcp/token", data={
        "grant_type": "authorization_code", "code": code, "client_id": cid,
        "redirect_uri": _REDIRECT, "code_verifier": verifier}).json()
    refresh = first["refresh_token"]
    r = client.post("/mcp/token", data={
        "grant_type": "refresh_token", "refresh_token": refresh, "client_id": cid})
    assert r.status_code == 200
    new = r.json()
    assert new["refresh_token"] != refresh and oauth.validate_access_token(new["access_token"])
    # o refresh antigo foi invalidado (rotação)
    old = client.post("/mcp/token", data={
        "grant_type": "refresh_token", "refresh_token": refresh, "client_id": cid})
    assert old.status_code == 400


def test_token_unsupported_grant(client):
    r = client.post("/mcp/token", data={"grant_type": "password"})
    assert r.status_code == 400 and r.json()["error"] == "unsupported_grant_type"


# --- middleware: 401 + WWW-Authenticate + fallback ------------------------- #

def test_sse_401_has_www_authenticate(client):
    r = client.get("/mcp/sse")  # sem token
    assert r.status_code == 401
    wa = r.headers.get("www-authenticate", "")
    assert 'resource_metadata="https://klarim.net/.well-known/oauth-protected-resource"' in wa


def _scope(path, bearer=None, token=None):
    headers = []
    if bearer:
        headers.append((b"authorization", b"Bearer " + bearer.encode()))
    qs = (f"token={token}").encode() if token else b""
    return {"type": "http", "path": path, "headers": headers, "query_string": qs}


def test_middleware_exempts_oauth_routes(monkeypatch):
    monkeypatch.setenv("MCP_API_KEY", _STATIC)
    mw = MCPAuthMiddleware(None)
    for p in ("/authorize", "/token", "/register"):
        assert mw._check(_scope(p)) is True  # isento, sem token


def test_middleware_accepts_static_and_jwt(monkeypatch):
    monkeypatch.setenv("MCP_API_KEY", _STATIC)
    monkeypatch.setenv("JWT_SECRET", "k" * 64)
    monkeypatch.setenv("MCP_ISSUER", "https://klarim.net")
    monkeypatch.delenv("MCP_JWT_SECRET", raising=False)
    mw = MCPAuthMiddleware(None)
    # sem token → 401
    assert mw._check(_scope("/sse")) is False
    # token estático via Bearer e via ?token=
    assert mw._check(_scope("/sse", bearer=_STATIC)) is True
    assert mw._check(_scope("/sse", token=_STATIC)) is True
    # JWT OAuth via Bearer e via ?token= (propagado ao /messages/)
    jwt_tok = oauth.mint_access_token()
    assert mw._check(_scope("/sse", bearer=jwt_tok)) is True
    assert mw._check(_scope("/messages/", token=jwt_tok)) is True
    # token inválido → 401
    assert mw._check(_scope("/sse", bearer="chave-errada")) is False
    assert mw._check(_scope("/sse", bearer="a.b.c")) is False


def test_middleware_fail_closed_without_config(monkeypatch):
    monkeypatch.delenv("MCP_API_KEY", raising=False)
    monkeypatch.delenv("JWT_SECRET", raising=False)
    monkeypatch.delenv("MCP_JWT_SECRET", raising=False)
    mw = MCPAuthMiddleware(None)
    assert mw._check(_scope("/sse", bearer="qualquer")) is False


# --- PKCE unit ------------------------------------------------------------- #

def test_pkce_s256_verification():
    verifier, challenge = _pkce()
    assert oauth.verify_pkce(verifier, challenge) is True
    assert oauth.verify_pkce("outro", challenge) is False
    assert oauth.verify_pkce("", challenge) is False
