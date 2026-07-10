"""Base do servidor MCP do Klarim — instância FastMCP + helpers das tools.

Fica isolado do `server.py` (que monta o app SSE) e das `tools/` para evitar
import circular: as tools importam `mcp`/`_guard`/`_api`/`_store` daqui, e o
`server.py` importa `mcp` daqui + as tools (que se registram via `@mcp.tool()`).
"""

from __future__ import annotations

from fastapi import HTTPException
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

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

# Desliga a proteção de DNS rebinding do transporte SSE: o default só permite Host
# localhost/127.0.0.1 e rejeitaria `klarim.net` atrás do Nginx ("Invalid Host
# header"). Seguro: estamos atrás do Nginx e a auth é por MCP_API_KEY.
mcp.settings.transport_security = TransportSecuritySettings(
    enable_dns_rebinding_protection=False)


def _api():
    """Import tardio do api.main (evita ciclo: api.main importa o mcp_app)."""
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
