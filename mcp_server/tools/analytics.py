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
async def get_lead_scoring_stats(period: str = "7d") -> dict:
    """KL-85 — qualidade do lead scoring de alertas: distribuição do `alert_quality_score`,
    quantos alvos passam do threshold (>=20), quantos seriam filtrados (0-19 / <0), score
    médio e alertas enviados no período. Base para calibrar o threshold e estimar economia da
    cota Resend. Períodos: today, 7d, 30d, 90d."""
    async def _impl():
        from api import admin_analytics as aa
        return await aa.alert_quality(None, period=period, start=None, end=None)
    return await _guard(_impl)


@mcp.tool()
async def get_rescan_stats() -> dict:
    """Estatísticas de re-scans: improved, worsened, unchanged, first_rescan."""
    return await _guard(lambda: _store().rescan_stats())


@mcp.tool()
async def get_sector_stats() -> dict:
    """KL-84 — saúde da taxonomia ABERTA de setores: contagem por status (official/approved/
    proposed/rejected/merged), total classificado, quantos em 'outro' + %, e a lista de
    setores EMERGENTES (propostos pela IA, aguardando curadoria) com contagem de sites. Base
    para decidir aprovar/merge/rejeitar e acompanhar o 'outro' caindo."""
    async def _impl():
        store = _store()
        stats = await store.sector_taxonomy_stats()
        emerging = await store.list_sectors(["proposed"])
        stats["emerging"] = [{"slug": s["slug"], "label": s["label"],
                              "macro_sector": s.get("macro_sector"),
                              "site_count": s.get("site_count", 0)} for s in emerging]
        return stats
    return await _guard(_impl)


@mcp.tool()
async def classify_target_sector(target_id: int) -> dict:
    """KL-84 — reclassifica UM alvo com a IA usando a descrição JÁ extraída (sem re-scan). Passa
    pela taxonomia aberta (resolve sinônimo, reusa setor existente ou cria proposta) e grava
    protegendo `manual`/`receita`. Retorna o setor antigo, o novo e a ação tomada. Não altera
    score/checks. Útil para desafogar o 'outro' em alvos específicos."""
    async def _impl():
        from scanner.ai_enrichment import AI_ENRICHMENT_ENABLED
        from scripts.reclassify_sectors import _classify_one
        from discovery.sector_classification import process_classification
        from discovery.classifier import PRICE_TIERS

        store = _store()
        if not AI_ENRICHMENT_ENABLED:
            return {"ok": False, "error": "OPENAI_API_KEY ausente (classificador desligado)."}
        target = await store.get_target(target_id)
        if not target:
            return {"ok": False, "error": "Alvo não encontrado."}
        if target.get("classification_source") in ("manual", "receita"):
            return {"ok": False, "error": "Classificação protegida (manual/receita).",
                    "sector": target.get("sector")}
        profile = await store.get_site_profile(target_id) or {}
        row = {"domain": target.get("domain"), "sector": target.get("sector"),
               "description": profile.get("description"),
               "business_type": profile.get("business_type"), "tags": profile.get("tags")}
        known = [r["slug"] for r in await store.list_sectors(["approved"])]
        ai = await _classify_one(row, known)
        if not ai or ai.get("sector_confidence", 0) <= 0.7:
            return {"ok": False, "error": "Sem sinal suficiente para reclassificar.",
                    "sector": target.get("sector")}
        decision = await process_classification(store, ai)
        new_sector, old = decision["sector"], target.get("sector") or "outro"
        if new_sector != "outro" and new_sector != old:
            tier = PRICE_TIERS.get(new_sector, "standard")
            await store.reclassify_target_sector(target_id, new_sector, tier,
                                                 ai["sector_confidence"])
        return {"ok": True, "old_sector": old, "new_sector": new_sector,
                "action": decision["action"], "confidence": ai["sector_confidence"]}
    return await _guard(_impl)


@mcp.tool()
async def get_privacy_stats() -> dict:
    """KL-44 P5 — distribuição PASS/FAIL por indicador TÉCNICO de privacidade nos sites
    escaneados (ex.: quantos têm política de privacidade, banner de cookies, DPO). Dado
    agregado/anônimo — inteligência comercial ('X% do setor não tem banner de cookies').
    NÃO é avaliação de conformidade LGPD."""
    return await _guard(lambda: _store().privacy_indicator_stats())
