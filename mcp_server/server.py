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

import contextlib
import hmac
import os
import secrets
import time
from html import escape
from typing import Optional
from urllib.parse import urlparse

from fastapi import HTTPException
from mcp.server.fastmcp import FastMCP
from mcp.server.sse import SseServerTransport

from discovery.store import get_target_store

# stateless_http=True: cada request do Streamable HTTP é independente (sem estado
# de sessão persistido) — combina com deploy simples atrás de proxy e é o que o
# Claude Desktop usa.
mcp = FastMCP(
    name="klarim",
    stateless_http=True,
    instructions=(
        "Klarim é um scanner passivo de segurança web para PMEs brasileiras. "
        "Use estas tools para monitorar o sistema, gerenciar alvos, disparar scans "
        "e alertas. Alvos com status 'sem_contato' precisam de e-mail para entrar "
        "no pipeline de alertas: use update_target_email — ao ganhar e-mail o alvo "
        "volta a 'discovered' automaticamente e pode ser escaneado/alertado. "
        "Todas as ações de escrita são operadas pelo dono do Klarim."
    ),
)

# Desliga a proteção de DNS rebinding do transporte (Streamable HTTP + SSE). O
# default só permite Host localhost/127.0.0.1 — atrás do Nginx o Host é
# `klarim.net`/`painel.klarim.net` e seria rejeitado ("Invalid Host header").
# É seguro: estamos atrás do Nginx e a auth é por MCP_API_KEY/session token.
from mcp.server.transport_security import TransportSecuritySettings  # noqa: E402

mcp.settings.transport_security = TransportSecuritySettings(
    enable_dns_rebinding_protection=False)


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

# Sessões MCP (in-memory) criadas pelo fluxo web de auth: token efêmero (256 bits)
# que substitui a API key nas conexões do Claude, com TTL de 24h. A **API key
# nunca** viaja em URL — só o session token (revogável/expirável) vai em ?token=.
MCP_SESSION_TTL = 24 * 3600
_mcp_sessions: dict[str, float] = {}  # session_token -> created_at (epoch)

# Rate limit da verificação de API key (anti brute-force), 5/min por IP.
_VERIFY_RL_MAX = 5
_VERIFY_RL_WINDOW = 60
_verify_attempts: dict[str, list[float]] = {}


def _cleanup_sessions() -> None:
    """Remove sessões MCP expiradas (>24h)."""
    now = time.time()
    for t in [t for t, created in _mcp_sessions.items() if now - created > MCP_SESSION_TTL]:
        _mcp_sessions.pop(t, None)


def _new_session() -> str:
    _cleanup_sessions()
    token = secrets.token_hex(32)  # 256 bits, CSPRNG
    _mcp_sessions[token] = time.time()
    return token


def _valid_session(token: str) -> bool:
    created = _mcp_sessions.get(token)
    if created is None:
        return False
    if time.time() - created > MCP_SESSION_TTL:
        _mcp_sessions.pop(token, None)  # expirada
        return False
    return True


def _key_ok(auth_header: str) -> bool:
    """Valida um header Authorization direto contra MCP_API_KEY (constant-time).
    Sem MCP_API_KEY configurada, o MCP fica desligado (tudo 401)."""
    expected = os.environ.get("MCP_API_KEY", "")
    if not expected:
        return False
    h = auth_header or ""
    token = h[7:].strip() if h[:7].lower() == "bearer " else h.strip()
    return bool(token) and hmac.compare_digest(token, expected)


def _extract_token(request) -> str:
    """Token de auth do request: header Authorization: Bearer <x> ou ?token=<x>."""
    h = request.headers.get("authorization", "") or ""
    token = h[7:].strip() if h[:7].lower() == "bearer " else h.strip()
    if not token:
        token = (request.query_params.get("token") or "").strip()
    return token


def _authorized(request) -> bool:
    """Autoriza uma conexão MCP. Aceita (constant-time): a **API key** direta OU um
    **session token** válido (do fluxo web), no header Bearer ou em ?token=.
    Sem MCP_API_KEY ⇒ MCP desligado (nunca autoriza)."""
    expected = os.environ.get("MCP_API_KEY", "")
    if not expected:
        return False
    token = _extract_token(request)
    if not token:
        return False
    if hmac.compare_digest(token, expected):
        return True
    return _valid_session(token)


def _verify_rl_ok(request) -> bool:
    """Rate limit da verificação de API key: 5/min por IP (janela deslizante)."""
    xri = request.headers.get("x-real-ip", "")
    ip = xri.split(",")[0].strip() if xri else (request.client.host if request.client else "unknown")
    now = time.time()
    q = _verify_attempts.setdefault(ip, [])
    cutoff = now - _VERIFY_RL_WINDOW
    while q and q[0] < cutoff:
        q.pop(0)
    if len(q) >= _VERIFY_RL_MAX:
        return False
    q.append(now)
    return True


