"""Tools MCP de sistema — status dos workers, saúde de e-mail, discovery, config."""

from __future__ import annotations

from mcp_server._base import mcp, _guard, _api


@mcp.tool()
async def get_system_status() -> dict:
    """Status completo do sistema: workers (alive/dead + últimos ciclos),
    dependências (postgres/redis/ct_logs/resend/abacatepay), métricas de e-mail
    (enviados hoje/mês, cota mensal) e backlog de alertas."""
    return await _guard(lambda: _api().api_system_status())


@mcp.tool()
async def get_email_health() -> dict:
    """Saúde de e-mail: bounce rate, bounces permanentes, complaints, tamanho da
    blocklist e status de risco (ok/warning/critical). Vital para não estourar a
    reputação do domínio no Resend/Gmail."""
    return await _guard(lambda: _api().api_system_email_health())


@mcp.tool()
async def get_discovery_status() -> dict:
    """Status do Discovery Worker: CT poller conectado, certificados vistos,
    domínios .com.br filtrados, buffer atual e alvos descobertos hoje."""
    return await _guard(lambda: _api().api_discovery_status())


@mcp.tool()
async def get_config() -> dict:
    """Configuração operacional em uso: batch size, intervalos, limites mensais,
    timeouts, etc. (somente leitura, sem segredos)."""
    return await _guard(lambda: _api().api_config())
