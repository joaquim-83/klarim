"""Tools MCP de alvos — listar, detalhar, buscar, adicionar, editar, classificar."""

from __future__ import annotations

from typing import Optional

from mcp_server._base import mcp, _guard, _api, _store


@mcp.tool()
async def list_targets(
    status: Optional[str] = None,
    platform: Optional[str] = None,
    sector: Optional[str] = None,
    search: Optional[str] = None,
    limit: int = 20,
    offset: int = 0,
) -> dict:
    """Lista alvos com filtros opcionais. status: discovered, scanned, alerted,
    converted, sem_contato, descartado, unsubscribed. `search` casa parcialmente
    (case-insensitive) em URL, domínio e e-mail."""
    async def _impl():
        store = _store()
        targets = await store.list_targets(
            status=status, platform=platform, sector=sector, search=search,
            limit=min(limit, 200), offset=offset)
        total = await store.count_targets(status=status)
        return {"targets": targets, "total": total, "limit": limit, "offset": offset}

    return await _guard(_impl)


@mcp.tool()
async def get_target(target_id: int) -> dict:
    """Detalhe completo de um alvo: dados, últimos scans, histórico de alertas e
    de re-scans."""
    async def _impl():
        store = _store()
        target = await store.get_target(target_id)
        if target is None:
            return {"error": "Alvo não encontrado."}
        return {
            "target": target,
            "profile": await store.get_site_profile(target_id),
            "classifications": await store.get_target_classifications(target_id),  # KL-55
            "recent_scans": await store.list_scans(target_id=target_id, limit=10),
            "alerts": await store.list_alerts(target_id=target_id, limit=10),
            "rescans": await store.list_rescans(target_id=target_id, limit=10),
        }

    return await _guard(_impl)


@mcp.tool()
async def get_target_classifications(target_id: int) -> dict:
    """Classificações CNAE multi-setor de um alvo (KL-55): código CNAE, seção (A–U),
    divisão, confiança, fonte (receita/ai/manual) e rank. Um alvo pode ter vários."""
    async def _impl():
        rows = await _store().get_target_classifications(target_id)
        return {"target_id": target_id, "classifications": rows}

    return await _guard(_impl)


@mcp.tool()
async def get_target_stats() -> dict:
    """Contagem de alvos por status, plataforma e setor + **perfis** (total, com
    descrição/IA, com CNAE, landings públicas)."""
    async def _impl():
        store = _store()
        base = await store.stats()
        base["profiles"] = await store.profile_counts()
        return base

    return await _guard(_impl)


@mcp.tool()
async def get_site_profile(target_id: int) -> dict:
    """Perfil comercial extraído do site (KL-50): nome, telefone, whatsapp, CNPJ,
    endereço, redes sociais, tecnologias, provedores e score de maturidade digital."""
    async def _impl():
        prof = await _store().get_site_profile(target_id)
        return prof if prof is not None else {"error": "Perfil não encontrado."}

    return await _guard(_impl)


@mcp.tool()
async def search_targets(query: str) -> dict:
    """Busca alvos por URL, domínio ou e-mail (parcial, case-insensitive). Até 20
    resultados. Útil para reaproveitar alvos existentes."""
    async def _impl():
        rows = await _store().list_targets(search=query, limit=20)
        return {"query": query, "count": len(rows), "targets": rows}

    return await _guard(_impl)


@mcp.tool()
async def add_target(url: str) -> dict:
    """Adiciona uma URL como alvo: fetch + fingerprint + extrai e-mail (com MX) +
    classifica setor e enfileira para scan."""
    m = _api()
    return await _guard(lambda: m.api_targets_add(m.TargetAddBody(url=url)))


@mcp.tool()
async def update_target_email(target_id: int, email: str) -> dict:
    """Atualiza o e-mail de contato de um alvo. Se estava em 'sem_contato', volta a
    'discovered' automaticamente e entra no pipeline de alertas."""
    m = _api()
    return await _guard(lambda: m.api_target_update_email(target_id, m.EmailBody(contact_email=email)))


@mcp.tool()
async def update_target_status(target_id: int, status: str) -> dict:
    """Altera o status de um alvo. Valores: discovered, scanned, alerted,
    converted, sem_contato, descartado, unsubscribed."""
    m = _api()
    return await _guard(lambda: m.api_target_update_status(target_id, m.StatusBody(status=status)))


@mcp.tool()
async def update_target_sector(target_id: int, sector: str) -> dict:
    """Classifica manualmente o setor de um alvo (source='manual', confiança 1.0).
    Valores: hotel, clinica, escola, ecommerce, condominio, juridico,
    contabilidade, restaurante, imobiliaria, automotivo, outro."""
    m = _api()
    return await _guard(lambda: m.api_target_classify(target_id, m.ClassifyBody(sector=sector)))


@mcp.tool()
async def classify_targets_batch(target_ids: list[int], sector: str) -> dict:
    """Classifica múltiplos alvos de uma vez para o mesmo setor. Retorna quantos
    foram atualizados."""
    m = _api()
    return await _guard(lambda: m.api_classify_batch(
        m.ClassifyBatchBody(target_ids=target_ids, sector=sector)))


@mcp.tool()
async def toggle_profile_visibility(target_id: int, visible: bool) -> dict:
    """Liga/desliga a landing pública de um alvo (`/site/{dominio}`, KL-56). Desligada,
    a página some do site e do sitemap (mesmo comportamento de descartado)."""
    m = _api()
    return await _guard(lambda: m.api_profile_visibility(
        target_id, m.VisibilityBody(visible=visible)))


@mcp.tool()
async def update_site_profile(
    target_id: int,
    description: str | None = None,
    business_type: str | None = None,
    company_name: str | None = None,
    tags: list[str] | None = None,
) -> dict:
    """Edita o perfil da landing (description/business_type/company_name/tags). Marca
    o perfil como editado à mão — o enrich automático deixa de sobrescrever esses
    campos (KL-56). Passe só os campos a alterar."""
    m = _api()
    body = m.ProfileEditBody(description=description, business_type=business_type,
                             company_name=company_name, tags=tags)
    return await _guard(lambda: m.api_update_profile(target_id, body))
