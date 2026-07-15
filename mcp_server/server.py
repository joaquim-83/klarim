"""Servidor MCP do Klarim (KL-18) — **SSE** com propagação de token (modelo Traka).

Monta um `mcp_app` (Starlette) com o transporte SSE do MCP em `/sse` (+ `/messages/`),
para ser montado em `/mcp` no FastAPI **envolvido pela `MCPAuthMiddleware`**. As 25
tools vivem em `mcp_server/tools/` e se registram ao importar o pacote.

**Propagação de token (o que faz o Claude.ai conectar).** O transporte SSE anuncia
o endpoint de POST como `data: /mcp/messages/?session_id=<hex>` — **sem** a auth.
O Claude então faz os POSTs nesse endpoint; sem o token, a `MCPAuthMiddleware`
responde 401 e a conexão falha na 2ª fase. Por isso reescrevemos o evento
`endpoint` para incluir `&token=<token>` (o mesmo token com que o cliente abriu o
SSE), garantindo que os POSTs cheguem autenticados.
"""

from __future__ import annotations

import re
from urllib.parse import quote, unquote

from mcp.server.sse import SseServerTransport
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Mount, Route

from mcp_server._base import mcp
from mcp_server import tools  # noqa: F401 - importa e registra as tools no `mcp`
from mcp_server import oauth  # KL-63: OAuth 2.1 + PKCE (authorize/token/register)

# Caminho RELATIVO ao mount em /mcp — o transporte prefixa o root_path (/mcp),
# resultando em /mcp/messages/ anunciado ao cliente.
_transport = SseServerTransport("/messages/")


def _token_from_scope(scope) -> str:
    """Token com que o cliente autenticou o SSE (Bearer header ou ?token=)."""
    headers = dict(scope.get("headers") or [])
    auth = headers.get(b"authorization", b"").decode("latin1")
    if auth[:7].lower() == "bearer ":
        return auth[7:].strip()
    qs = scope.get("query_string", b"").decode("latin1")
    for part in qs.split("&"):
        if part.startswith("token="):
            return unquote(part[6:])
    return ""


def _token_propagating_send(send, token: str):
    """Envolve o `send` do SSE para anexar `&token=<token>` ao evento `endpoint`."""
    tok = quote(token, safe="").encode("latin1")
    state = {"done": False}

    async def wrapped(message):
        if not state["done"] and message.get("type") == "http.response.body":
            body = message.get("body", b"")
            if b"event: endpoint" in body and b"session_id=" in body and b"token=" not in body:
                body = re.sub(rb"(session_id=[0-9a-fA-F]+)", rb"\1&token=" + tok, body, count=1)
                message = {**message, "body": body}
                state["done"] = True
        await send(message)

    return wrapped


async def _sse_endpoint(request: Request) -> Response:
    # A auth já foi feita pela MCPAuthMiddleware; aqui só propagamos o token.
    token = _token_from_scope(request.scope)
    send = _token_propagating_send(request._send, token) if token else request._send
    async with _transport.connect_sse(request.scope, request.receive, send) as streams:
        await mcp._mcp_server.run(
            streams[0], streams[1], mcp._mcp_server.create_initialization_options())
    return Response()


async def _messages_asgi(scope, receive, send) -> None:
    await _transport.handle_post_message(scope, receive, send)


# App SSE para montar em /mcp (envolvido pela MCPAuthMiddleware no api.main). As rotas
# OAuth (KL-63) são ISENTAS de auth na MCPAuthMiddleware (são o próprio fluxo de login).
mcp_app = Starlette(routes=[
    # OAuth 2.1 + PKCE (KL-63) — /mcp/authorize, /mcp/token, /mcp/register.
    Route("/authorize", endpoint=oauth.authorize, methods=["GET", "POST"]),
    Route("/token", endpoint=oauth.token, methods=["POST"]),
    Route("/register", endpoint=oauth.register, methods=["POST"]),
    # Transporte SSE do MCP (autenticado).
    Route("/sse", endpoint=_sse_endpoint, methods=["GET"]),
    Route("/sse/", endpoint=_sse_endpoint, methods=["GET"]),
    Mount("/messages/", app=_messages_asgi),
])
