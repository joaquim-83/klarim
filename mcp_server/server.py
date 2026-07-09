"""Servidor MCP do Klarim (KL-18).

Wrapper **fino** sobre a API existente: cada tool mapeia para um ou mais
endpoints/métodos já implementados (nenhuma lógica de negócio é duplicada aqui).
O servidor é montado no mesmo FastAPI (mesmo processo/porta) via SSE em
`/mcp/sse` — ver `mount_mcp()`. Autenticação por `MCP_API_KEY` no header
`Authorization: Bearer <chave>`.

As funções de endpoint do `api.main` são importadas **lazily** dentro das tools
para evitar import circular (o `api.main` importa `mount_mcp` deste módulo).
"""

from __future__ import annotations

import hmac
import os
from typing import Optional

from fastapi import HTTPException
from mcp.server.fastmcp import FastMCP
from mcp.server.sse import SseServerTransport

from discovery.store import get_target_store

mcp = FastMCP(
    name="klarim",
    instructions=(
        "Klarim é um scanner passivo de segurança web para PMEs brasileiras. "
        "Use estas tools para monitorar o sistema, gerenciar alvos, disparar scans "
        "e alertas. Alvos com status 'sem_contato' precisam de e-mail para entrar "
        "no pipeline de alertas: use update_target_email — ao ganhar e-mail o alvo "
        "volta a 'discovered' automaticamente e pode ser escaneado/alertado. "
        "Todas as ações de escrita são operadas pelo dono do Klarim."
    ),
)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def _api():
    """Import tardio do api.main (evita ciclo de import)."""
    from api import main as _m
    return _m


def _store():
    return get_target_store()


async def _guard(make_coro):
    """Executa a coroutine e converte exceções num dict de erro amigável para o
    operador (em vez de estourar a tool). `make_coro` é um callable sem args."""
    try:
        return await make_coro()
    except HTTPException as exc:
        return {"error": str(exc.detail), "status_code": exc.status_code}
    except Exception as exc:  # noqa: BLE001 - a tool nunca deve derrubar a sessão
        return {"error": f"{type(exc).__name__}: {exc}"}


# --------------------------------------------------------------------------- #
# Tools de leitura
# --------------------------------------------------------------------------- #

@mcp.tool()
async def get_system_status() -> dict:
    """Status completo do sistema: workers (alive/dead + últimos ciclos),
    dependências (postgres/redis/ct_logs/resend/abacatepay), métricas de e-mail
    (enviados hoje/mês, cota mensal) e backlog de alertas."""
    return await _guard(lambda: _api().api_system_status())


@mcp.tool()
async def get_email_health() -> dict:
    """Saúde de e-mail: bounce rate, bounces permanentes, complaints, tamanho da
    blocklist e status de risco (ok/warning/critical). Vital para não estourar a
    reputação do domínio no Resend/Gmail."""
    return await _guard(lambda: _api().api_system_email_health())


@mcp.tool()
async def get_discovery_status() -> dict:
    """Status do Discovery Worker: CT poller conectado, certificados vistos,
    domínios .com.br filtrados, buffer atual e alvos descobertos hoje."""
    return await _guard(lambda: _api().api_discovery_status())


@mcp.tool()
async def get_config() -> dict:
    """Configuração operacional em uso: batch size, intervalos, limites mensais,
    timeouts, etc. (somente leitura, sem segredos)."""
    return await _guard(lambda: _api().api_config())


@mcp.tool()
async def list_targets(
    status: Optional[str] = None,
    platform: Optional[str] = None,
    sector: Optional[str] = None,
    search: Optional[str] = None,
    limit: int = 20,
    offset: int = 0,
) -> dict:
    """Lista alvos com filtros opcionais. status: discovered, scanned, alerted,
    converted, sem_contato, descartado, unsubscribed. `search` casa parcialmente
    (case-insensitive) em URL, domínio e e-mail."""
    async def _impl():
        store = _store()
        targets = await store.list_targets(
            status=status, platform=platform, sector=sector, search=search,
            limit=min(limit, 200), offset=offset)
        total = await store.count_targets(status=status)
        return {"targets": targets, "total": total, "limit": limit, "offset": offset}

    return await _guard(_impl)


