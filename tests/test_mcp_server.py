"""Testes do servidor MCP (KL-18) — registro de tools, auth, guard e reuso. Offline."""

from __future__ import annotations

import asyncio
import time

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

import api.main as apimain
import mcp_server.server as srv


class _Req:
    """Stub mínimo de request para testar _authorized (headers/query dict)."""
    def __init__(self, headers=None, query=None):
        self.headers = headers or {}
        self.query_params = query or {}


# --- registro de tools ----------------------------------------------------- #

READ_TOOLS = [
    "get_system_status", "get_email_health", "get_discovery_status", "get_config",
    "list_targets", "get_target", "get_target_stats", "search_targets",
    "list_scans", "get_scan", "get_scan_stats", "list_alerts", "get_alert_stats",
    "list_payments", "get_payment_stats", "get_funnel", "get_rescan_stats",
]
WRITE_TOOLS = [
    "scan_url", "add_target", "update_target_email", "update_target_status",
    "update_target_sector", "send_alert_to_target", "send_report_to_email",
    "classify_targets_batch",
]


def test_all_tools_registered():
    names = {t.name for t in asyncio.run(srv.mcp.list_tools())}
    assert len(names) >= 25
    missing = [t for t in READ_TOOLS + WRITE_TOOLS if t not in names]
    assert not missing, f"faltam tools: {missing}"


def test_tools_have_descriptions():
    for t in asyncio.run(srv.mcp.list_tools()):
        assert t.description and len(t.description) > 10


# --- autenticação ---------------------------------------------------------- #

def test_key_ok(monkeypatch):
    monkeypatch.setenv("MCP_API_KEY", "s3cr3t")
    assert srv._key_ok("Bearer s3cr3t") is True
    assert srv._key_ok("s3cr3t") is True            # sem prefixo Bearer também vale
    assert srv._key_ok("Bearer errado") is False
    assert srv._key_ok("") is False


def test_key_ok_disabled_without_env(monkeypatch):
    monkeypatch.delenv("MCP_API_KEY", raising=False)
    assert srv._key_ok("Bearer qualquer") is False   # sem chave => MCP desligado


def test_mcp_sse_requires_auth(monkeypatch):
    monkeypatch.setenv("MCP_API_KEY", "s3cr3t")
    c = TestClient(apimain.app, raise_server_exceptions=False)
    assert c.get("/mcp/sse").status_code == 401                        # sem header
    assert c.get("/mcp/sse", headers={"Authorization": "Bearer x"}).status_code == 401
    assert apimain._is_protected("/mcp/sse") is False                  # não é rota admin


# --- _guard ---------------------------------------------------------------- #

def test_guard_converts_httpexception():
    async def boom():
        raise HTTPException(status_code=404, detail="Alvo não encontrado.")
    assert asyncio.run(srv._guard(boom)) == {"error": "Alvo não encontrado.", "status_code": 404}


def test_guard_converts_generic_error():
    async def boom():
        raise ValueError("xyz")
    res = asyncio.run(srv._guard(boom))
    assert res["error"].startswith("ValueError")


# --- execução de tools (store falso) --------------------------------------- #

class FakeStore:
    def __init__(self):
        self.kw = None

    async def list_targets(self, **kw):
        self.kw = kw
        return [{"id": 1, "url": "https://verdegreen.com.br", "domain": "verdegreen.com.br",
                 "contact_email": None, "status": "sem_contato"}]

    async def count_targets(self, status=None):
        return 1

    async def get_target(self, target_id):
        return None if target_id == 999 else {"id": target_id, "url": "https://x.com.br"}

    async def list_scans(self, **kw):
        return [{"id": 10, "score": 80}]

    async def list_alerts(self, **kw):
        return []

    async def list_rescans(self, **kw):
        return []

    async def stats(self):
        return {"by_status": {"sem_contato": 1900}}

    async def update_target_status(self, target_id, status):
        return None if target_id == 999 else {"id": target_id, "status": status}


@pytest.fixture
def fake_store(monkeypatch):
    store = FakeStore()
    monkeypatch.setattr(srv, "get_target_store", lambda: store)
    monkeypatch.setattr(apimain, "get_target_store", lambda: store)
    return store


def test_list_targets_tool(fake_store):
    res = asyncio.run(srv.list_targets(status="sem_contato", limit=5))
    assert res["total"] == 1 and len(res["targets"]) == 1
    assert fake_store.kw["status"] == "sem_contato" and fake_store.kw["limit"] == 5


def test_search_targets_tool(fake_store):
    res = asyncio.run(srv.search_targets("verde"))
    assert res["count"] == 1 and fake_store.kw["search"] == "verde"


def test_get_target_tool_aggregates(fake_store):
    res = asyncio.run(srv.get_target(3))
    assert res["target"]["id"] == 3
    assert "recent_scans" in res and "alerts" in res and "rescans" in res


def test_get_target_tool_not_found(fake_store):
    assert asyncio.run(srv.get_target(999)).get("error")


def test_get_target_stats_tool(fake_store):
    assert asyncio.run(srv.get_target_stats())["by_status"]["sem_contato"] == 1900


