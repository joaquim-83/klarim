"""Tools MCP de alertas — histórico, stats, disparar alerta e enviar relatório."""

from __future__ import annotations

from mcp_server._base import mcp, _guard, _api, _store


@mcp.tool()
async def list_alerts(limit: int = 20, offset: int = 0) -> dict:
    """Histórico de alertas enviados com e-mail, score, data, status e email_id."""
    async def _impl():
        rows = await _store().list_alerts(limit=min(limit, 200), offset=offset)
        return {"alerts": rows, "limit": limit, "offset": offset}

    return await _guard(_impl)


@mcp.tool()
async def get_alert_stats() -> dict:
    """Contagem de alertas enviados: hoje, semana, mês e total."""
    return await _guard(lambda: _store().alert_stats())


@mcp.tool()
async def send_alert_to_target(target_id: int) -> dict:
    """Dispara o alerta de segurança por e-mail para um alvo (ignora a cota — ação
    manual). Requer contact_email e que o alvo não esteja unsubscribed."""
    return await _guard(lambda: _api().api_target_alert(target_id))


@mcp.tool()
async def send_report_to_email(target_url: str, email: str) -> dict:
    """Escaneia a URL e envia o relatório completo (executivo + técnico em PDF)
    para um e-mail específico. Uso admin — não exige pagamento."""
    m = _api()
    return await _guard(lambda: m.api_admin_scan_and_report(m.ScanAndReportBody(
        url=target_url, send_email=True, email_to=email, email_type="report")))
