"""Tools MCP de scans — listar, detalhar, estatísticas e escanear uma URL."""

from __future__ import annotations

from mcp_server._base import mcp, _guard, _api, _store


@mcp.tool()
async def list_scans(limit: int = 20, offset: int = 0) -> dict:
    """Lista scans recentes com score, semáforo, contagens e data."""
    async def _impl():
        rows = await _store().list_scans(limit=min(limit + offset, 500))
        return {"scans": rows[offset:offset + limit], "limit": limit, "offset": offset}

    return await _guard(_impl)


@mcp.tool()
async def get_scan(scan_id: int) -> dict:
    """Detalhe de um scan: todos os checks com PASS/FAIL/INCONCLUSO e evidence."""
    return await _guard(lambda: _api().api_get_scan(scan_id))


@mcp.tool()
async def get_scan_stats() -> dict:
    """Estatísticas de scans: total, score médio, distribuição por semáforo."""
    return await _guard(lambda: _store().scan_stats())


@mcp.tool()
async def scan_url(url: str) -> dict:
    """Escaneia uma URL e retorna o resultado completo: score, semáforo, todos os
    checks, riscos, plataforma, setor e e-mail extraído. Registra automaticamente
    no banco (source='admin'). Não envia e-mail."""
    m = _api()
    return await _guard(lambda: m.api_admin_scan_and_report(m.ScanAndReportBody(url=url)))
