"""Re-scan Worker (KL-13) — reescaneia alvos a cada 30 dias e envia e-mail de evolução.

Fecha o ciclo de vida do alvo: sites já engajados (scanned/alerted) são
reescaneados após 30 dias; o score novo é comparado ao anterior e um e-mail conta
a história (melhorou / piorou / permaneceu igual), reativando a conversão sem
precisar descobrir alvos novos.

Compartilha o throttle GLOBAL de e-mails proativos com o Alert Worker
(`count_proactive_emails_last_hours`). Se o teto estiver batido, o re-scan
acontece mesmo assim (dados atualizados) e o e-mail fica pendente
(`rescan_log.email_id IS NULL`) para reenvio no próximo ciclo.
"""

from __future__ import annotations

import asyncio
import os
from typing import Any, Dict, Optional

from scanner import run_scan
from scanner.cache import ScanCache
from notifier import KlarimMailer, build_unsubscribe_link
from payments import PRICING, DEFAULT_TIER, amount_display
from .store import get_target_store
from .alert_worker import severity_counts_from_checks


# Pausa entre reenvios de e-mails pendentes (protege a reputação do domínio).
EVOLUTION_PAUSE_SECONDS = float(os.environ.get("EVOLUTION_PAUSE_SECONDS", "5"))


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

    report = await run_scan(url)
    if cache is not None:
        await cache.set(url, report)
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

    email_id = None
    if send_email and mailer is not None and target.get("contact_email"):
        res = await mailer.send_evolution(
            target["contact_email"], url,
            old_score if old_score is not None else new_score, new_score,
            evolution, new_semaphore, fail_count, sev,
            price_display_for_tier(target.get("price_tier")),
            unsubscribe_link=_unsub_link(target["contact_email"]))
        email_id = res.get("email_id")

    await store.log_rescan(target_id, old_score, new_score, evolution,
                           old_semaphore, new_semaphore, email_id)
    if email_id:
        # Evita alerta duplicado do Alert Worker dentro da janela de 30 dias.
        await store.mark_target_contacted(target_id)

    return {"target_id": target_id, "url": url, "evolution": evolution,
            "old_score": old_score, "new_score": new_score,
            "email_id": email_id, "sent": email_id is not None}


class RescanWorker:
    """Reescaneia alvos a cada 30 dias e envia e-mail de evolução (ciclo de 24h)."""

    def __init__(self) -> None:
        self.interval_hours = int(os.environ.get("RESCAN_INTERVAL_HOURS", "24"))
        self.age_days = int(os.environ.get("RESCAN_AGE_DAYS", "30"))
        self.max_hour = int(os.environ.get("MAX_ALERTS_PER_HOUR", "10"))
        self.max_day = int(os.environ.get("MAX_ALERTS_PER_DAY", "50"))
        max_scans = int(os.environ.get("WORKER_MAX_SCANS_PER_HOUR", "50"))
        self.pause_s = 3600.0 / max_scans if max_scans > 0 else 0.0
        self.batch = int(os.environ.get("RESCAN_BATCH_SIZE", "50"))
        self.store = get_target_store()
        self._cache_obj: Optional[ScanCache] = None

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

    async def _throttle_ok(self) -> bool:
        h = await self.store.count_proactive_emails_last_hours(1)
        d = await self.store.count_proactive_emails_last_hours(24)
        return h < self.max_hour and d < self.max_day

    async def _flush_pending(self, mailer: KlarimMailer) -> int:
        """Reenvia e-mails de evolução que ficaram pendentes por throttle."""
        pending = await self.store.get_pending_evolution_emails(days=self.age_days, limit=self.batch)
        resent = 0
        for p in pending:
            if not await self._throttle_ok():
                break  # ainda no teto; tenta no próximo ciclo
            try:
                sev = severity_counts_from_checks(p.get("checks_json"))
                res = await mailer.send_evolution(
                    p["contact_email"], p["url"],
                    p["old_score"] if p["old_score"] is not None else p["new_score"],
                    p["new_score"], p["evolution"], p["new_semaphore"],
                    p.get("fail_count") or 0, sev,
                    price_display_for_tier(p.get("price_tier")),
                    unsubscribe_link=_unsub_link(p["contact_email"]))
                email_id = res.get("email_id")
                if email_id:
                    await self.store.update_rescan_email(p["rescan_id"], email_id)
                    await self.store.mark_target_contacted(p["target_id"])
                    resent += 1
                await asyncio.sleep(EVOLUTION_PAUSE_SECONDS)
            except Exception as exc:  # noqa: BLE001
                print(f"[rescan] falha ao reenviar pendente {p.get('url')}: {exc!r}", flush=True)
        return resent

    async def run_cycle(self) -> dict:
        stats = {"eligible": 0, "rescanned": 0, "emailed": 0, "deferred": 0,
                 "errors": 0, "pending_resent": 0}
        mailer = self._mailer()
        cache = await self._cache()

        if mailer is not None:
            stats["pending_resent"] = await self._flush_pending(mailer)

        targets = await self.store.get_targets_for_rescan(self.age_days, limit=self.batch)
        stats["eligible"] = len(targets)

        for t in targets:
            try:
                can_email = mailer is not None and await self._throttle_ok()
                res = await rescan_target(self.store, mailer, cache, t, send_email=can_email)
                stats["rescanned"] += 1
                if res["sent"]:
                    stats["emailed"] += 1
                    print(f"[rescan] {t['url']}: {res['old_score']}→{res['new_score']} "
                          f"({res['evolution']}, e-mail {res['email_id']})", flush=True)
                elif mailer is not None:
                    stats["deferred"] += 1  # rescan feito; e-mail adiado (throttle)
                    print(f"[rescan] {t['url']}: {res['evolution']} — e-mail adiado (throttle)", flush=True)
                await asyncio.sleep(self.pause_s)  # mesmo rate limit do scan worker
            except Exception as exc:  # noqa: BLE001 - um alvo ruim não derruba o ciclo
                stats["errors"] += 1
                print(f"[rescan] erro em {t.get('url')}: {exc!r}", flush=True)

        print(f"[rescan] ciclo concluído: {stats}", flush=True)
        return stats

    async def start(self) -> None:
        print(f"[rescan] iniciado (idade {self.age_days}d, intervalo {self.interval_hours}h, "
              f"teto {self.max_hour}/h {self.max_day}/dia)", flush=True)
        while True:
            try:
                await self.run_cycle()
            except Exception as exc:  # noqa: BLE001
                print(f"[rescan] ciclo falhou: {exc!r}", flush=True)
            await asyncio.sleep(self.interval_hours * 3600)
