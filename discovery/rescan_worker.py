"""Re-scan Worker (KL-13) — reescaneia alvos a cada 30 dias e envia e-mail de evolução.

Fecha o ciclo de vida do alvo: sites já engajados (scanned/alerted) são
reescaneados após 30 dias; o score novo é comparado ao anterior e um e-mail conta
a história (melhorou / piorou / permaneceu igual), reativando a conversão sem
precisar descobrir alvos novos.

Compartilha a **cota mensal GLOBAL** de e-mails proativos com o Alert Worker
(`count_proactive_emails_this_month`, KL-23). Os e-mails de evolução são enviados
em LOTE (`send_evolution_batch`) ao fim do ciclo: cada alvo é reescaneado
individualmente (é preciso varrer o site), mas o e-mail fica pendente
(`rescan_log.email_id IS NULL`) e é despachado em batches — incluindo pendências
de ciclos anteriores. Se a cota mensal estourar, o e-mail continua pendente para
o próximo ciclo.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional
from urllib.parse import quote

from scanner import run_scan
from scanner.checks.base import registrable_domain, domain_of
from scanner.cache import ScanCache
from notifier import KlarimMailer, build_unsubscribe_link
from reporter.risk_messages import get_risk_messages
from payments import PRICING, DEFAULT_TIER, amount_display
from .heartbeat import publish_heartbeat
from .store import get_target_store
from .alert_worker import severity_counts_from_checks, alerts_stopped, is_demo_target
from . import worker_control

_SITE_BASE = os.environ.get("SITE_BASE", "https://klarim.net")


def _monitor_secret() -> str:
    return os.environ.get("JWT_SECRET", "") or os.environ.get("UNSUBSCRIBE_SECRET", "")


def _monitor_removal_token(domain: str) -> str:
    # DEVE casar com api.main._monitor_removal_token (link de remoção nos e-mails).
    return hmac.new(_monitor_secret().encode(), f"remove:{domain}".encode(),
                    hashlib.sha256).hexdigest()[:32]


def _monitor_result_url(url: str) -> str:
    return f"{_SITE_BASE}/result?url={quote(url, safe='')}"


def _monitor_remove_url(domain: str) -> str:
    return f"{_SITE_BASE}/api/monitoring/remove?domain={quote(domain, safe='')}&token={_monitor_removal_token(domain)}"


def _monitor_approve_url(token: str) -> str:
    return f"{_SITE_BASE}/monitorados/aprovar?token={quote(token, safe='')}"


async def _maybe_offer_monitoring(store, mailer, target: Dict[str, Any]) -> bool:
    """Site engajado que voltou a 100 no re-scan → confere no scan COMPLETO (29) e,
    se 100 e ainda não monitorado, cria a oferta + envia o e-mail (KL-29)."""
    url = target["url"]
    email = (target.get("contact_email") or "").strip()
    if not email or mailer is None or is_demo_target(email=email, url=url):
        return False
    domain = registrable_domain(domain_of(url))
    existing = await store.get_monitored_by_domain(domain)
    if existing and existing["status"] in ("active", "suspended", "pending"):
        return False  # já ofertado/monitorado
    report = await run_scan(url, full=True)  # confirma no completo
    score = report.score.score if report.score else 0
    if score != 100:
        return False
    token = secrets.token_urlsafe(48)
    site = await store.upsert_monitoring_offer(
        domain=domain, url=url, contact_email=email, approval_token=token,
        target_id=target.get("id"), score=100)
    if not site or site["status"] != "pending":
        return False
    try:
        await mailer.send_monitor_offer(email, domain, _monitor_approve_url(site["approval_token"]))
        print(f"[monitor] oferta enviada a {email} ({domain})", flush=True)
        return True
    except Exception as exc:  # noqa: BLE001 - best-effort
        print(f"[monitor] falha ao ofertar {domain}: {exc!r}", flush=True)
        return False


def classify_evolution(old_score: Optional[int], new_score: Optional[int]) -> str:
    """Classifica a evolução do score entre duas varreduras."""
    if old_score is None:
        return "first_rescan"
    if new_score is None:
        return "unchanged"
    if new_score > old_score:
        return "improved"
    if new_score < old_score:
        return "worsened"
    return "unchanged"


def price_display_for_tier(tier: Optional[str]) -> str:
    return amount_display(PRICING.get(tier or DEFAULT_TIER, PRICING[DEFAULT_TIER]))


def _unsub_link(email: str) -> Optional[str]:
    secret = os.environ.get("UNSUBSCRIBE_SECRET")
    return build_unsubscribe_link(email, secret) if secret else None


async def rescan_target(store, mailer: Optional[KlarimMailer], cache: Optional[ScanCache],
                        target: Dict[str, Any], send_email: bool = True) -> Dict[str, Any]:
    """Reescaneia um alvo, persiste, compara score e (se permitido) envia a evolução.

    Reutilizado pelo worker (ciclo) e pela API (disparo manual). Sempre atualiza os
    dados; o e-mail só sai se ``send_email`` e houver mailer + e-mail de contato.
    """
    url = target["url"]
    target_id = target["id"]
    old_score = target.get("last_scan_score")
    old_semaphore = target.get("old_semaphore")

    # Re-scan do funil de re-engajamento: tier GRATUITO (15 checks, KL-27) — o
    # score de evolução tem que ser comparável ao do alerta (também 15).
    report = await run_scan(url, full=False)
    if cache is not None:
        await cache.set(url, report, full=False)
    s = report.score
    new_score = s.score if s else None
    new_semaphore = s.semaphore if s else None
    fail_count = s.failed if s else 0
    if s is not None:
        scan_id = await store.save_scan(
            target_id, url, s.score, s.semaphore, s.passed, s.failed,
            s.inconclusive, report.to_dict(), source="rescan")
        await store.update_scan_result(target_id, scan_id, s.score)

    evolution = classify_evolution(old_score, new_score)
    sev = severity_counts_from_checks(report.to_dict())
    risks = get_risk_messages(report)

    email_id = None
    if send_email and mailer is not None and target.get("contact_email"):
        res = await mailer.send_evolution(
            target["contact_email"], url,
            old_score if old_score is not None else new_score, new_score,
            evolution, new_semaphore, fail_count, sev,
            price_display_for_tier(target.get("price_tier")),
            unsubscribe_link=_unsub_link(target["contact_email"]),
            risk_messages=risks, target_id=target_id)
        email_id = res.get("email_id")

    await store.log_rescan(target_id, old_score, new_score, evolution,
                           old_semaphore, new_semaphore, email_id)
    if email_id:
        # Evita alerta duplicado do Alert Worker dentro da janela de 30 dias.
        await store.mark_target_contacted(target_id)

    # KL-29: atingiu 100 no re-scan → confere no completo e oferece monitoramento.
    offered = False
    if new_score == 100:
        try:
            offered = await _maybe_offer_monitoring(store, mailer, target)
        except Exception as exc:  # noqa: BLE001 - oferta é best-effort
            print(f"[monitor] erro na oferta de {url}: {exc!r}", flush=True)

    return {"target_id": target_id, "url": url, "evolution": evolution,
            "old_score": old_score, "new_score": new_score,
            "email_id": email_id, "sent": email_id is not None,
            "monitoring_offered": offered}


class RescanWorker:
    """Reescaneia alvos a cada 30 dias e envia e-mail de evolução (ciclo de 24h)."""

    def __init__(self) -> None:
        self.interval_hours = int(os.environ.get("RESCAN_INTERVAL_HOURS", "24"))
        self.age_days = int(os.environ.get("RESCAN_AGE_DAYS", "30"))
        # Cota mensal + batch de e-mail compartilhados com o Alert Worker (KL-23).
        self.monthly_limit = int(os.environ.get("ALERT_MONTHLY_LIMIT", "45000"))
        self.email_batch_size = int(os.environ.get("ALERT_BATCH_SIZE", "50"))
        self.batch_pause = float(os.environ.get("ALERT_BATCH_PAUSE", "10"))
        max_scans = int(os.environ.get("WORKER_MAX_SCANS_PER_HOUR", "50"))
        self.pause_s = 3600.0 / max_scans if max_scans > 0 else 0.0
        self.batch = int(os.environ.get("RESCAN_BATCH_SIZE", "50"))
        # KL-29/KL-31: re-scan dos sites monitorados (score 100) a cada 30 dias.
        self.monitor_interval_days = int(os.environ.get("MONITOR_INTERVAL_DAYS", "30"))
        self.store = get_target_store()
        self._cache_obj: Optional[ScanCache] = None
        self._last_cycle_at = None
        self._next_cycle_at = None
        self._last_cycle_stats: dict = {}
        self._last_monitor_stats: dict = {}

    def _hb_payload(self) -> dict:
        return {
            "last_cycle_at": self._last_cycle_at.isoformat() if self._last_cycle_at else None,
            "next_cycle_at": self._next_cycle_at.isoformat() if self._next_cycle_at else None,
            "last_cycle_stats": self._last_cycle_stats,
        }

    async def _heartbeat_loop(self) -> None:
        while True:
            await publish_heartbeat("rescan", self._hb_payload())
            await asyncio.sleep(60)

    def _mailer(self) -> Optional[KlarimMailer]:
        key = os.environ.get("RESEND_API_KEY")
        return KlarimMailer(key, os.environ.get("RESEND_FROM") or None) if key else None

    async def _cache(self) -> Optional[ScanCache]:
        if self._cache_obj is None:
            try:
                import redis.asyncio as aioredis

                client = aioredis.from_url(
                    os.environ.get("REDIS_URL", "redis://redis:6379/0"), decode_responses=True)
                self._cache_obj = ScanCache(client)
            except Exception as exc:  # noqa: BLE001 - cache é best-effort
                print(f"[rescan] cache indisponível: {exc!r}", flush=True)
        return self._cache_obj

    def _evolution_payload(self, p: Dict[str, Any]) -> Dict[str, Any]:
        """Monta o dict de evolução (para o batch) a partir de uma pendência."""
        return {
            "rescan_id": p["rescan_id"], "target_id": p.get("target_id"),
            "to_email": p["contact_email"], "target_url": p["url"],
            "old_score": p["old_score"] if p["old_score"] is not None else p["new_score"],
            "new_score": p["new_score"], "evolution": p["evolution"],
            "semaphore": p["new_semaphore"], "fail_count": p.get("fail_count") or 0,
            "severity_counts": severity_counts_from_checks(p.get("checks_json")),
            "price_display": price_display_for_tier(p.get("price_tier")),
            "unsubscribe_link": _unsub_link(p["contact_email"]),
            "risk_messages": get_risk_messages((p.get("checks_json") or {}).get("results", [])),
        }

    async def _flush_pending_batch(self, mailer: KlarimMailer) -> int:
        """Despacha os e-mails de evolução pendentes em LOTE (KL-23).

        Inclui as pendências recém-criadas neste ciclo e as de ciclos anteriores.
        Respeita a cota mensal compartilhada; o que não couber fica pendente.
        """
        sent_month = await self.store.count_proactive_emails_this_month()
        room = self.monthly_limit - sent_month
        if room <= 0:
            print(f"[rescan] cota mensal atingida ({sent_month}/{self.monthly_limit}); "
                  f"e-mails de evolução adiados", flush=True)
            return 0

        cap = self.email_batch_size * int(os.environ.get("ALERT_BATCHES_PER_CYCLE", "4"))
        pending = await self.store.get_pending_evolution_emails(
            days=self.age_days, limit=min(cap, room))
        resent = 0
        for i in range(0, len(pending), self.email_batch_size):
            if resent >= room:
                break
            chunk = pending[i:i + self.email_batch_size][:room - resent]
            evolutions = [self._evolution_payload(p) for p in chunk]
            try:
                res = await mailer.send_evolution_batch(evolutions)
            except Exception as exc:  # noqa: BLE001 - batch ruim não derruba o ciclo
                print(f"[rescan] batch de evolução falhou: {exc!r}", flush=True)
                continue
            ids = res.get("ids") or []
            for j, e in enumerate(evolutions):
                email_id = ids[j] if j < len(ids) else None
                if email_id:
                    await self.store.update_rescan_email(e["rescan_id"], email_id)
                    await self.store.mark_target_contacted(e["target_id"])
                    resent += 1
            print(f"[rescan] batch de evolução: {res.get('sent', 0)} enviados", flush=True)
            if i + self.email_batch_size < len(pending):
                await asyncio.sleep(self.batch_pause)
        return resent

    async def run_cycle(self) -> dict:
        stats = {"eligible": 0, "rescanned": 0, "emailed": 0, "errors": 0}
        # Controle centralizado (KL-32): pausa por MCP/painel.
        if not worker_control.is_enabled("rescan"):
            print("[rescan] worker pausado (worker_control); pulando ciclo", flush=True)
            stats["disabled"] = True
            return stats
        mailer = self._mailer()
        cache = await self._cache()

        # 1. Reescaneia cada alvo (e-mail adiado — sai em batch no passo 2).
        targets = await self.store.get_targets_for_rescan(self.age_days, limit=self.batch)
        stats["eligible"] = len(targets)
        for t in targets:
            try:
                res = await rescan_target(self.store, mailer, cache, t, send_email=False)
                stats["rescanned"] += 1
                print(f"[rescan] {t['url']}: {res['old_score']}→{res['new_score']} "
                      f"({res['evolution']})", flush=True)
                await asyncio.sleep(self.pause_s)  # mesmo rate limit do scan worker
            except Exception as exc:  # noqa: BLE001 - um alvo ruim não derruba o ciclo
                stats["errors"] += 1
                print(f"[rescan] erro em {t.get('url')}: {exc!r}", flush=True)

        # 2. Despacha os e-mails de evolução pendentes em batch. O kill-switch
        # (STOP_ALERTS) segura só os e-mails — o re-scan (dados) continua; as
        # evoluções ficam pendentes e saem quando o flag for removido.
        if mailer is not None and alerts_stopped():
            stats["paused_by_flag"] = True
            print("[rescan] STOP_ALERTS ativo; e-mails de evolução adiados", flush=True)
        elif mailer is not None:
            stats["emailed"] = await self._flush_pending_batch(mailer)

        print(f"[rescan] ciclo concluído: {stats}", flush=True)
        return stats

    # --- ciclo de monitoramento (KL-29) ------------------------------------ #

    async def _monitor_cycle(self) -> dict:
        """Re-scan COMPLETO (29) dos sites monitorados: <100 suspende + alerta;
        suspenso que volta a 100 é restaurado + e-mail."""
        stats = {"checked": 0, "suspended": 0, "restored": 0, "errors": 0}
        # Controle KL-32: pausa junto com o rescan worker (ambos vivem no mesmo loop).
        if not worker_control.is_enabled("rescan"):
            stats["disabled"] = True
            return stats
        mailer = self._mailer()
        try:
            sites = await self.store.get_monitored_for_rescan()
        except Exception as exc:  # noqa: BLE001
            print(f"[monitor] falha ao listar sites monitorados: {exc!r}", flush=True)
            return stats

        for site in sites:
            try:
                report = await run_scan(site["url"], full=True)
                score = report.score.score if report.score else 0
                await self.store.update_monitor_check(site["id"], score)
                stats["checked"] += 1
                email = site.get("contact_email")
                domain = site["domain"]

                if score < 100 and site["status"] == "active":
                    await self.store.suspend_monitored_site(
                        site["id"], f"Score caiu para {score}/100")
                    stats["suspended"] += 1
                    print(f"[monitor] {domain} suspenso (score {score})", flush=True)
                    if mailer and email:
                        await mailer.send_monitor_alert(
                            email, domain, score, _monitor_result_url(site["url"]),
                            _monitor_remove_url(domain))
                elif score == 100 and site["status"] == "suspended":
                    if await self.store.restore_monitored_site(site["id"]):
                        stats["restored"] += 1
                        print(f"[monitor] {domain} restaurado (100/100)", flush=True)
                        if mailer and email:
                            await mailer.send_monitor_restored(
                                email, domain, _monitor_result_url(site["url"]),
                                _monitor_remove_url(domain))
                await asyncio.sleep(self.pause_s)
            except Exception as exc:  # noqa: BLE001 - um site ruim não derruba o ciclo
                stats["errors"] += 1
                print(f"[monitor] erro em {site.get('domain')}: {exc!r}", flush=True)

        self._last_monitor_stats = stats
        print(f"[monitor] ciclo concluído: {stats}", flush=True)
        return stats

    async def _monitor_loop(self) -> None:
        while True:
            try:
                await self._monitor_cycle()
            except Exception as exc:  # noqa: BLE001
                print(f"[monitor] ciclo falhou: {exc!r}", flush=True)
            await asyncio.sleep(self.monitor_interval_days * 86400)

    async def start(self) -> None:
        print(f"[rescan] iniciado (idade {self.age_days}d, intervalo {self.interval_hours}h, "
              f"batch e-mail {self.email_batch_size}, limite {self.monthly_limit // 1000}k/mês; "
              f"monitor a cada {self.monitor_interval_days}d)",
              flush=True)
        asyncio.create_task(self._heartbeat_loop())
        asyncio.create_task(self._monitor_loop())
        while True:
            try:
                self._last_cycle_stats = await self.run_cycle()
            except Exception as exc:  # noqa: BLE001
                print(f"[rescan] ciclo falhou: {exc!r}", flush=True)
            self._last_cycle_at = datetime.now(timezone.utc)
            self._next_cycle_at = self._last_cycle_at + timedelta(hours=self.interval_hours)
            await publish_heartbeat("rescan", self._hb_payload())
            await asyncio.sleep(self.interval_hours * 3600)
