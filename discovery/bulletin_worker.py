"""Bulletin Worker (KL-44 P3) — boletim de segurança recorrente.

Filosofia Guardião Digital: "email push > dashboard pull". Envia o boletim por
frequência do plano (free→mensal, pro→semanal, agency→diário úteis), com score +
tendência + vigílias + ação prioritária + laudo compartilhável. Se há técnico
vinculado (KL-44 P3), manda também o laudo técnico. Mesmo container `discovery`
(asyncio.gather), heartbeat no Redis, respeita o `worker_control` (pausa via MCP).
"""

from __future__ import annotations

import asyncio
import json
import os
import secrets
import string
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from urllib.parse import quote

from notifier import bulletin as _bl
from notifier.email_client import KlarimMailer
from reporter.laudo import enrich_fails

from .store import get_target_store
from .heartbeat import publish_heartbeat
from . import worker_control

SITE_BASE = os.environ.get("SITE_BASE", "https://klarim.net")
_ALPHABET = string.ascii_uppercase + string.digits


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _gen_code(n: int = 8) -> str:
    return "".join(secrets.choice(_ALPHABET) for _ in range(n))


def _mask_email(email: str) -> str:
    email = (email or "").strip()
    if "@" not in email:
        return email
    local, _, domain = email.partition("@")
    head = local[0] if local else ""
    tail = local[-1] if len(local) > 1 else ""
    return f"{head}***{tail}@{domain}"


def _semaphore(score: Optional[int]) -> str:
    s = score or 0
    return "verde" if s >= 90 else ("amarelo" if s >= 50 else "vermelho")


def _whatsapp_url(domain: str, score: Any, code: str) -> str:
    msg = (f"Oi, nosso site está com score {score} de segurança. Pode dar uma olhada?\n\n"
           f"Relatório completo: {SITE_BASE}/laudo/{code}")
    return f"https://wa.me/?text={quote(msg)}"


