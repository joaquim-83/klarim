"""Tools MCP de leads (KL-61) — lista, totalizadores e funil de conversão PQL."""

from __future__ import annotations

from typing import Optional

from mcp_server._base import mcp, _guard, _store


@mcp.tool()
async def list_leads(classification: Optional[str] = None, sector: Optional[str] = None,
                     has_account: Optional[bool] = None, search: Optional[str] = None,
                     limit: int = 20, offset: int = 0) -> dict:
    """Lista leads com filtros (KL-61). `classification`: cold/warm/hot/pql. `search`
    casa parcialmente (case-insensitive) em e-mail e domínio. Retorna as leads + total +
    contagem por classificação."""
    async def _impl():
        cls = classification if classification in ("cold", "warm", "hot", "pql") else None
        return await _store().list_leads(
            classification=cls, sector=(sector or None), has_account=has_account,
            search=(search or None), limit=min(max(limit, 1), 100), offset=max(offset, 0))

    return await _guard(_impl)


@mcp.tool()
async def get_lead_stats() -> dict:
    """Totalizadores de leads (KL-61): total, por classificação (cold/warm/hot/pql), com
    conta, com monitoramento, score médio, e-mails corporativos, multi-scan, top setores,
    conversão por setor, setores com maior dor, taxa PQL, hoje e últimos 7 dias."""
    return await _guard(lambda: _store().lead_stats())


@mcp.tool()
async def get_lead_funnel() -> dict:
    """Funil de conversão de leads (KL-61): e-mail verificado → scan completado → conta
    criada → monitoramento, com as taxas de conversão."""
    return await _guard(lambda: _store().lead_funnel())
