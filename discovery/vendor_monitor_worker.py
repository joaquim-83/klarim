"""KL-152 P3 — worker de monitoramento de fornecedores (Enterprise).

Roda 1x/dia: re-escaneia os vendors com `monitor_enabled` cujo `next_monitor_at` já passou, e
alerta o Enterprise quando o score cai abaixo do threshold de aprovação. O scan/persistência
reusa `api.gate.run_vendor_scan` (que já reprograma o `next_monitor_at`). Deps injetáveis p/ teste.
"""
from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone
from typing import Optional

from discovery.store import get_target_store


class VendorMonitorWorker:
    def __init__(self, store=None, scan_fn=None, mailer_fn=None,
                 interval_hours: Optional[int] = None):
        self._store = store
        self._scan_fn = scan_fn          # async (account_id, vendor) -> dict com 'score'
        self._mailer_fn = mailer_fn      # () -> mailer
        self.interval_hours = int(interval_hours
                                  or os.environ.get("VENDOR_MONITOR_INTERVAL_HOURS", "24"))

    @property
    def store(self):
        return self._store or get_target_store()

    async def _scan(self, account_id: int, vendor: dict) -> dict:
        if self._scan_fn is not None:
            return await self._scan_fn(account_id, vendor)
        from api.gate import run_vendor_scan   # lazy: evita puxar FastAPI no import do worker
        return await run_vendor_scan(account_id, vendor)

    def _mailer(self):
        if self._mailer_fn is not None:
            return self._mailer_fn()
        import api.main as _m
        return _m._mailer()

    async def run_cycle(self, now: Optional[datetime] = None) -> dict:
        now = now or datetime.now(timezone.utc)
        vendors = await self.store.get_vendors_due_for_monitoring(now, limit=100)
        scanned = alerted = errors = 0
        for v in vendors:
            try:
                prev = v.get("last_scan_score")
                scan = await self._scan(v["account_id"], v)
                scanned += 1
                score = scan.get("score")
                threshold = int(v.get("approval_threshold") or 80)
                if score is not None and int(score) < threshold:
                    if await self._alert_enterprise(v, int(score), prev):
                        alerted += 1
            except Exception as exc:  # noqa: BLE001 - um vendor não derruba o ciclo
                errors += 1
                print(f"[vendor-monitor] falha no vendor {v.get('id')}: {exc!r}", flush=True)
        return {"due": len(vendors), "scanned": scanned, "alerted": alerted, "errors": errors}

    async def _alert_enterprise(self, vendor: dict, score: int, prev) -> bool:
        prof = await self.store.get_enterprise_profile(vendor["account_id"])
        if not prof or not prof.get("email"):
            return False
        mailer = self._mailer()
        if mailer is None:
            return False
        await mailer.send_vendor_score_drop(
            prof["email"], vendor.get("name") or vendor.get("domain"), vendor.get("domain"),
            score, int(vendor.get("approval_threshold") or 80), prev)
        return True

    async def start(self) -> None:
        while True:
            try:
                await self.run_cycle()
            except Exception as exc:  # noqa: BLE001 - o loop nunca morre
                print(f"[vendor-monitor] ciclo falhou: {exc!r}", flush=True)
            await asyncio.sleep(self.interval_hours * 3600)
