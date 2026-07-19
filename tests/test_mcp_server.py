"""Testes do servidor MCP (KL-18, modelo Traka) — auth middleware, propagação de
token, registro e execução das tools. Offline."""

from __future__ import annotations

import asyncio

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

import api.main as apimain
from mcp_server._base import mcp, _guard, _store, _api  # noqa: F401
from mcp_server.auth import MCPAuthMiddleware
import mcp_server.server as srv
import mcp_server.tools.targets as targets_tools
import mcp_server.tools.system as system_tools
import mcp_server.tools.scans as scans_tools
import mcp_server.tools.inbox as inbox_tools
import mcp_server.tools.leads as leads_tools


# --- registro das 25 tools ------------------------------------------------- #

READ_TOOLS = [
    "get_system_status", "get_email_health", "get_discovery_status", "get_config",
    "list_targets", "get_target", "get_target_stats", "search_targets",
    "get_site_profile",   # KL-52: perfil comercial extraído (site_profile)
    "list_scans", "get_scan", "get_scan_stats", "list_alerts", "get_alert_stats",
    "list_payments", "get_payment_stats", "get_funnel", "get_rescan_stats",
    "get_analytics_metrics", "get_analytics_funnel",  # KL-83
    "get_lead_scoring_stats",  # KL-85
    # fix MCP: novas tools de dados
    "get_dashboard_stats", "get_enrichment_status", "get_user_accounts", "search_inbox",
    # KL-61: leads
    "list_leads", "get_lead_stats", "get_lead_funnel",
    # KL-62: log unificado de e-mails
    "get_email_log",
    # KL-44: planos & assinaturas
    "get_subscription_stats", "list_subscribers",
    # KL-44 P6: pagamentos de assinatura
    "get_subscription_payment_stats",
    # KL-44 P2: vigílias
    "get_vigilia_stats", "list_vigilia_alerts",
    # KL-44 P4: typosquat/phishing
    "get_typosquat_alerts",
    # KL-44 P5: indicadores de privacidade
    "get_privacy_stats",
    # KL-68: verificação de propriedade
    "get_ownership_stats",
    # KL-44 P3: boletim + técnico
    "get_bulletin_stats", "list_technician_links",
]
WRITE_TOOLS = [
    "scan_url", "add_target", "update_target_email", "update_target_status",
    "update_target_sector", "send_alert_to_target", "send_report_to_email",
    "classify_targets_batch",
    # KL-69: gestão de usuários
    "admin_remove_user_site",
]


def test_all_25_tools_registered():
    names = {t.name for t in asyncio.run(mcp.list_tools())}
    assert len(names) >= 25
    missing = [t for t in READ_TOOLS + WRITE_TOOLS if t not in names]
    assert not missing, f"faltam tools: {missing}"


def test_tools_have_descriptions():
    for t in asyncio.run(mcp.list_tools()):
        assert t.description and len(t.description) > 10


def test_mcp_app_routes():
    paths = [getattr(r, "path", "") for r in srv.mcp_app.routes]
    assert "/sse" in paths and "/messages" in paths


# --- MCPAuthMiddleware ----------------------------------------------------- #

def _scope(headers=None, query=b""):
    return {"type": "http", "headers": headers or [], "query_string": query}


def test_auth_middleware_check(monkeypatch):
    monkeypatch.setenv("MCP_API_KEY", "the-key")
    mw = MCPAuthMiddleware(app=None)
    # Bearer header
    assert mw._check(_scope(headers=[(b"authorization", b"Bearer the-key")])) is True
    # query ?token=
    assert mw._check(_scope(query=b"token=the-key")) is True
    assert mw._check(_scope(query=b"session_id=x&token=the-key")) is True
    # errado / ausente
    assert mw._check(_scope(headers=[(b"authorization", b"Bearer nope")])) is False
    assert mw._check(_scope(query=b"token=nope")) is False
    assert mw._check(_scope()) is False


def test_auth_middleware_fail_closed(monkeypatch):
    monkeypatch.delenv("MCP_API_KEY", raising=False)
    mw = MCPAuthMiddleware(app=None)
    assert mw._check(_scope(query=b"token=whatever")) is False  # sem chave => 401


def test_mcp_sse_401_with_www_authenticate(monkeypatch):
    monkeypatch.setenv("MCP_API_KEY", "the-key")
    c = TestClient(apimain.app, raise_server_exceptions=False)
    r = c.get("/mcp/sse")
    assert r.status_code == 401
    # KL-63: o WWW-Authenticate agora aponta o Protected Resource Metadata (dispara OAuth).
    assert "resource_metadata=" in r.headers.get("www-authenticate", "")
    assert c.get("/mcp/sse", headers={"Authorization": "Bearer wrong"}).status_code == 401
    # POST /messages/ sem token também é barrado pela middleware
    assert c.post("/mcp/messages/?session_id=x").status_code == 401
    assert apimain._is_protected("/mcp/sse") is False  # auth própria, não JWT