def _safe_callback(url: str) -> bool:
    """Anti open-redirect: só redireciona o session token para destinos confiáveis
    (localhost do Claude Desktop OU domínios da Anthropic). Qualquer outro ⇒ ignora
    o callback e mostra o token na página (o usuário copia manualmente)."""
    if not url:
        return False
    try:
        p = urlparse(url)
    except Exception:  # noqa: BLE001
        return False
    if p.scheme not in ("http", "https"):
        return False
    host = (p.hostname or "").lower()
    return (host in ("localhost", "127.0.0.1")
            or host == "claude.ai" or host.endswith(".claude.ai")
            or host == "anthropic.com" or host.endswith(".anthropic.com"))


# CSP restritivo para as páginas de auth (sem JS; só o próprio formulário).
_AUTH_CSP = "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; base-uri 'none'"


def _auth_page_html(callback_url: str = "", error: str = "") -> str:
    """Página de autenticação do MCP (HTML puro, sem React). Campo password."""
    err_html = (f'<p style="color:#FF4D4D;font-size:14px;margin:8px 0">{escape(error)}</p>'
                if error else "")
    return f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Klarim MCP — Autenticação</title>
</head>
<body style="margin:0;background:#0D1117;color:#E6EDF3;font-family:Arial,Helvetica,sans-serif;display:flex;justify-content:center;align-items:center;min-height:100vh">
  <div style="max-width:400px;width:90%;text-align:center;padding:24px">
    <h1 style="letter-spacing:2px;font-size:28px">KLA<span style="color:#FF6B35">R</span>IM</h1>
    <p style="color:#8B949E;font-size:15px;line-height:1.5">Cole sua <b>API Key</b> para conectar o Klarim ao Claude via MCP.</p>
    {err_html}
    <form method="POST" action="/mcp/auth/verify" autocomplete="off">
      <input type="hidden" name="callback_url" value="{escape(callback_url)}">
      <input type="password" name="api_key" placeholder="Cole sua API Key aqui" required autofocus
             style="width:100%;box-sizing:border-box;padding:12px;border:1px solid #30363D;border-radius:6px;background:#161B22;color:#E6EDF3;margin:16px 0;font-size:14px">
      <button type="submit"
              style="width:100%;padding:12px;background:#FF6B35;color:#0D1117;border:none;border-radius:6px;cursor:pointer;font-weight:bold;font-size:15px">
        Conectar
      </button>
    </form>
    <p style="color:#484F58;font-size:12px;margin-top:24px">Conexão segura (HTTPS). Sua API Key não é registrada em logs.</p>
  </div>
</body>
</html>"""


def _auth_success_html(connect_url: str, host: str, token: str) -> str:
    """Página de sucesso: URL pronta (com session token) para o Claude Desktop.
    Streamable HTTP (`/mcp/`) é o primário; SSE (`/mcp/sse`) fica como alternativa."""
    sse_url = f"https://{host}/mcp/sse?token={token}"
    return f"""<!DOCTYPE html>
<html lang="pt-BR">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Klarim MCP — Conectado</title></head>
<body style="margin:0;background:#0D1117;color:#E6EDF3;font-family:Arial,Helvetica,sans-serif;display:flex;justify-content:center;align-items:center;min-height:100vh">
  <div style="max-width:560px;width:90%;text-align:center;padding:24px">
    <h1 style="color:#00D26A;font-size:24px">✅ Conectado</h1>
    <p style="color:#8B949E;font-size:15px;line-height:1.5">Cole esta URL no conector personalizado do Claude Desktop
    (válida por 24h):</p>
    <code style="display:block;word-break:break-all;background:#161B22;border:1px solid #30363D;border-radius:6px;padding:12px;margin:16px 0;color:#FF6B35;font-size:13px">{escape(connect_url)}</code>
    <p style="color:#8B949E;font-size:12px;line-height:1.5;margin-top:20px">Alternativa (SSE, para clients legados):</p>
    <code style="display:block;word-break:break-all;background:#161B22;border:1px solid #30363D;border-radius:6px;padding:10px;margin:8px 0;color:#8B949E;font-size:12px">{escape(sse_url)}</code>
    <p style="color:#484F58;font-size:12px;margin-top:16px">Este é um token de sessão temporário — não é a sua API Key.</p>
  </div>
