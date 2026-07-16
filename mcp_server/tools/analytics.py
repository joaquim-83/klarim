"""Tools MCP de analytics — funil de conversão e estatísticas de re-scan."""

from __future__ import annotations

from mcp_server._base import mcp, _guard, _api, _store


@mcp.tool()
async def get_funnel(period: str = "7d") -> dict:
    """Funil de conversão: e-mails enviados → cliques → resultado visto → CTA →
    PIX → pago → PDF baixado. Períodos: today, 7d, 30d, total."""
    return await _guard(lambda: _api().api_analytics_funnel(period))


@mcp.tool()
async def get_rescan_stats() -> dict:
    """Estatísticas de re-scans: improved, worsened, unchanged, first_rescan."""
    return await _guard(lambda: _store().rescan_stats())


@mcp.tool()
async def get_privacy_stats() -> dict:
    """KL-44 P5 — distribuição PASS/FAIL por indicador TÉCNICO de privacidade nos sites
    escaneados (ex.: quantos têm política de privacidade, banner de cookies, DPO). Dado
    agregado/anônimo — inteligência comercial ('X% do setor não tem banner de cookies').
    NÃO é avaliação de conformidade LGPD."""
    return await _guard(lambda: _store().privacy_indicator_stats())