# --- propagação de token (o fix que faz o Claude.ai conectar) --------------- #

def test_token_propagation_appends_to_endpoint_event():
    captured = []

    async def fake_send(message):
        captured.append(message)

    wrapped = srv._token_propagating_send(fake_send, "MYTOKEN")
    body = b"event: endpoint\r\ndata: /mcp/messages/?session_id=abc123def456\r\n\r\n"
    asyncio.run(wrapped({"type": "http.response.body", "body": body}))
    out = captured[0]["body"]
    assert b"session_id=abc123def456&token=MYTOKEN" in out


def test_token_propagation_only_first_endpoint_event():
    sent = []

    async def fake_send(m):
        sent.append(m.get("body", b""))

    wrapped = srv._token_propagating_send(fake_send, "T")
    # 2º chunk (resposta de tool) não deve ser alterado
    asyncio.run(wrapped({"type": "http.response.body",
                         "body": b"event: endpoint\r\ndata: /mcp/messages/?session_id=aa11\r\n\r\n"}))
    asyncio.run(wrapped({"type": "http.response.body", "body": b"event: message\r\ndata: {}\r\n\r\n"}))
    assert b"token=T" in sent[0] and b"token=T" not in sent[1]


def test_token_from_scope():
    assert srv._token_from_scope(_scope(headers=[(b"authorization", b"Bearer abc")])) == "abc"
    assert srv._token_from_scope(_scope(query=b"token=xyz")) == "xyz"
    assert srv._token_from_scope(_scope()) == ""


# --- _guard ---------------------------------------------------------------- #

def test_guard_converts_httpexception():
    async def boom():
        raise HTTPException(status_code=404, detail="Não encontrado.")
    assert asyncio.run(_guard(boom)) == {"error": "Não encontrado.", "status_code": 404}


def test_guard_converts_generic_error():
    async def boom():
        raise ValueError("xyz")
    assert asyncio.run(_guard(boom))["error"].startswith("ValueError")


# --- execução de tools (store falso) --------------------------------------- #

class FakeStore:
    def __init__(self):
        self.kw = None

    async def list_targets(self, **kw):
        self.kw = kw
        return [{"id": 1, "url": "https://verdegreen.com.br", "contact_email": None,
                 "status": "sem_contato"}]

    async def count_targets(self, status=None):
        return 1

    async def get_target(self, target_id):
        return None if target_id == 999 else {"id": target_id, "url": "https://x.com.br"}

    async def get_site_profile(self, target_id):
        if target_id == 999:
            return None
        return {"target_id": target_id, "company_name": "Empresa X",
                "maturity_score": 7, "phone": "11999999999"}

    async def get_target_classifications(self, target_id):
        return []

    async def list_scans(self, **kw):
        return [{"id": 10, "score": 80}]

    async def list_alerts(self, **kw):
        return []

    async def list_rescans(self, **kw):
        return []

    async def stats(self):
        return {"by_status": {"sem_contato": 1900}}

    # --- fix MCP: novas tools de dados ---
    async def profile_counts(self):
        return {"total": 3476, "with_description": 3332, "with_cnae": 1027,
                "public_visible": 3474}

    async def scan_stats(self):
        return {"total": 4832, "avg_score": 73, "by_semaphore": {"amarelo": 4000},
                "manual": 52, "automated": 4780, "today": 45, "last_7_days": 312,
                "score_100_count": 86}

    async def dashboard_summary(self):
        return {"targets": {"total": 20432, "by_status": {}, "score_100": 86},
                "scans": {"total": 4832, "manual": 52, "automated": 4780},
                "profiles": {"total": 3476, "with_ai": 3332, "with_cnae": 1027},
                "accounts": {"total": 6, "active": 6, "sites_monitored": 5},
                "alerts": {"total": 1747, "today": 0}}

    async def inbox_unread_count(self):
        return 2

    async def count_enrichment_groups(self, mode="all"):
        return {"group1": 10, "group2": 20, "group3": 30, "group4": 40, "total": 100}

    async def count_unscanned_targets(self, status="sem_contato"):
        return 7400

    async def list_users_with_sites(self):
        return [{"id": 1, "email": "a@x.com.br", "is_active": True,
                 "sites": [{"target_id": 9}]}]

    async def list_inbox_messages(self, box="all", limit=25, offset=0, source=None, search=None):
        self.kw = {"box": box, "source": source, "search": search}
        return [{"id": 1, "subject": "oi", "source": source or "webhook"}]

    # --- KL-61: leads ---
    async def list_leads(self, **kw):
        self.kw = kw
        return {"leads": [{"id": 1, "email": "dono@empresa.com.br", "classification": "hot",
                           "lead_score": 45}],
                "total": 1, "by_classification": {"cold": 3, "warm": 2, "hot": 1, "pql": 0}}

    async def lead_stats(self):
        return {"total": 6, "by_classification": {"cold": 3, "warm": 2, "hot": 1, "pql": 0},
                "with_account": 2, "with_monitoring": 1, "avg_lead_score": 30,
                "corporate_emails": 4, "multi_scan": 1, "top_sectors": [],
                "today": 1, "last_7_days": 4, "conversion_by_sector": [],
                "pain_sectors": [{"sector": "hotel", "avg_worst_score": 42}], "pql_rate": 0.0}

    async def lead_funnel(self):
        return {"email_verified": 6, "scan_completed": 6, "account_created": 2,
                "monitoring_added": 1, "conversion_rate_scan_to_account": 33.3,
                "conversion_rate_account_to_monitoring": 50.0}