</body>
</html>"""


# Session manager do Streamable HTTP (criado em mount_mcp). Precisa rodar dentro
# de um contexto async — ativado no lifespan do FastAPI via `lifespan_cm()`.
_session_manager = None


@contextlib.asynccontextmanager
async def lifespan_cm():
    """Contexto do session manager do Streamable HTTP para o lifespan do FastAPI.
    No-op se o Streamable HTTP não foi montado (ex.: pacote `mcp` ausente)."""
    if _session_manager is None:
        yield
        return
    async with _session_manager.run():
        yield


def mount_mcp(app) -> None:
    """Monta o MCP no FastAPI: **Streamable HTTP** em `/mcp/` (o que o Claude Desktop
    usa) + **SSE** em `/mcp/sse` (legado/curl) + fluxo de auth web em `/mcp/auth`
    (+ `/mcp/auth/verify`).

    Autenticação (Fix KL-18): o Claude Desktop não oferece campo de API key ao
    adicionar um conector — por isso o fluxo web. O usuário abre `/mcp/auth`, cola a
    API key, recebe um **session token** de 24h e usa a URL
    `…/mcp/sse?token=<session>` no Claude. As conexões aceitam a API key (Bearer) OU
    o session token (Bearer ou ?token=). Segue o padrão canônico do FastMCP.
    """
    global _session_manager
    from starlette.applications import Starlette
    from starlette.requests import Request
    from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
    from starlette.routing import Mount, Route

    # Caminho RELATIVO ao sub-app montado em /mcp — o transporte prefixa o
    # root_path (/mcp) automaticamente, resultando em /mcp/messages/ para o cliente.
    transport = SseServerTransport("/messages/")

    async def sse_endpoint(request: Request) -> Response:
        if not _authorized(request):
            return JSONResponse(
                {"error": "unauthorized", "auth_url": "/mcp/auth"},
                status_code=401, headers={"WWW-Authenticate": "Bearer"})
        async with transport.connect_sse(
            request.scope, request.receive, request._send,
        ) as streams:
            await mcp._mcp_server.run(
                streams[0], streams[1],
                mcp._mcp_server.create_initialization_options(),
            )
        return Response()

    async def messages_asgi(scope, receive, send) -> None:
        if not _authorized(Request(scope)):
            await JSONResponse({"error": "unauthorized"}, status_code=401)(scope, receive, send)
            return
        await transport.handle_post_message(scope, receive, send)

    async def auth_page(request: Request) -> HTMLResponse:
        callback = request.query_params.get("callback_url", "")
        return HTMLResponse(_auth_page_html(callback),
                            headers={"Content-Security-Policy": _AUTH_CSP})

    async def auth_verify(request: Request) -> Response:
        if not _verify_rl_ok(request):
            return HTMLResponse(
                _auth_page_html(error="Muitas tentativas. Aguarde um minuto."),
                status_code=429, headers={"Content-Security-Policy": _AUTH_CSP,
                                          "Retry-After": str(_VERIFY_RL_WINDOW)})
        form = await request.form()
        api_key = (form.get("api_key") or "").strip()
        callback = (form.get("callback_url") or "").strip()
        expected = os.environ.get("MCP_API_KEY", "")
        if not expected or not hmac.compare_digest(api_key, expected):
            return HTMLResponse(_auth_page_html(callback, "API Key inválida."),
                                status_code=401, headers={"Content-Security-Policy": _AUTH_CSP})
        token = _new_session()
        if _safe_callback(callback):
            sep = "&" if "?" in callback else "?"
            return RedirectResponse(f"{callback}{sep}token={token}", status_code=302)
        host = request.headers.get("host", "klarim.net")
        # URL do conector: Streamable HTTP (/mcp/) — o transporte do Claude Desktop.
        return HTMLResponse(_auth_success_html(f"https://{host}/mcp/?token={token}", host, token),
                            headers={"Content-Security-Policy": _AUTH_CSP})

    # Streamable HTTP (KL-18): o transporte que o Claude Desktop usa. Inicializa o
    # session manager do FastMCP (lazy) e o embrulha com autenticação. É um objeto
    # callable ASGI — o Route do Starlette só trata instância de classe como app
    # ASGI (uma função async(scope,…) viraria endpoint request-response).
    streamable_asgi = None
    try:
        mcp.streamable_http_app()          # cria o session manager (side-effect)
        _session_manager = mcp.session_manager

        class _AuthStreamable:
            async def __call__(self, scope, receive, send):
                if scope.get("type") == "http" and not _authorized(Request(scope)):
                    await JSONResponse({"error": "unauthorized"}, status_code=401)(scope, receive, send)
                    return
                await _session_manager.handle_request(scope, receive, send)

        streamable_asgi = _AuthStreamable()
    except Exception as exc:  # noqa: BLE001 - sem Streamable HTTP, o SSE continua
        print(f"[mcp] Streamable HTTP indisponível ({exc!r}); só SSE", flush=True)

    routes = [
        Route("/auth", endpoint=auth_page, methods=["GET"]),
        Route("/auth/verify", endpoint=auth_verify, methods=["POST"]),
        Route("/sse", endpoint=sse_endpoint, methods=["GET"]),
        Route("/sse/", endpoint=sse_endpoint, methods=["GET"]),
        Mount("/messages/", app=messages_asgi),
    ]
    if streamable_asgi is not None:
        # Root do sub-app => /mcp/ (GET/POST/DELETE). Fica por último para não
        # sombrear as rotas específicas acima.
        routes.append(Route("/", endpoint=streamable_asgi))
    app.mount("/mcp", Starlette(routes=routes))
