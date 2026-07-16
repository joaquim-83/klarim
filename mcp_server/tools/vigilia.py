"""MCP — vigílias (KL-44 P2). Tools de leitura sobre o monitoramento contínuo.

Wrapper fino sobre o store/API (nenhuma lógica duplicada). Toda tool passa pelo
`_guard` — nunca derruba a sessão."""

from typing import Optional

from mcp_server._base import mcp, _guard, _store


@mcp.tool()
async def get_vigilia_stats() -> dict:
    """Estatísticas das vigílias (KL-44 P2): total ativas, contagem por tipo
    (ssl/domain/score/email/reputation), por status (ok/warning/critical/error) e
    alertas gerados hoje/7d/30d."""
    return await _guard(lambda: _store().vigilia_stats())


@mcp.tool()
async def list_vigilia_alerts(tipo: Optional[str] = None, severity: Optional[str] = None,
                              limit: int = 50) -> dict:
    """Lista os alertas de vigília mais recentes (KL-44 P2). Filtros opcionais:
    `tipo` (ssl/domain/score/email/reputation/uptime/changes/phishing) e
    `severity` (info/warning/critical)."""
    async def _impl():
        lim = max(1, min(int(limit), 200))
        rows = await _store().list_vigilia_alerts(tipo=tipo, severity=severity, limit=lim)
        return {"alerts": rows, "count": len(rows)}
    return await _guard(_impl)


@mcp.tool()
async def get_typosquat_alerts(limit: int = 100) -> dict:
    """Domínios suspeitos de typosquatting/phishing detectados nos CT logs (KL-44 P4):
    domínios muito parecidos com os monitorados por contas Agency. Traz o domínio suspeito,
    o tipo de similaridade (levenshtein/homoglyph/tld_variant), o dono e se já foi
    notificado/descartado, + um resumo (`stats`)."""
    async def _impl():
        lim = max(1, min(int(limit), 500))
        store = _store()
        rows = await store.list_typosquat_alerts(limit=lim)
        return {"alerts": rows, "count": len(rows), "stats": await store.typosquat_stats()}
    return await _guard(_impl)