@mcp.tool()
async def get_target(target_id: int) -> dict:
    """Detalhe completo de um alvo: dados, últimos scans, histórico de alertas e
    de re-scans."""
    async def _impl():
        store = _store()
        target = await store.get_target(target_id)
        if target is None:
            return {"error": "Alvo não encontrado."}
        return {
            "target": target,
            "recent_scans": await store.list_scans(target_id=target_id, limit=10),
            "alerts": await store.list_alerts(target_id=target_id, limit=10),
            "rescans": await store.list_rescans(target_id=target_id, limit=10),
        }

    return await _guard(_impl)


@mcp.tool()
async def get_target_stats() -> dict:
    """Contagem de alvos por status, plataforma e setor."""
    return await _guard(lambda: _store().stats())


@mcp.tool()
async def search_targets(query: str) -> dict:
    """Busca alvos por URL, domínio ou e-mail (parcial, case-insensitive). Até 20
    resultados. Útil para reaproveitar alvos existentes."""
    async def _impl():
        rows = await _store().list_targets(search=query, limit=20)
        return {"query": query, "count": len(rows), "targets": rows}

    return await _guard(_impl)


@mcp.tool()
async def list_scans(limit: int = 20, offset: int = 0) -> dict:
    """Lista scans recentes com score, semáforo, contagens e data."""
    async def _impl():
        rows = await _store().list_scans(limit=min(limit + offset, 500))
        return {"scans": rows[offset:offset + limit], "limit": limit, "offset": offset}

    return await _guard(_impl)


@mcp.tool()
async def get_scan(scan_id: int) -> dict:
    """Detalhe de um scan: todos os checks com PASS/FAIL/INCONCLUSO e evidence."""
    return await _guard(lambda: _api().api_get_scan(scan_id))


@mcp.tool()
async def get_scan_stats() -> dict:
    """Estatísticas de scans: total, score médio, distribuição por semáforo."""
    return await _guard(lambda: _store().scan_stats())


@mcp.tool()
async def list_alerts(limit: int = 20, offset: int = 0) -> dict:
    """Histórico de alertas enviados com e-mail, score, data, status e email_id."""
    async def _impl():
        rows = await _store().list_alerts(limit=min(limit, 200), offset=offset)
        return {"alerts": rows, "limit": limit, "offset": offset}

    return await _guard(_impl)


@mcp.tool()
async def get_alert_stats() -> dict:
    """Contagem de alertas enviados: hoje, semana, mês e total."""
    return await _guard(lambda: _store().alert_stats())


@mcp.tool()
async def list_payments(limit: int = 20) -> dict:
    """Lista de pagamentos com charge_id, URL, valor, status, alvo e data."""
    return await _guard(lambda: _api().api_payments_list(
        status=None, limit=min(limit, 200), offset=0))


@mcp.tool()
async def get_payment_stats() -> dict:
    """Receita total, contagem por status e ticket médio."""
    return await _guard(lambda: _api().api_payments_stats())


@mcp.tool()
async def get_funnel(period: str = "7d") -> dict:
    """Funil de conversão: e-mails enviados → cliques → resultado visto → CTA →
    PIX → pago → PDF baixado. Períodos: today, 7d, 30d, total."""
    return await _guard(lambda: _api().api_analytics_funnel(period))


@mcp.tool()
async def get_rescan_stats() -> dict:
    """Estatísticas de re-scans: improved, worsened, unchanged, first_rescan."""
    return await _guard(lambda: _store().rescan_stats())


# --------------------------------------------------------------------------- #
# Tools de escrita
# --------------------------------------------------------------------------- #

@mcp.tool()
async def scan_url(url: str) -> dict:
    """Escaneia uma URL e retorna o resultado completo: score, semáforo, todos os
    checks, riscos, plataforma, setor e e-mail extraído. Registra automaticamente
    no banco (source='admin'). Não envia e-mail."""
    m = _api()
    return await _guard(lambda: m.api_admin_scan_and_report(m.ScanAndReportBody(url=url)))


@mcp.tool()
async def add_target(url: str) -> dict:
    """Adiciona uma URL como alvo: fetch + fingerprint + extrai e-mail (com MX) +
    classifica setor e enfileira para scan."""
    m = _api()
    return await _guard(lambda: m.api_targets_add(m.TargetAddBody(url=url)))


