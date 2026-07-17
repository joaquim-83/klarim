"""Trial Worker (KL-44 P6) — expiração de trial + avisos.

Decisão de produto (Opção A): trial expirado → **downgrade silencioso para Free**
(vigílias avançadas desativadas, limite cai para 1 site, **dados preservados**, sem
bloqueio). Avisa 7 dias e 1 dia antes; no dia, rebaixa e avisa.

Roda no container `discovery` (junto dos outros workers via `asyncio.gather`), verifica de
hora em hora e **age uma vez por dia** às `TRIAL_HOUR_UTC` (06h UTC ≈ 03h BRT, antes do
horário comercial). Controlado por `worker_control` + flag `TRIAL_EXPIRATION_ENABLED`.
Best-effort: um e-mail/downgrade ruim não derruba o ciclo.
"""

from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from notifier import KlarimMailer
from .heartbeat import publish_heartbeat
from .store import get_target_store
from . import worker_control

from api import plans as _plans   # import tardio evita puxar api.main no import do pacote


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class TrialWorker:
    def __init__(self) -> None:
        self.hour_utc = int(os.environ.get("TRIAL_HOUR_UTC", "6"))
        self.store = get_target_store()
        self._last_cycle_at: Optional[datetime] = None
        self._last_cycle_stats: Dict[str, Any] = {}

    def _mailer(self) -> Optional[KlarimMailer]:
        key = os.environ.get("RESEND_API_KEY")
        return KlarimMailer(key, os.environ.get("RESEND_FROM") or None) if key else None

    def _hb_payload(self) -> Dict[str, Any]:
        return {"last_cycle_at": self._last_cycle_at.isoformat() if self._last_cycle_at else None,
                "last_cycle_stats": self._last_cycle_stats}

    async def _heartbeat_loop(self) -> None:
        while True:
            await publish_heartbeat("trial", self._hb_payload())
            await asyncio.sleep(60)

    async def _enabled(self) -> bool:
        if not worker_control.is_enabled("trial"):
            return False
        try:
            v = await self.store.get_setting("TRIAL_EXPIRATION_ENABLED", "true")
            return str(v).strip().lower() not in ("false", "0", "no")
        except Exception:  # noqa: BLE001 - fail-open
            return True

    @staticmethod
    def _label(dt: Any) -> str:
        try:
            return dt.date().isoformat() if hasattr(dt, "date") else str(dt)[:10]
        except Exception:  # noqa: BLE001
            return ""

    async def run_cycle(self) -> Dict[str, Any]:
        stats = {"warned_7d": 0, "warned_1d": 0, "expired": 0, "errors": 0}
        if not await self._enabled():
            stats["disabled"] = True
            return stats
        now = _utcnow()
        if now.hour != self.hour_utc:
            return stats
        mailer = self._mailer()

        # Avisos 7d / 1d antes.
        for days, key in ((7, "warned_7d"), (1, "warned_1d")):
            try:
                rows = await self.store.get_trials_expiring_in(days)
            except Exception as exc:  # noqa: BLE001
                print(f"[trial] get_trials_expiring_in({days}) falhou: {exc!r}", flush=True)
                continue
            for r in rows:
                try:
                    if mailer and r.get("email"):
                        await mailer.send_trial_warning(r["email"], days, self._label(r.get("trial_ends_at")))
                        stats[key] += 1
                except Exception as exc:  # noqa: BLE001
                    stats["errors"] += 1
                    print(f"[trial] aviso {days}d falhou user={r.get('user_id')}: {exc!r}", flush=True)

        # Expirados → downgrade silencioso para Free.
        try:
            expired = await self.store.get_expired_trials()
        except Exception as exc:  # noqa: BLE001
            print(f"[trial] get_expired_trials falhou: {exc!r}", flush=True)
            expired = []
        for r in expired:
            uid = r.get("user_id")
            try:
                await _plans.change_plan(uid, "free", changed_by="system", reason="trial expirado")
                await self.store.disable_user_vigilias_except(uid, [])  # free = zero vigílias
                if mailer and r.get("email"):
                    await mailer.send_trial_expired(r["email"])
                stats["expired"] += 1
                print(f"[trial] expirado → free: user={uid}", flush=True)
            except Exception as exc:  # noqa: BLE001 - um ruim não para o ciclo
                stats["errors"] += 1
                print(f"[trial] downgrade falhou user={uid}: {exc!r}", flush=True)

        if any(stats[k] for k in ("warned_7d", "warned_1d", "expired")):
            print(f"[trial] ciclo: {stats}", flush=True)
        return stats

    async def start(self) -> None:
        try:
            await self.store.ensure_schema()
        except Exception as exc:  # noqa: BLE001
            print(f"[trial] ensure_schema: {exc!r} (seguindo)", flush=True)
        print(f"[trial] iniciado (age às {self.hour_utc}h UTC; trial expirado → free "
              "silencioso + avisos 7d/1d). Controlado por worker_control.", flush=True)
        asyncio.create_task(self._heartbeat_loop())
        await asyncio.sleep(int(os.environ.get("TRIAL_WARMUP_SECONDS", "90")))
        while True:
            try:
                self._last_cycle_stats = await self.run_cycle()
            except Exception as exc:  # noqa: BLE001
                print(f"[trial] ciclo falhou: {exc!r}", flush=True)
            self._last_cycle_at = _utcnow()
            await publish_heartbeat("trial", self._hb_payload())
            await asyncio.sleep(3600)   # verifica a cada hora; age 1x/dia
