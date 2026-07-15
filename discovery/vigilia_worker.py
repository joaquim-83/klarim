"""Vigília Worker (KL-44 P2) — monitoramento silencioso contínuo.

Roda as 5 vigílias core (SSL, domínio, score, e-mail, reputação) para os sites
monitorados por contas Pro/Agency. A cada ciclo (padrão 6h) processa as vigílias
vencidas (`next_check_at <= now`), executa o check correspondente, e — só quando algo
importa — cria um `vigilia_alert` e envia o e-mail ao dono.

Convive com o resto do sistema (mesmo container `discovery`): heartbeat no Redis
(`worker:vigilia:status`), respeita o `worker_control` (pausa via MCP), enforcement de
plano por vigília (lazy trial expiry via `plans.get_subscription`), rate limit de RDAP
(1 req/s), teto por ciclo e timeout por check. Um erro em uma vigília **não** derruba as
outras. **Começa efetivamente pausado** (o seed grava a pausa no `worker_control`).
"""

from __future__ import annotations

import asyncio
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from notifier import KlarimMailer
from .heartbeat import publish_heartbeat
from .store import get_target_store
from . import worker_control

# Import tardio (evita puxar api.main no import do pacote discovery).
from api import vigilias as _vig
from api import plans as _plans


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class VigiliaWorker:
    def __init__(self) -> None:
        self.cycle_hours = int(os.environ.get("VIGILIA_CYCLE_HOURS", "6"))
        self.max_per_cycle = int(os.environ.get("VIGILIA_MAX_PER_CYCLE", "100"))
        self.check_timeout = int(os.environ.get("VIGILIA_CHECK_TIMEOUT", "30"))
        self.rdap_pause = float(os.environ.get("VIGILIA_RDAP_PAUSE", "1.0"))
        self.store = get_target_store()
        self._redis: Any = None
        self._last_cycle_at: Optional[datetime] = None
        self._next_cycle_at: Optional[datetime] = None
        self._last_cycle_stats: Dict[str, Any] = {}

    # ----- infra ----------------------------------------------------------- #

    def _mailer(self) -> Optional[KlarimMailer]:
        key = os.environ.get("RESEND_API_KEY")
        return KlarimMailer(key, os.environ.get("RESEND_FROM") or None) if key else None

    async def _get_redis(self) -> Any:
        if self._redis is None:
            try:
                import redis.asyncio as aioredis
                self._redis = aioredis.from_url(
                    os.environ.get("REDIS_URL", "redis://redis:6379/0"),
                    decode_responses=True)
            except Exception as exc:  # noqa: BLE001 - cache RDAP é best-effort
                print(f"[vigilia] redis indisponível: {exc!r}", flush=True)
        return self._redis

    def _hb_payload(self) -> Dict[str, Any]:
        return {
            "last_cycle_at": self._last_cycle_at.isoformat() if self._last_cycle_at else None,
            "next_cycle_at": self._next_cycle_at.isoformat() if self._next_cycle_at else None,
            "last_cycle_stats": self._last_cycle_stats,
        }

    async def _heartbeat_loop(self) -> None:
        while True:
            await publish_heartbeat("vigilia", self._hb_payload())
            await asyncio.sleep(60)

    # ----- enforcement de plano -------------------------------------------- #

    async def _plan_allows(self, user_id: int, tipo: str, cache: Dict[int, Any]) -> Optional[bool]:
        """True/False se o plano da conta permite a vigília `tipo`; None se o lookup
        falhou (transiente → o worker deve pular sem desabilitar)."""
        sub = cache.get(user_id, "MISS")
        if sub == "MISS":
            try:
                sub = await _plans.get_subscription(user_id)
            except Exception as exc:  # noqa: BLE001 - transiente: não desabilita nada
                print(f"[vigilia] plano indisponível user={user_id}: {exc!r}", flush=True)
                sub = None
            cache[user_id] = sub
        if not sub:
            return None
        plan = sub.get("plan") or {}
        return bool(plan.get(f"vigilia_{tipo}", False))

    # ----- ciclo ----------------------------------------------------------- #

    async def run_cycle(self) -> Dict[str, Any]:
        stats = {"due": 0, "checked": 0, "alerts": 0, "emailed": 0,
                 "skipped_plan": 0, "errors": 0}
        if not worker_control.is_enabled("vigilia"):
            print("[vigilia] worker pausado (worker_control); pulando ciclo", flush=True)
            stats["disabled"] = True
            return stats

        cfg = worker_control.worker_config("vigilia")
        cycle_hours = int(cfg.get("cycle_hours") or self.cycle_hours)
        max_per_cycle = int(cfg.get("max_per_cycle") or self.max_per_cycle)
        next_at = _utcnow() + timedelta(hours=cycle_hours)

        try:
            due = await self.store.get_due_vigilias(limit=max_per_cycle)
        except Exception as exc:  # noqa: BLE001
            print(f"[vigilia] get_due_vigilias falhou: {exc!r}", flush=True)
            return stats
        stats["due"] = len(due)
        mailer = self._mailer()
        redis = await self._get_redis()
        plan_cache: Dict[int, Any] = {}

        for vig in due:
            tipo = vig.get("tipo")
            try:
                allowed = await self._plan_allows(vig["user_id"], tipo, plan_cache)
                if allowed is None:
                    continue  # transiente — reprocessa no próximo ciclo (não reagenda)
                if not allowed:
                    # downgrade/trial expirado: desativa a vigília (para de vencer)
                    await self.store.disable_user_vigilias_except(
                        vig["user_id"], await self._allowed_types(vig["user_id"], plan_cache))
                    stats["skipped_plan"] += 1
                    continue
                result = await asyncio.wait_for(
                    _vig.run_vigilia_check(self.store, vig, redis=redis),
                    timeout=self.check_timeout)
                stats["checked"] += 1
                alerted = False
                if result.get("should_alert"):
                    await self._emit_alert(vig, result, mailer, stats)
                    alerted = True
                await self.store.update_vigilia_after_check(
                    vig["id"], result.get("status", "ok"), result.get("data") or {},
                    next_at, alerted=alerted)
            except asyncio.TimeoutError:
                stats["errors"] += 1
                print(f"[vigilia] timeout tipo={tipo} dom={vig.get('site_domain')}", flush=True)
                await self._safe_reschedule(vig["id"], next_at)
            except Exception as exc:  # noqa: BLE001 - uma vigília ruim não para o ciclo
                stats["errors"] += 1
                print(f"[vigilia] erro tipo={tipo} dom={vig.get('site_domain')}: {exc!r}",
                      flush=True)
                await self._safe_reschedule(vig["id"], next_at)
            if tipo == "domain":  # rate limit RDAP (1 req/s)
                await asyncio.sleep(self.rdap_pause)

        print(f"[vigilia] ciclo: {stats}", flush=True)
        return stats

    async def _allowed_types(self, user_id: int, cache: Dict[int, Any]) -> list:
        sub = cache.get(user_id)
        plan = (sub or {}).get("plan") or {}
        return [t for t in _vig.VIGILIA_TYPES if plan.get(f"vigilia_{t}")]

    async def _safe_reschedule(self, vigilia_id: int, next_at: datetime) -> None:
        try:
            await self.store.update_vigilia_after_check(vigilia_id, "error", {}, next_at,
                                                        alerted=False)
        except Exception as exc:  # noqa: BLE001
            print(f"[vigilia] reschedule falhou id={vigilia_id}: {exc!r}", flush=True)

    async def _emit_alert(self, vig: Dict[str, Any], result: Dict[str, Any],
                          mailer: Optional[KlarimMailer], stats: Dict[str, Any]) -> None:
        """Cria o `vigilia_alert` e envia o e-mail (best-effort — o alerta persiste
        mesmo se o e-mail falhar)."""
        alert_id = await self.store.create_vigilia_alert(
            vig["id"], vig["user_id"], vig["site_domain"], vig["tipo"],
            result.get("severity", "warning"), result.get("title", ""),
            result.get("message", ""), result.get("action_text"), result.get("data"))
        stats["alerts"] += 1
        to_email = vig.get("user_email")
        if not mailer or not to_email:
            return
        try:
            res = await mailer.send_vigilia_alert(
                to_email=to_email, tipo=vig["tipo"], domain=vig["site_domain"],
                subject=result.get("subject") or result.get("title") or "Alerta Klarim",
                title=result.get("title", ""), message=result.get("message", ""),
                action_text=result.get("action_text"),
                severity=result.get("severity", "warning"), data=result.get("data") or {})
            if res and not res.get("blocked"):
                await self.store.mark_vigilia_alert_sent(alert_id, res.get("email_id"))
                stats["emailed"] += 1
        except Exception as exc:  # noqa: BLE001 - e-mail é best-effort
            print(f"[vigilia] e-mail falhou alert={alert_id}: {exc!r}", flush=True)

    async def start(self) -> None:
        try:
            await self.store.ensure_schema()
        except Exception as exc:  # noqa: BLE001
            print(f"[vigilia] ensure_schema: {exc!r} (seguindo)", flush=True)
        print(f"[vigilia] iniciado (ciclo {self.cycle_hours}h, teto {self.max_per_cycle}/ciclo). "
              "Controlado por worker_control (começa pausado via seed).", flush=True)
        asyncio.create_task(self._heartbeat_loop())
        # Warmup: dá tempo do seed gravar a pausa antes do 1º ciclo.
        await asyncio.sleep(int(os.environ.get("VIGILIA_WARMUP_SECONDS", "120")))
        while True:
            try:
                self._last_cycle_stats = await self.run_cycle()
            except Exception as exc:  # noqa: BLE001
                print(f"[vigilia] ciclo falhou: {exc!r}", flush=True)
            self._last_cycle_at = _utcnow()
            self._next_cycle_at = self._last_cycle_at + timedelta(hours=self.cycle_hours)
            await publish_heartbeat("vigilia", self._hb_payload())
            await asyncio.sleep(self.cycle_hours * 3600)