class BulletinWorker:
    def __init__(self) -> None:
        self.hour_utc = int(os.environ.get("BULLETIN_HOUR_UTC", "13"))   # 10h BRT
        self.batch_size = int(os.environ.get("BULLETIN_BATCH_SIZE", "50"))
        self.weekday = int(os.environ.get("BULLETIN_WEEKDAY", "0"))       # segunda
        self.monthday = int(os.environ.get("BULLETIN_MONTHDAY", "1"))     # dia 1
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
            await publish_heartbeat("bulletin", self._hb_payload())
            await asyncio.sleep(60)

    def _frequencies_due(self, now: datetime) -> List[str]:
        if now.hour != self.hour_utc:
            return []
        due = []
        if now.weekday() < 5:              # dias úteis (Agency)
            due.append("daily")
        if now.weekday() == self.weekday:  # segunda (Pro)
            due.append("weekly")
        if now.day == self.monthday:       # dia 1 (Free)
            due.append("monthly")
        return due

    async def _enabled(self) -> bool:
        if not worker_control.is_enabled("bulletin"):
            return False
        try:
            v = await self.store.get_setting("BULLETIN_ENABLED", "true")
            return str(v).strip().lower() not in ("false", "0", "no")
        except Exception:  # noqa: BLE001 - fail-open
            return True

    async def run_cycle(self) -> Dict[str, Any]:
        stats = {"due_freqs": [], "candidates": 0, "sent": 0, "tech_sent": 0,
                 "skipped": 0, "errors": 0}
        if not await self._enabled():
            stats["disabled"] = True
            return stats
        # KL-44 P4: config ao vivo (admin_settings > .env) — relê a hora por ciclo.
        try:
            self.hour_utc = int(await self.store.get_setting("BULLETIN_HOUR_UTC", self.hour_utc))
        except Exception:  # noqa: BLE001 - mantém a atual
            pass
        now = _utcnow()
        freqs = self._frequencies_due(now)
        stats["due_freqs"] = freqs
        if not freqs:
            return stats
        mailer = self._mailer()
        for freq in freqs:
            try:
                rows = await self.store.list_users_due_bulletin(freq)
            except Exception as exc:  # noqa: BLE001
                print(f"[bulletin] list_users_due_bulletin({freq}) falhou: {exc!r}", flush=True)
                continue
            stats["candidates"] += len(rows)
            for row in rows[:self.batch_size]:
                try:
                    if await self._send_one(row, freq, mailer, now):
                        stats["sent"] += 1
                    else:
                        stats["skipped"] += 1
                except Exception as exc:  # noqa: BLE001 - um boletim ruim não derruba o ciclo
                    stats["errors"] += 1
                    print(f"[bulletin] falha user={row.get('user_id')} "
                          f"target={row.get('target_id')}: {exc!r}", flush=True)
        return stats

    async def _send_one(self, row: Dict[str, Any], freq: str,
                        mailer: Optional[KlarimMailer], now: datetime) -> bool:
        uid, tid = row["user_id"], row["target_id"]
        email = row["email"]
        domain = row.get("domain") or ""
        scan = await self.store.get_latest_scan_full(tid)
        if not scan or scan.get("score") is None:
            return False
        score = scan["score"]
        semaphore = scan.get("semaphore") or _semaphore(score)
        raw = scan.get("checks_json") or []
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except Exception:  # noqa: BLE001
                raw = []
        # checks_json pode ser o dict completo do report ({results, score, privacy}) ou a
        # lista de checks (formato antigo). KL-44 P5: extrai a lista + o bloco de privacidade.
        privacy = None
        if isinstance(raw, dict):
            privacy = raw.get("privacy")
            checks = raw.get("results") or raw.get("checks") or []
        else:
            checks = raw

        last = await self.store.get_last_bulletin(uid, tid)
        prev = last.get("score") if last else None
        delta = (score - prev) if prev is not None else 0
        trend = "up" if delta >= 2 else ("down" if delta <= -2 else "stable")

        vig_raw = await self.store.get_user_target_vigilias(uid, domain)
        vig = {t: ("error" if s in ("error", "critical") else ("warning" if s == "warning" else "ok"))
               for t, s in vig_raw.items()}
        vig_alerts = [f"{_bl._VIGILIA_LABEL.get(t, t)}: requer atenção"
                      for t, s in vig.items() if s in ("warning", "error")]

        fails = enrich_fails(checks)
        top = fails[0] if fails else None
        top_action = None
        if top:
            top_action = {"name": top.get("name"), "evidence": top.get("evidence") or "",
                          "fix": top.get("fix") or "—", "technical": top.get("fix_code") or ""}

        tech_link = await self.store.get_active_technician_for_target(uid, tid)
        code = _gen_code(8)
        try:
            await self.store.create_shared_report(
                tid, uid, code, scan_id=scan["id"],
                technician_link_id=(tech_link["id"] if tech_link else None))
        except Exception as exc:  # noqa: BLE001 - sem laudo o boletim ainda vai (só sem link)
            print(f"[bulletin] shared_report falhou: {exc!r}", flush=True)

        # KL-44 P5 / KL-20: benchmark do setor (anônimo) + risco setorizado — best-effort.
        benchmark, risk_line = None, None
        try:
            sector = (row.get("sector")
                      or (await self.store.get_target(tid) or {}).get("sector") or "").strip().lower()
            if sector and sector != "outro":
                benchmark = await self.store.sector_benchmark(sector, min_count=10)
            if top:  # KL-20: consequência de negócio da ação prioritária (linguagem do dono)
                from reporter.risk_messages import build_risk_summary
                rs = build_risk_summary(checks, sector, limit=1)
                risk_line = rs["risks"][0]["message"] if rs["risks"] else None
        except Exception as exc:  # noqa: BLE001
            print(f"[bulletin] benchmark/risco falhou: {exc!r}", flush=True)

        owner_text = _bl.build_owner_bulletin({
            "domain": domain, "score": score, "semaphore": semaphore, "trend": trend,
            "delta": delta, "vigilias": vig, "vigilia_alerts": vig_alerts,
            "top_action": top_action, "code": code, "risk_line": risk_line,   # KL-20
            "whatsapp_url": _whatsapp_url(domain, score, code),
            "technician_masked": _mask_email(tech_link["technician_email"]) if tech_link else None,
            "benchmark": benchmark, "privacy": privacy,   # KL-44 P5
        })
        subject = _bl.owner_subject(domain, _bl.bulletin_period_label(now.month, now.year))

        tech_notified = False
        if mailer:
            try:
                await mailer.send_bulletin_owner(email, domain, subject, owner_text, target_id=tid)
            except Exception as exc:  # noqa: BLE001
                print(f"[bulletin] e-mail dono falhou {email}: {exc!r}", flush=True)
            if tech_link and tech_link.get("status") == "active" and tech_link.get("technician_email"):
                tech_text = _bl.build_technician_bulletin({
                    "domain": domain, "score": score, "semaphore": semaphore, "trend": trend,
                    "delta": delta, "fails": fails, "pass_count": len(checks) - len(fails),
                    "owner_masked": _mask_email(email), "code": code})
                try:
                    await mailer.send_bulletin_technician(
                        tech_link["technician_email"], domain,
                        _bl.technician_subject(domain, score), tech_text, target_id=tid)
                    tech_notified = True
                except Exception as exc:  # noqa: BLE001
                    print(f"[bulletin] e-mail técnico falhou: {exc!r}", flush=True)

        await self.store.create_bulletin(
            user_id=uid, target_id=tid, scan_id=scan["id"], bulletin_type=freq, score=score,
            previous_score=prev, score_trend=trend, vigilias_summary=vig,
            top_action=(top.get("name") if top else None), shared_report_code=code,
            technician_notified=tech_notified)
        if tech_notified:
            # heurística de stat rápida (não crítica)
            self._last_cycle_stats["tech_sent"] = self._last_cycle_stats.get("tech_sent", 0) + 1
        return True

    async def start(self) -> None:
        try:
            await self.store.ensure_schema()
        except Exception as exc:  # noqa: BLE001
            print(f"[bulletin] ensure_schema: {exc!r} (seguindo)", flush=True)
        print(f"[bulletin] iniciado (envia às {self.hour_utc}h UTC; free=mensal, "
              "pro=semanal, agency=diário úteis). Controlado por worker_control.", flush=True)
        asyncio.create_task(self._heartbeat_loop())
        await asyncio.sleep(int(os.environ.get("BULLETIN_WARMUP_SECONDS", "60")))
        while True:
            try:
                self._last_cycle_stats = await self.run_cycle()
            except Exception as exc:  # noqa: BLE001
                print(f"[bulletin] ciclo falhou: {exc!r}", flush=True)
            self._last_cycle_at = _utcnow()
            await publish_heartbeat("bulletin", self._hb_payload())
            await asyncio.sleep(3600)   # verifica a cada hora