@mcp.tool()
async def update_target_email(target_id: int, email: str) -> dict:
    """Atualiza o e-mail de contato de um alvo. Se estava em 'sem_contato', volta a
    'discovered' automaticamente e entra no pipeline de alertas."""
    m = _api()
    return await _guard(lambda: m.api_target_update_email(target_id, m.EmailBody(contact_email=email)))


@mcp.tool()
async def update_target_status(target_id: int, status: str) -> dict:
    """Altera o status de um alvo. Valores: discovered, scanned, alerted,
    converted, sem_contato, descartado, unsubscribed."""
    m = _api()
    return await _guard(lambda: m.api_target_update_status(target_id, m.StatusBody(status=status)))


@mcp.tool()
async def update_target_sector(target_id: int, sector: str) -> dict:
    """Classifica manualmente o setor de um alvo (source='manual', confiança 1.0).
    Valores: hotel, clinica, escola, ecommerce, condominio, juridico,
    contabilidade, restaurante, imobiliaria, automotivo, outro."""
    m = _api()
    return await _guard(lambda: m.api_target_classify(target_id, m.ClassifyBody(sector=sector)))


@mcp.tool()
async def send_alert_to_target(target_id: int) -> dict:
    """Dispara o alerta de segurança por e-mail para um alvo (ignora a cota — ação
    manual). Requer contact_email e que o alvo não esteja unsubscribed."""
    return await _guard(lambda: _api().api_target_alert(target_id))


@mcp.tool()
async def send_report_to_email(target_url: str, email: str) -> dict:
    """Escaneia a URL e envia o relatório completo (executivo + técnico em PDF)
    para um e-mail específico. Uso admin — não exige pagamento."""
    m = _api()
    return await _guard(lambda: m.api_admin_scan_and_report(m.ScanAndReportBody(
        url=target_url, send_email=True, email_to=email, email_type="report")))


@mcp.tool()
async def classify_targets_batch(target_ids: list[int], sector: str) -> dict:
    """Classifica múltiplos alvos de uma vez para o mesmo setor. Retorna quantos
    foram atualizados."""
    m = _api()
    return await _guard(lambda: m.api_classify_batch(
        m.ClassifyBatchBody(target_ids=target_ids, sector=sector)))


# --------------------------------------------------------------------------- #
# Montagem no FastAPI (SSE) + autenticação
# --------------------------------------------------------------------------- #

def _key_ok(auth_header: str) -> bool:
    """Valida o header Authorization contra MCP_API_KEY (constant-time).
    Sem MCP_API_KEY configurada, o MCP fica desligado (tudo 401)."""
    expected = os.environ.get("MCP_API_KEY", "")
    if not expected:
        return False
    h = auth_header or ""
    token = h[7:].strip() if h[:7].lower() == "bearer " else h.strip()
    return bool(token) and hmac.compare_digest(token, expected)


def mount_mcp(app) -> None:
    """Monta o transporte SSE do MCP no FastAPI, em `/mcp/sse` (+ `/mcp/messages/`).

    Cada requisição é autenticada por MCP_API_KEY. Segue o padrão canônico do
    FastMCP (connect_sse + `_mcp_server.run`).
    """
    from starlette.applications import Starlette
    from starlette.requests import Request
    from starlette.responses import JSONResponse, Response
    from starlette.routing import Mount, Route

    # Caminho RELATIVO ao sub-app montado em /mcp — o transporte prefixa o
    # root_path (/mcp) automaticamente, resultando em /mcp/messages/ para o cliente.
    transport = SseServerTransport("/messages/")

    async def sse_endpoint(request: Request) -> Response:
        if not _key_ok(request.headers.get("authorization", "")):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        async with transport.connect_sse(
            request.scope, request.receive, request._send,
        ) as streams:
            await mcp._mcp_server.run(
                streams[0], streams[1],
                mcp._mcp_server.create_initialization_options(),
            )
        return Response()

    async def messages_asgi(scope, receive, send) -> None:
        if not _key_ok(Request(scope).headers.get("authorization", "")):
            await JSONResponse({"error": "unauthorized"}, status_code=401)(scope, receive, send)
            return
        await transport.handle_post_message(scope, receive, send)

    sse_app = Starlette(routes=[
        Route("/sse", endpoint=sse_endpoint, methods=["GET"]),
        Route("/sse/", endpoint=sse_endpoint, methods=["GET"]),
        Mount("/messages/", app=messages_asgi),
    ])
    app.mount("/mcp", sse_app)
