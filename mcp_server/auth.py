"""Middleware ASGI de autenticação do MCP (modelo Traka).

Fail-closed, constant-time. Aceita a `MCP_API_KEY` no header
`Authorization: Bearer <chave>` **ou** no query param `?token=<chave>`. Sem
`MCP_API_KEY` configurada, tudo retorna 401 (MCP desligado). Toda 401 traz o header
`WWW-Authenticate: Bearer realm="klarim-mcp"`.

Envolve o `mcp_app` inteiro (SSE + /messages/), então tanto a conexão do stream
quanto os POSTs de mensagens são autenticados. Como o endpoint anunciado propaga
o `?token=` (ver `server._token_propagating_send`), os POSTs do Claude chegam
autenticados.
"""

from __future__ import annotations

import hmac
import os
from urllib.parse import unquote

from starlette.responses import JSONResponse


class MCPAuthMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") in ("http", "websocket") and not self._check(scope):
            resp = JSONResponse(
                {"error": "unauthorized"},
                status_code=401,
                headers={"WWW-Authenticate": 'Bearer realm="klarim-mcp"'},
            )
            await resp(scope, receive, send)
            return
        await self.app(scope, receive, send)

    def _check(self, scope) -> bool:
        expected = os.environ.get("MCP_API_KEY", "")
        if not expected:
            return False  # fail-closed: sem chave configurada = MCP desligado

        # 1. Header Authorization: Bearer <token>
        headers = dict(scope.get("headers") or [])
        auth = headers.get(b"authorization", b"").decode("latin1")
        if auth[:7].lower() == "bearer " and hmac.compare_digest(auth[7:].strip(), expected):
            return True

        # 2. Query param ?token=<token> (Claude.ai passa a auth na URL do SSE)
        qs = scope.get("query_string", b"").decode("latin1")
        for part in qs.split("&"):
            if part.startswith("token="):
                if hmac.compare_digest(unquote(part[6:]), expected):
                    return True

        return False
