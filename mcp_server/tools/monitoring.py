"""Tools MCP de sites monitorados (KL-29) — listar e oferecer monitoramento."""

from __future__ import annotations

from typing import Optional

from mcp_server._base import mcp, _guard, _api, _store


@mcp.tool()
async def list_monitored_sites(status: Optional[str] = None) -> dict:
    """Lista os sites monitorados (KL-29) com status e último score. status:
    pending, active, suspended, removed. Inclui estatísticas por status."""
    async def _impl():
        store = _store()
        return {
            "sites": await store.list_monitored_sites(status=status),
            "stats": await store.monitored_stats(),
        }

    return await _guard(_impl)


@mcp.tool()
async def offer_monitoring(target_id: int) -> dict:
    """Oferece monitoramento gratuito para um alvo com score 100. Roda um scan
    COMPLETO (29) para confirmar o score; se 100 e ainda não monitorado, cria a
    oferta e envia o e-mail de convite. Requer e-mail configurado (Resend)."""
    async def _impl():
        store = _store()
        target = await store.get_target(target_id)
        if target is None:
            return {"error": "Alvo não encontrado."}
        m = _api()
        mailer = m._mailer() if m._email_enabled() else None
        if mailer is None:
            return {"error": "Envio de e-mail não configurado — não é possível ofertar."}
        from discovery.rescan_worker import _maybe_offer_monitoring
        offered = await _maybe_offer_monitoring(store, mailer, target)
        return {"target_id": target_id, "url": target["url"], "offered": offered}

    return await _guard(_impl)
