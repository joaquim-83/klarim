"""Alert Worker — dispara o alerta gratuito por e-mail para alvos escaneados.

Elegibilidade: status='scanned', com FALHAS, com e-mail, sem alerta nos últimos
30 dias, não 'unsubscribed'.

Envio em LOTE (KL-23, Resend Pro): cada ciclo busca TODOS os alvos elegíveis (sem
cap artificial por ciclo/hora/dia), agrupa em batches de ``ALERT_BATCH_SIZE`` e
envia cada batch em 1 request via `KlarimMailer.send_alert_batch`. O único teto é
a **cota mensal** (`ALERT_MONTHLY_LIMIT`, compartilhada com os e-mails de evolução
do Re-scan Worker) — reserva de segurança dentro do limite de 50k/mês do Resend Pro.
"""

from __future__ import annotations

import asyncio
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from notifier import KlarimMailer, build_unsubscribe_link
from reporter.risk_messages import get_risk_messages
from .store import get_target_store
from .heartbeat import publish_heartbeat

_SEV_MAP = {"CRITICA": "critica", "ALTA": "alta", "MEDIA": "media", "BAIXA": "baixa"}


def severity_counts_from_checks(checks_json: Optional[dict]) -> Dict[str, int]:
    counts = {"critica": 0, "alta": 0, "media": 0, "baixa": 0}
    for r in (checks_json or {}).get("results", []):
        if r.get("status") == "FAIL":
            key = _SEV_MAP.get(r.get("severity"))
            if key:
                counts[key] += 1
    return counts


async def build_alert_payload(store, target: Dict[str, Any]) -> Dict[str, Any]:
    """Monta o dict de alerta (para o batch) a partir de um alvo elegível.

    Reusa os campos já trazidos pelo JOIN de `get_eligible_targets_for_alert`
    (``scan_checks``/``scan_semaphore``/``scan_fail_count``); cai para `get_scan`
    se algum faltar (ex.: alvo vindo de `get_target`, sem o JOIN).
    """
    email = target.get("contact_email")
    if not email:
        raise ValueError("alvo sem e-mail")

    checks = target.get("scan_checks")
    semaphore = target.get("scan_semaphore")
    fail_count = target.get("scan_fail_count")
    score = target.get("last_scan_score")
    if checks is None or score is None:
        scan = await store.get_scan(target["last_scan_id"]) if target.get("last_scan_id") else None
        if scan is None:
            raise ValueError("alvo sem scan")
        checks = scan.get("checks_json") if checks is None else checks
        semaphore = semaphore or scan.get("semaphore")
        fail_count = scan.get("fail_count") if fail_count is None else fail_count
        score = scan.get("score") if score is None else score

    sev = severity_counts_from_checks(checks)
    risks = get_risk_messages((checks or {}).get("results", []))
    secret = os.environ.get("UNSUBSCRIBE_SECRET")
    unsub = build_unsubscribe_link(email, secret) if secret else None
    return {
        "target_id": target["id"], "to_email": email, "target_url": target["url"],
        "score": score or 0, "semaphore": semaphore or "", "fail_count": fail_count or 0,
        "severity_counts": sev, "risk_messages": risks, "unsubscribe_link": unsub,
    }


async def send_alert_for_target(store, mailer: KlarimMailer, target: Dict[str, Any]) -> Optional[str]:
    """Envia o alerta de UM alvo (envio único), marca 'alerted' e registra no log.

    Usado pelos disparos manuais da API (`/targets/{id}/alert`, `/admin/resend-alert`)
    — o batch é só para o ciclo automático do worker.
    """
    email = target.get("contact_email")
    if not email:
        raise ValueError("alvo sem e-mail")
    scan = await store.get_scan(target["last_scan_id"]) if target.get("last_scan_id") else None
    if scan is None:
        raise ValueError("alvo sem scan")

    score, semaphore, fail_count = scan["score"], scan["semaphore"], scan["fail_count"]
    sev = severity_counts_from_checks(scan.get("checks_json"))
    risks = get_risk_messages((scan.get("checks_json") or {}).get("results", []))
    secret = os.environ.get("UNSUBSCRIBE_SECRET")
    unsub = build_unsubscribe_link(email, secret) if secret else None

    res = await mailer.send_alert(email, target["url"], score, semaphore, fail_count, sev,
                                  unsubscribe_link=unsub, risk_messages=risks,
                                  target_id=target["id"])
    email_id = res.get("email_id")
    await store.mark_target_alerted(target["id"])
    await store.log_alert(target["id"], email, score, semaphore, fail_count, email_id)
    return email_id


