"""Middleware ASGI de autenticação do MCP (modelo Traka + OAuth 2.1, KL-63).

Aceita, em ordem:
  1. **Bearer JWT** (access token OAuth 2.1) — validado por assinatura/iss/aud/exp/typ/
     scope (`mcp_server.oauth.validate_access_token`).
  2. **Token estático** `MCP_API_KEY` no `Authorization: Bearer` (fallback CLI / conexões
     existentes), constant-time.
  3. **Token no query param** `?token=` — pode ser o JWT (propagado ao endpoint de
     `/messages/`) **ou** a `MCP_API_KEY` (Claude.ai passa a auth na URL do SSE).

As rotas do fluxo OAuth (`/authorize`, `/token`, `/register`) são **isentas** (são o
próprio login — não podem exigir token). Sem `MCP_API_KEY` **e** sem validação OAuth,
tudo retorna 401 (fail-closed). Toda 401 traz o header `WWW-Authenticate` apontando
para o Protected Resource Metadata (RFC 9728), que dispara o fluxo OAuth no cliente.
"""

from __future__ import annotations

import hmac
import os
from urllib.parse import unquote

from starlette.responses import JSONResponse

# Rotas (relativas ao mount /mcp) que NÃO exigem auth — são o fluxo OAuth em si.
_EXEMPT = {"authorize", "token", "register"}


class MCPAuthMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") in ("http", "websocket") and not self._check(scope):
            from mcp_server import oauth
            resp = JSONResponse(
                {"error": "unauthorized"},
                status_code=401,
                headers={"WWW-Authenticate": oauth.www_authenticate_header()},
            )
            await resp(scope, receive, send)
            return
        await self.app(scope, receive, send)

    def _is_exempt(self, scope) -> bool:
        # path relativo ao mount (/mcp) — robusto a variações (`/authorize` ou último segmento).
        path = (scope.get("path") or "").rstrip("/")
        last = path.rsplit("/", 1)[-1]
        return last in _EXEMPT

    def _extract_token(self, scope) -> str:
        headers = dict(scope.get("headers") or [])
        auth = headers.get(b"authorization", b"").decode("latin1")
        if auth[:7].lower() == "bearer ":
            return auth[7:].strip()
        qs = scope.get("query_string", b"").decode("latin1")
        for part in qs.split("&"):
            if part.startswith("token="):
                return unquote(part[6:])
        return ""

    def _check(self, scope) -> bool:
        if self._is_exempt(scope):
            return True
        token = self._extract_token(scope)
        if not token:
            return False

        # 1. Access token OAuth (JWT). looks_like_jwt evita tratar a chave estática como JWT.
        from mcp_server import oauth
        if oauth.looks_like_jwt(token) and oauth.validate_access_token(token):
            return True

        # 2/3. Token estático (fallback), constant-time.
        expected = os.environ.get("MCP_API_KEY", "")
        if expected and hmac.compare_digest(token, expected):
            return True

        return False
