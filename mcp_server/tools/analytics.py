"""Tools MCP de analytics — funil de conversão e estatísticas de re-scan."""

from __future__ import annotations

from mcp_server._base import mcp, _guard, _api, _store


@mcp.tool()
async def get_funnel(period: str = "7d") -> dict:
    """Funil de conversão: e-mails enviados → cliques → resultado visto → CTA →
    PIX → pago → PDF baixado. Períodos: today, 7d, 30d, total."""
    return await _guard(lambda: _api().api_analytics_funnel(period))


@mcp.tool()
async def get_analytics_metrics(period: str = "7d") -> dict:
    """KL-83 — os 6 KPIs-chave do analytics (visitantes únicos, scans manuais, contas
    criadas, conversão visitante→conta, pageviews/sessão, taxa de clique em alertas) com
    valor, período anterior e variação %. SEM sparklines (economia de tokens). Períodos:
    today, 7d, 30d, 90d."""
    async def _impl():
        from api import admin_analytics as aa
        data = await aa.metrics(None, period=period, start=None, end=None)
        # remove as sparklines (arrays diários) para não gastar tokens
        slim = {k: {kk: vv for kk, vv in v.items() if kk != "sparkline"}
                for k, v in data.get("metrics", {}).items()}
        return {"period": data.get("period"), "metrics": slim}
    return await _guard(_impl)


@mcp.tool()
async def get_analytics_funnel(period: str = "7d") -> dict:
    """KL-83 — funil de conversão com breakdown por campanha e taxas inter-etapa
    (emails_sent → clicks → result_viewed → scan_started → account_created → payment_created
    → payment_completed). Marca o gargalo (menor conversão). Períodos: today, 7d, 30d, 90d."""
    async def _impl():
        from api import admin_analytics as aa
        return await aa.funnel(None, period=period, start=None, end=None)
    return await _guard(_impl)


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
