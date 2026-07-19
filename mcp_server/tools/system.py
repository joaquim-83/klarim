"""Tools MCP de sistema — status dos workers, saúde de e-mail, discovery, config,
totalizadores do painel, enriquecimento e contas."""

from __future__ import annotations

from typing import Optional

from mcp_server._base import mcp, _guard, _api, _store


@mcp.tool()
async def get_system_status() -> dict:
    """Status completo do sistema: workers (alive/dead + últimos ciclos),
    dependências (postgres/redis/ct_logs/resend/abacatepay), métricas de e-mail
    (enviados hoje/mês, cota mensal) e backlog de alertas. `scan.last_scan_at` vem do
    banco (bate com a página Scans do painel); `worker_last_activity` é o heartbeat."""
    return await _guard(lambda: _api().api_system_status())


@mcp.tool()
async def get_dashboard_stats() -> dict:
    """Resumo completo da plataforma (os MESMOS totalizadores da home do painel):
    alvos (por status, score 100), scans (total, manuais vs automáticos, hoje, 7 dias,
    média, semáforo), perfis/landings (total, públicas, com IA, com CNAE), contas
    (total, ativas, sites monitorados), alertas e e-mails não lidos no inbox."""
    async def _impl():
        store = _store()
        summary = await store.dashboard_summary()
        try:
            summary["inbox"] = {"unread": await store.inbox_unread_count()}
        except Exception:  # noqa: BLE001
            summary["inbox"] = {"unread": 0}
        return summary

    return await _guard(_impl)


@mcp.tool()
async def get_enrichment_status() -> dict:
    """Status do enriquecimento de perfis: backlog por grupo (G1 sem perfil, G2 sem IA,
    G3 sem descrição, G4 sem CNAE) + o backlog `sem_contato` sem scan (KL-60)."""
    async def _impl():
        store = _store()
        groups = await store.count_enrichment_groups("all")
        return {
            "backlog": {
                "g1_no_profile": groups.get("group1", 0),
                "g2_no_ai": groups.get("group2", 0),
                "g3_no_description": groups.get("group3", 0),
                "g4_no_cnae": groups.get("group4", 0),
                "total": groups.get("total", 0),
            },
            "unscanned_sem_contato": await store.count_unscanned_targets("sem_contato"),
        }

    return await _guard(_impl)


@mcp.tool()
async def get_user_accounts() -> dict:
    """Contas de usuário + sites monitorados de cada uma (e-mail, plano, sites com
    score, criação, último login, ativo). Reusa a Gestão de Clientes do painel."""
    return await _guard(lambda: _api().admin_clients())


@mcp.tool()
async def get_email_health() -> dict:
    """Saúde de e-mail: bounce rate, bounces permanentes, complaints, tamanho da
    blocklist e status de risco (ok/warning/critical). Vital para não estourar a
    reputação do domínio no Resend/Gmail. Cobre TODOS os caminhos (email_log, KL-62)."""
    return await _guard(lambda: _api().api_system_email_health())


@mcp.tool()
async def get_email_log(email_type: Optional[str] = None, status: Optional[str] = None,
                        to_email: Optional[str] = None, source: Optional[str] = None,
                        limit: int = 20) -> dict:
    """Log unificado de e-mails enviados (KL-62) — auditoria dos 20 caminhos. Filtros:
    `email_type` (alert, profile_view, verification_code, report_delivery, …), `status`
    (sent/bounced/failed/blocked), `to_email` (busca parcial), `source`. Retorna as
    entradas + total + contagem por status."""
    async def _impl():
        et = email_type or None
        st = status if status in ("sent", "bounced", "failed", "blocked", "complained") else None
        return await _store().list_email_log(
            email_type=et, status=st, to_email=(to_email or None),
            source=(source or None), limit=min(max(limit, 1), 100), offset=0)

    return await _guard(_impl)


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


@mcp.tool()
async def get_gcs_archive_stats() -> dict:
    """Saúde do arquivamento de responses brutos no GCS (KL-77 Fase 2): se está
    habilitado, o bucket, arquivos e bytes arquivados hoje, tamanho médio comprimido,
    último upload e erros de hoje (com o último erro). Vital para confirmar que o
    dado bruto de cada scan — que o KL-75 vai reprocessar — está sendo preservado."""
    return await _guard(lambda: _api().api_gcs_archive_stats())


@mcp.tool()
async def get_ownership_stats() -> dict:
    """Verificação de propriedade de sites (KL-68): total de donos verificados, por
    método (auto_email vs code_verification), funil de verificações por status
    (pending/verified/expired/failed) e taxa de sites monitorados com dono."""
    return await _guard(lambda: _store().ownership_stats())


@mcp.tool()
async def get_bulletin_stats() -> dict:
    """Boletim de segurança (KL-44 P3): total enviados, hoje, semana, por frequência
    (weekly/monthly/daily) e quantos notificaram o técnico vinculado."""
    return await _guard(lambda: _store().bulletin_stats())


@mcp.tool()
async def list_technician_links(limit: int = 50) -> dict:
    """Vínculos dono↔técnico (KL-44 P3): dono, alvo, e-mail do técnico e status
    (pending/active/revoked)."""
    return await _guard(lambda: _store().list_technician_links_admin(min(max(limit, 1), 200)))


@mcp.tool()
async def admin_remove_user_site(user_id: int, target_id: int, notify: bool = True) -> dict:
    """Remove um site do monitoramento de um usuário (KL-69). Revoga a propriedade
    (auditoria) e remove o vínculo. Se notify=true, envia e-mail ao usuário avisando
    da remoção. A conta do usuário permanece ativa."""
    async def _impl():
        m = _api()
        store = _store()
        user = await store.get_user_by_id(user_id)
        if not user:
            return {"error": "usuário não encontrado", "status_code": 404}
        link = await store.get_user_site(user_id, target_id)
        if not link:
            return {"error": "site não vinculado ao usuário", "status_code": 404}
        target = await store.get_target(target_id)
        domain = (target or {}).get("domain") or ""
        if link.get("is_owner"):
            await store.mark_ownership_revoked(user_id, target_id)
        await store.unlink_user_site(user_id, target_id)
        notified = await m._notify_site_removed(user, domain) if notify else False
        return {"removed": True, "domain": domain, "notified": notified}

    return await _guard(_impl)