def test_update_target_status_tool_valid(fake_store):
    res = asyncio.run(srv.update_target_status(5, "scanned"))
    assert res["status"] == "scanned"


def test_update_target_status_tool_invalid(fake_store):
    # status inválido -> api_target_update_status levanta 422 -> _guard converte
    res = asyncio.run(srv.update_target_status(5, "banana"))
    assert res.get("status_code") == 422


# --- fluxo de auth web (Fix KL-18) ----------------------------------------- #

def test_authorized_accepts_key_and_session(monkeypatch):
    monkeypatch.setenv("MCP_API_KEY", "the-key")
    srv._mcp_sessions.clear()
    assert srv._authorized(_Req(headers={"authorization": "Bearer the-key"})) is True
    assert srv._authorized(_Req(query={"token": "the-key"})) is True          # key via ?token=
    tok = srv._new_session()
    assert srv._authorized(_Req(query={"token": tok})) is True                # session via query
    assert srv._authorized(_Req(headers={"authorization": f"Bearer {tok}"})) is True
    assert srv._authorized(_Req(query={"token": "nope"})) is False
    assert srv._authorized(_Req()) is False                                    # sem token


def test_authorized_disabled_without_env(monkeypatch):
    monkeypatch.delenv("MCP_API_KEY", raising=False)
    assert srv._authorized(_Req(headers={"authorization": "Bearer x"})) is False


def test_session_lifecycle():
    srv._mcp_sessions.clear()
    tok = srv._new_session()
    assert len(tok) == 64 and srv._valid_session(tok) is True      # 256 bits hex
    srv._mcp_sessions[tok] = time.time() - srv.MCP_SESSION_TTL - 10  # força expiração
    assert srv._valid_session(tok) is False
    assert tok not in srv._mcp_sessions                            # removida ao validar


def test_safe_callback():
    assert srv._safe_callback("http://localhost:8765/cb") is True
    assert srv._safe_callback("http://127.0.0.1:9/cb") is True
    assert srv._safe_callback("https://claude.ai/x") is True
    assert srv._safe_callback("https://foo.anthropic.com/x") is True
    assert srv._safe_callback("https://evil.com/steal") is False   # open-redirect barrado
    assert srv._safe_callback("javascript:alert(1)") is False
    assert srv._safe_callback("") is False


def test_auth_page_renders(monkeypatch):
    monkeypatch.setenv("MCP_API_KEY", "the-key")
    c = TestClient(apimain.app, raise_server_exceptions=False)
    r = c.get("/mcp/auth")
    assert r.status_code == 200
    assert 'type="password"' in r.text and "API Key" in r.text
    assert "default-src 'none'" in r.headers.get("content-security-policy", "")
    assert apimain._is_protected("/mcp/auth") is False


def test_auth_verify_wrong_key(monkeypatch):
    monkeypatch.setenv("MCP_API_KEY", "correct-key")
    srv._verify_attempts.clear()
    c = TestClient(apimain.app, raise_server_exceptions=False)
    r = c.post("/mcp/auth/verify", data={"api_key": "wrong", "callback_url": ""})
    assert r.status_code == 401 and "inválida" in r.text


def test_auth_verify_right_key_creates_session(monkeypatch):
    monkeypatch.setenv("MCP_API_KEY", "correct-key")
    srv._verify_attempts.clear()
    srv._mcp_sessions.clear()
    c = TestClient(apimain.app, raise_server_exceptions=False)
    r = c.post("/mcp/auth/verify", data={"api_key": "correct-key", "callback_url": ""})
    assert r.status_code == 200 and "/mcp/sse?token=" in r.text
    assert len(srv._mcp_sessions) == 1


def test_auth_verify_safe_callback_redirects(monkeypatch):
    monkeypatch.setenv("MCP_API_KEY", "correct-key")
    srv._verify_attempts.clear()
    c = TestClient(apimain.app, raise_server_exceptions=False)
    r = c.post("/mcp/auth/verify",
               data={"api_key": "correct-key", "callback_url": "http://localhost:9999/cb"},
               follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["location"].startswith("http://localhost:9999/cb?token=")


def test_auth_verify_rejects_unsafe_callback(monkeypatch):
    # callback externo NÃO redireciona (anti open-redirect) — mostra o token na página
    monkeypatch.setenv("MCP_API_KEY", "correct-key")
    srv._verify_attempts.clear()
    c = TestClient(apimain.app, raise_server_exceptions=False)
    r = c.post("/mcp/auth/verify",
               data={"api_key": "correct-key", "callback_url": "https://evil.com/steal"},
               follow_redirects=False)
    assert r.status_code == 200 and "evil.com" not in r.text


def test_auth_verify_rate_limited(monkeypatch):
    monkeypatch.setenv("MCP_API_KEY", "correct-key")
    srv._verify_attempts.clear()
    c = TestClient(apimain.app, raise_server_exceptions=False)
    codes = [c.post("/mcp/auth/verify", data={"api_key": "wrong"},
                    headers={"X-Real-IP": "5.5.5.5"}).status_code for _ in range(6)]
    assert codes[:5] == [401] * 5 and codes[5] == 429