@pytest.fixture
def fake_store(monkeypatch):
    store = FakeStore()
    # o helper _store() (em _base) e o get_target_store do api.main
    monkeypatch.setattr("mcp_server._base.get_target_store", lambda: store)
    monkeypatch.setattr(apimain, "get_target_store", lambda: store)
    return store


def test_list_targets_tool(fake_store):
    res = asyncio.run(targets_tools.list_targets(status="sem_contato", limit=5))
    assert res["total"] == 1 and len(res["targets"]) == 1
    assert fake_store.kw["status"] == "sem_contato" and fake_store.kw["limit"] == 5


def test_search_targets_tool(fake_store):
    res = asyncio.run(targets_tools.search_targets("verde"))
    assert res["count"] == 1 and fake_store.kw["search"] == "verde"


def test_get_target_tool_aggregates(fake_store):
    res = asyncio.run(targets_tools.get_target(3))
    assert res["target"]["id"] == 3
    assert "recent_scans" in res and "alerts" in res and "rescans" in res


def test_get_target_tool_not_found(fake_store):
    assert asyncio.run(targets_tools.get_target(999)).get("error")


def test_get_site_profile_tool(fake_store):
    # KL-52: perfil comercial completo para um target com profile.
    res = asyncio.run(targets_tools.get_site_profile(3))
    assert res["company_name"] == "Empresa X" and res["maturity_score"] == 7


def test_get_site_profile_tool_not_found(fake_store):
    # KL-52: erro quando o target não tem profile.
    assert asyncio.run(targets_tools.get_site_profile(999)).get("error")


def test_get_target_stats_tool(fake_store):
    res = asyncio.run(targets_tools.get_target_stats())
    assert res["by_status"]["sem_contato"] == 1900
    # fix MCP: agora inclui contagem de perfis
    assert res["profiles"]["total"] == 3476 and res["profiles"]["with_cnae"] == 1027


def test_get_scan_stats_tool_manual_vs_auto(fake_store):
    res = asyncio.run(scans_tools.get_scan_stats())
    assert res["manual"] == 52 and res["automated"] == 4780
    assert res["today"] == 45 and res["score_100_count"] == 86


def test_get_dashboard_stats_tool(fake_store):
    res = asyncio.run(system_tools.get_dashboard_stats())
    for key in ("targets", "scans", "profiles", "accounts", "alerts", "inbox"):
        assert key in res
    assert res["inbox"]["unread"] == 2 and res["scans"]["manual"] == 52


def test_get_enrichment_status_tool(fake_store):
    res = asyncio.run(system_tools.get_enrichment_status())
    assert res["backlog"]["g1_no_profile"] == 10 and res["backlog"]["total"] == 100
    assert res["unscanned_sem_contato"] == 7400


def test_get_user_accounts_tool(fake_store):
    res = asyncio.run(system_tools.get_user_accounts())
    assert res["total"] == 1 and res["active"] == 1 and res["total_sites"] == 1


def test_search_inbox_tool(fake_store):
    res = asyncio.run(inbox_tools.search_inbox(query="oi", source="contact_form",
                                               unread_only=True))
    assert res["count"] == 1 and res["unread_total"] == 2
    assert fake_store.kw["search"] == "oi" and fake_store.kw["source"] == "contact_form"
    assert fake_store.kw["box"] == "unread"


def test_list_leads_tool(fake_store):
    res = asyncio.run(leads_tools.list_leads(classification="hot", search="empresa"))
    assert res["total"] == 1 and len(res["leads"]) == 1
    assert fake_store.kw["classification"] == "hot" and fake_store.kw["search"] == "empresa"


def test_list_leads_tool_bad_classification_ignored(fake_store):
    asyncio.run(leads_tools.list_leads(classification="banana"))
    assert fake_store.kw["classification"] is None


def test_get_lead_stats_tool(fake_store):
    res = asyncio.run(leads_tools.get_lead_stats())
    assert res["total"] == 6 and res["with_account"] == 2
    assert res["pain_sectors"][0]["sector"] == "hotel"


def test_get_lead_funnel_tool(fake_store):
    res = asyncio.run(leads_tools.get_lead_funnel())
    assert res["email_verified"] == 6 and res["account_created"] == 2


def test_update_target_status_tool_invalid(fake_store, monkeypatch):
    # status inválido -> api_target_update_status levanta 422 -> _guard converte
    res = asyncio.run(targets_tools.update_target_status(5, "banana"))
    assert res.get("status_code") == 422
