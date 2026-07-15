"""Tools MCP de assinaturas/planos (KL-44) — stats e lista de assinantes."""

from __future__ import annotations

from typing import Optional

from mcp_server._base import mcp, _guard


@mcp.tool()
async def get_subscription_stats() -> dict:
    """Totalizadores de assinaturas (KL-44 Guardião Digital): total de contas, por plano
    (free/pro/agency), por status (trial/active/free/expired/cancelled), trials ativos,
    trials expirando em 7 dias e taxa de conversão trial→pago."""
    from api import plans
    return await _guard(plans.get_subscription_stats)


@mcp.tool()
async def list_subscribers(plan_id: Optional[str] = None, status: Optional[str] = None,
                           search: Optional[str] = None, limit: int = 25) -> dict:
    """Lista assinantes (KL-44) com filtros (plan_id, status, search por e-mail). Retorna
    e-mail, plano, status, trial_ends_at, dias de trial restantes e nº de sites monitorados."""
    from api import plans

    async def _impl():
        rows = await plans.list_subscribers(
            plan_id=(plan_id or None), status=(status or None),
            search=(search or None), limit=min(max(limit, 1), 200))
        return {"subscribers": rows}

    return await _guard(_impl)