class AlertWorker:
    def __init__(self) -> None:
        # Batch sending (KL-23 / Resend Pro).
        self.batch_size = int(os.environ.get("ALERT_BATCH_SIZE", "50"))
        self.batches_per_cycle = int(os.environ.get("ALERT_BATCHES_PER_CYCLE", "4"))
        self.batch_pause = float(os.environ.get("ALERT_BATCH_PAUSE", "10"))
        self.monthly_limit = int(os.environ.get("ALERT_MONTHLY_LIMIT", "45000"))
        # Intervalo em minutos tem precedência; ALERT_INTERVAL_HOURS é o fallback.
        interval_minutes = int(os.environ.get("ALERT_INTERVAL_MINUTES", "0"))
        if not interval_minutes:
            interval_minutes = int(os.environ.get("ALERT_INTERVAL_HOURS", "1")) * 60
        self.interval_minutes = interval_minutes or 30
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
        stats = {"eligible": 0, "batches": 0, "sent": 0, "failed": 0,
                 "errors": 0, "skipped": 0}
        mailer = self._mailer()
        if mailer is None:
            print("[alert] RESEND_API_KEY não configurada; ciclo pulado", flush=True)
            return stats

        # Cota mensal GLOBAL (alertas + evolução). Único teto no plano Pro.
        sent_month = await self.store.count_proactive_emails_this_month()
        if sent_month >= self.monthly_limit:
            print(f"[alert] cota mensal atingida ({sent_month}/{self.monthly_limit}); "
                  f"ciclo pulado", flush=True)
            return stats

        # Busca só o que cabe no ciclo e na cota mensal restante.
        cycle_cap = self.batch_size * self.batches_per_cycle
        want = min(cycle_cap, self.monthly_limit - sent_month)
        targets = await self.store.get_eligible_targets_for_alert(limit=want)
        stats["eligible"] = len(targets)

        for bi in range(self.batches_per_cycle):
            chunk = targets[bi * self.batch_size:(bi + 1) * self.batch_size]
            if not chunk:
                break
            # Respeita a cota mensal em tempo real (recalculada com o que já enviamos).
            room = self.monthly_limit - (sent_month + stats["sent"])
            if room <= 0:
                stats["skipped"] += len(chunk)
                break
            if len(chunk) > room:
                stats["skipped"] += len(chunk) - room
                chunk = chunk[:room]

            # Monta os payloads; um alvo ruim é pulado sem derrubar o batch.
            alerts = []
            for t in chunk:
                try:
                    alerts.append(await build_alert_payload(self.store, t))
                except Exception as exc:  # noqa: BLE001
                    stats["errors"] += 1
                    print(f"[alert] pulando {t.get('url')}: {exc!r}", flush=True)
            if not alerts:
                continue

            try:
                res = await mailer.send_alert_batch(alerts)
            except Exception as exc:  # noqa: BLE001 - batch ruim não derruba o ciclo
                stats["errors"] += 1
                print(f"[alert] batch {bi + 1} falhou: {exc!r}", flush=True)
                for a in alerts:
                    await self.store.log_alert(
                        a["target_id"], a["to_email"], a.get("score"),
                        a.get("semaphore"), a.get("fail_count"), None, status="failed")
                continue

            # Resposta 2xx => todos os e-mails foram aceitos (batch é atômico no Resend).
            ids = res.get("ids") or []
            for i, a in enumerate(alerts):
                email_id = ids[i] if i < len(ids) else None
                await self.store.mark_target_alerted(a["target_id"])
                await self.store.log_alert(a["target_id"], a["to_email"], a.get("score"),
                                           a.get("semaphore"), a.get("fail_count"), email_id)
            stats["batches"] += 1
            stats["sent"] += res.get("sent", 0)
            stats["failed"] += res.get("failed", 0)
            print(f"[alert] batch {stats['batches']}: {res.get('sent', 0)} enviados", flush=True)

            if bi < self.batches_per_cycle - 1:
                await asyncio.sleep(self.batch_pause)

        remaining = await self.store.count_eligible_targets_for_alert()
        print(f"[alert] ciclo: {stats['batches']} batches, {stats['sent']} enviados, "
              f"{remaining} restantes, mês {sent_month + stats['sent']}/{self.monthly_limit}",
              flush=True)
        return stats

    async def start(self) -> None:
        print(f"[alert] iniciado (batch {self.batch_size}, {self.batches_per_cycle} batches/ciclo, "
              f"pausa {int(self.batch_pause)}s, intervalo {self.interval_minutes}min, "
              f"limite {self.monthly_limit // 1000}k/mês)", flush=True)
        asyncio.create_task(self._heartbeat_loop())
        while True:
            try:
                self._last_cycle_stats = await self.run_cycle()
            except Exception as exc:  # noqa: BLE001
                print(f"[alert] ciclo falhou: {exc!r}", flush=True)
            self._last_cycle_at = datetime.now(timezone.utc)
            self._next_cycle_at = self._last_cycle_at + timedelta(minutes=self.interval_minutes)
            await publish_heartbeat("alert", self._hb_payload())
            await asyncio.sleep(self.interval_minutes * 60)
