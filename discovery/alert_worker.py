"""Alert Worker — dispara o alerta gratuito por e-mail para alvos escaneados.

Elegibilidade: status='scanned', com FALHAS, com e-mail, sem alerta nos últimos
30 dias, não 'unsubscribed'. Com throttle (por hora/dia) e pausa de 5s entre
envios para proteger a reputação do domínio.
"""

from __future__ import annotations

import asyncio
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from notifier import KlarimMailer, build_unsubscribe_link
from .store import get_target_store
from .heartbeat import publish_heartbeat

_SEV_MAP = {"CRITICA": "critica", "ALTA": "alta", "MEDIA": "media", "BAIXA": "baixa"}

# Pausa mínima entre e-mails (parecer orgânico + não sobrecarregar o Resend).
ALERT_PAUSE_SECONDS = float(os.environ.get("ALERT_PAUSE_SECONDS", "5"))


def severity_counts_from_checks(checks_json: Optional[dict]) -> Dict[str, int]:
    counts = {"critica": 0, "alta": 0, "media": 0, "baixa": 0}
    for r in (checks_json or {}).get("results", []):
        if r.get("status") == "FAIL":
            key = _SEV_MAP.get(r.get("severity"))
            if key:
                counts[key] += 1
    return counts


async def send_alert_for_target(store, mailer: KlarimMailer, target: Dict[str, Any]) -> Optional[str]:
    """Envia o alerta de um alvo, marca 'alerted' e registra em alert_log."""
    email = target.get("contact_email")
    if not email:
        raise ValueError("alvo sem e-mail")
    scan = await store.get_scan(target["last_scan_id"]) if target.get("last_scan_id") else None
    if scan is None:
        raise ValueError("alvo sem scan")

    score, semaphore, fail_count = scan["score"], scan["semaphore"], scan["fail_count"]
    sev = severity_counts_from_checks(scan.get("checks_json"))
    secret = os.environ.get("UNSUBSCRIBE_SECRET")
    unsub = build_unsubscribe_link(email, secret) if secret else None

    res = await mailer.send_alert(email, target["url"], score, semaphore, fail_count, sev,
                                  unsubscribe_link=unsub)
    email_id = res.get("email_id")
    await store.mark_target_alerted(target["id"])
    await store.log_alert(target["id"], email, score, semaphore, fail_count, email_id)
    return email_id


class AlertWorker:
    def __init__(self) -> None:
        self.max_hour = int(os.environ.get("MAX_ALERTS_PER_HOUR", "10"))
        self.max_day = int(os.environ.get("MAX_ALERTS_PER_DAY", "50"))
        self.interval_hours = int(os.environ.get("ALERT_INTERVAL_HOURS", "1"))
        self.store = get_target_store()
        self._last_cycle_at = None
        self._next_cycle_at = None
        self._last_cycle_stats: dict = {}

    def _hb_payload(self) -> dict:
        return {
            "last_cycle_at": self._last_cycle_at.isoformat() if self._last_cycle_at else None,
            "next_cycle_at": self._next_cycle_at.isoformat() if self._next_cycle_at else None,
            "last_cycle_stats": self._last_cycle_stats,
        }

    async def _heartbeat_loop(self) -> None:
        while True:
            await publish_heartbeat("alert", self._hb_payload())
            await asyncio.sleep(60)

    def _mailer(self) -> Optional[KlarimMailer]:
        key = os.environ.get("RESEND_API_KEY")
        return KlarimMailer(key, os.environ.get("RESEND_FROM") or None) if key else None

    async def run_cycle(self) -> dict:
        stats = {"eligible": 0, "sent": 0, "throttled": 0, "errors": 0}
        mailer = self._mailer()
        if mailer is None:
            print("[alert] RESEND_API_KEY não configurada; ciclo pulado", flush=True)
            return stats

        # Throttle GLOBAL: alertas + e-mails de evolução (KL-13) somam no mesmo teto.
        sent_hour = await self.store.count_proactive_emails_last_hours(1)
        sent_day = await self.store.count_proactive_emails_last_hours(24)
        if sent_hour >= self.max_hour or sent_day >= self.max_day:
            print(f"[alert] limite atingido (hora={sent_hour}/{self.max_hour}, "
                  f"dia={sent_day}/{self.max_day}); aguardando", flush=True)
            return stats

        targets = await self.store.get_eligible_targets_for_alert(limit=self.max_day)
        stats["eligible"] = len(targets)

        for t in targets:
            if sent_hour >= self.max_hour or sent_day >= self.max_day:
                stats["throttled"] += 1
                continue
            try:
                email_id = await send_alert_for_target(self.store, mailer, t)
                stats["sent"] += 1
                sent_hour += 1
                sent_day += 1
                print(f"[alert] enviado para {t['contact_email']} ({t['url']}, id={email_id})", flush=True)
                await asyncio.sleep(ALERT_PAUSE_SECONDS)
            except Exception as exc:  # noqa: BLE001 - um alvo ruim não derruba o ciclo
                stats["errors"] += 1
                await self.store.log_alert(
                    t.get("id"), t.get("contact_email", ""), t.get("last_scan_score"),
                    None, None, None, status="failed")
                print(f"[alert] falha em {t.get('url')}: {exc!r}", flush=True)

        print(f"[alert] ciclo concluído: {stats}", flush=True)
        return stats

    async def start(self) -> None:
        print(f"[alert] iniciado (max {self.max_hour}/h, {self.max_day}/dia, "
              f"intervalo {self.interval_hours}h)", flush=True)
        asyncio.create_task(self._heartbeat_loop())
        while True:
            try:
                self._last_cycle_stats = await self.run_cycle()
            except Exception as exc:  # noqa: BLE001
                print(f"[alert] ciclo falhou: {exc!r}", flush=True)
            self._last_cycle_at = datetime.now(timezone.utc)
            self._next_cycle_at = self._last_cycle_at + timedelta(hours=self.interval_hours)
            await publish_heartbeat("alert", self._hb_payload())
            await asyncio.sleep(self.interval_hours * 3600)
