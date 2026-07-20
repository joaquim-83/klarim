"""Tools MCP de tecnografia (KL-75) — tech stack detectado por scan, adoção de
tecnologias por setor e histórico de status dos sites.

Dados extraídos do response bruto de cada scan (headers/scripts/meta/dns/ssl), sem
request extra. Inteligência comercial: quem usa GA4, quem tem checkout, quais sites
estão parked/abandonados, market share de tecnologias por setor."""

from __future__ import annotations

from typing import Optional

from mcp_server._base import mcp, _guard, _api


@mcp.tool()
async def get_tech_adoption(tech: str, sector: Optional[str] = None) -> dict:
    """Taxa de adoção de uma tecnologia (KL-75), opcionalmente filtrada por setor.
    `tech` é o nome canônico detectado (ex.: 'google_analytics_4', 'wordpress',
    'mercado_pago', 'cloudflare'). Retorna total de sites, quantos têm a tech e a taxa
    (ex.: "72% dos hotéis usam GA"). Sem `sector` = base inteira."""
    return await _guard(lambda: _api().api_tech_adoption(tech, sector))


@mcp.tool()
async def get_site_tech_stack(domain: str) -> dict:
    """Tech stack completo de um domínio (KL-75): tecnologias detectadas (nome,
    categoria, versão, fonte), provedor de e-mail (via MX), domínios relacionados
    (via SSL SAN), status atual, **tipo de site** (institucional/ecommerce/saas/portal/
    blog/parked/abandonado) e **nº de subdomínios**. Ex.: 'hotel.com.br'."""
    return await _guard(lambda: _api().api_site_tech_stack(domain))


@mcp.tool()
async def get_site_subdomains(domain: str, limit: int = 50) -> dict:
    """Subdomínios de um domínio vistos nos CT logs (KL-75 P2): nome, tipo (app/api/
    admin/staging/mail/shop/docs/status/cdn/blog), quando visto e a CA do certificado.
    Só a existência é registrada — subdomínios NUNCA são escaneados. Ex.: 'empresa.com.br'."""
    return await _guard(lambda: _api().api_site_subdomains(domain, limit))


@mcp.tool()
async def get_site_status_history(target_id: Optional[int] = None,
                                  domain: Optional[str] = None, limit: int = 10) -> dict:
    """Histórico de status de um site (KL-75) — ativo, parked, abandonado, fora_do_ar,
    bloqueado, dominio_inativo — com código HTTP e tempo de resposta de cada scan.
    Informe `target_id` OU `domain`. Rastreia sites que caem ou viram parking."""
    return await _guard(
        lambda: _api().api_site_status_history(domain=domain, target_id=target_id,
                                               limit=limit))
