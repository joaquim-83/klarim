"""Tools MCP de pagamentos — listagem e estatísticas de receita."""

from __future__ import annotations

from mcp_server._base import mcp, _guard, _api


@mcp.tool()
async def list_payments(limit: int = 20) -> dict:
    """Lista de pagamentos com charge_id, URL, valor, status, alvo e data."""
    return await _guard(lambda: _api().api_payments_list(
        status=None, limit=min(limit, 200), offset=0))


@mcp.tool()
async def get_payment_stats() -> dict:
    """Receita total, contagem por status e ticket médio."""
    return await _guard(lambda: _api().api_payments_stats())
