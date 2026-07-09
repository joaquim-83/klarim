"""Testes do servidor MCP (KL-18) — registro de tools, auth, guard e reuso. Offline."""

from __future__ import annotations

import asyncio

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

import api.main as apimain
import mcp_server.server as srv


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
