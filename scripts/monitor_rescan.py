"""Monitoramento mensal das contas de usuário (KL-51 f3).

Todo site vinculado a uma conta ATIVA cujo último scan tem mais de `MONITOR_AGE_DAYS`
(padrão 30) é reescaneado (scan COMPLETO, 48 checks) e o dono recebe um e-mail de
evolução (score anterior → atual). É **independente** do rescan worker antigo (que
enviava alertas do funil, hoje pausado) — aqui não se mexe em alert_log/rescan_log.

Design:
- Roda via cron diário (idempotente: só pega sites com >30 dias). Processa até
  `--limit` sites por execução, com pausa entre scans (rate limit educado).
- Deduplica o scan por site (um site com vários donos é escaneado 1×, e-mail a cada dono).
- Best-effort: erro num site é logado e não aborta o batch. E-mail exige Resend
  configurado (senão só reescaneia e atualiza o dashboard).

Uso: `docker compose exec -T api python scripts/monitor_rescan.py [--limit 100]
[--age-days 30] [--dry-run] [--no-email]`.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from typing import Any, Dict, List, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scanner import run_scan  # noqa: E402
from discovery.store import get_target_store  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger("monitor_rescan")

SITE_BASE = os.environ.get("SITE_BASE", "https://klarim.net")
MONITOR_AGE_DAYS = int(os.environ.get("MONITOR_AGE_DAYS", "30"))
_SCAN_PAUSE = float(os.environ.get("MONITOR_SCAN_PAUSE", "5"))  # segundos entre scans


def _mailer():
    """Mailer se o Resend estiver configurado, senão None (só reescaneia)."""
    key = os.environ.get("RESEND_API_KEY")
    if not key:
        return None
    from notifier import KlarimMailer
    return KlarimMailer(key, os.environ.get("RESEND_FROM") or None)


def _dashboard_link(target_id: int) -> str:
    return f"{SITE_BASE}/dashboard/site/{target_id}"


async def _rescan_one(store, target_id: int, url: str) -> Optional[Dict[str, Any]]:
    """Reescaneia um site (completo), salva e atualiza o alvo. Retorna score/fails
    antigos e novos, ou ``None`` em falha."""
    prev = await store.latest_scan_meta(target_id)  # ANTES de salvar o novo
    prev_score = prev["score"] if prev else None
    prev_fail = prev["fail_count"] if prev else None

    report = await run_scan(url, full=True)
    s = report.score
    if s is None:
        return None
    scan_id = await store.save_scan(
        target_id, url, s.score, s.semaphore, s.passed, s.failed, s.inconclusive,
        report.to_dict(), source="rescan")
    await store.update_scan_result(target_id, scan_id, s.score)
    return {"prev_score": prev_score, "prev_fail": prev_fail,
            "new_score": s.score, "new_fail": s.failed,
            "semaphore": s.semaphore}


async def run(limit: int, age_days: int, dry_run: bool, send_email: bool) -> None:
    store = get_target_store()
    rows: List[Dict[str, Any]] = await store.get_user_sites_for_monitoring(age_days)
    if not rows:
        log.info("Nenhum site elegível para re-scan (todos com scan < %dd).", age_days)
        return

    # Agrupa por site: escaneia 1×, notifica cada dono.
    by_target: Dict[int, Dict[str, Any]] = {}
    for r in rows:
        tid = r["target_id"]
        by_target.setdefault(tid, {"url": r["url"], "domain": r["domain"], "recipients": []})
        by_target[tid]["recipients"].append(r["user_email"])

    targets = list(by_target.items())[:limit]
    log.info("%d site(s) elegíveis; processando %d (age>%dd, dry_run=%s, email=%s).",
             len(by_target), len(targets), age_days, dry_run, send_email)

    mailer = _mailer() if (send_email and not dry_run) else None
    stats = {"rescanned": 0, "emails": 0, "errors": 0}

    for i, (tid, info) in enumerate(targets, 1):
        url, domain = info["url"], info["domain"]
        if dry_run:
            log.info("[dry-run] %d/%d %s → %d dono(s)", i, len(targets), domain,
                     len(info["recipients"]))
            continue
        try:
            res = await _rescan_one(store, tid, url)
            if res is None:
                log.warning("%d/%d %s: scan sem score — pulado", i, len(targets), domain)
                stats["errors"] += 1
                continue
            stats["rescanned"] += 1
            prev = res["prev_score"] if res["prev_score"] is not None else res["new_score"]
            fixed = max(0, (res["prev_fail"] or res["new_fail"]) - res["new_fail"])
            log.info("%d/%d %s: %s → %s (fails %s)", i, len(targets), domain,
                     prev, res["new_score"], res["new_fail"])
            if mailer is not None:
                for email in info["recipients"]:
                    try:
                        await mailer.send_account_evolution(
                            email, domain, prev, res["new_score"], fixed,
                            res["new_fail"], _dashboard_link(tid))
                        stats["emails"] += 1
                    except Exception as exc:  # noqa: BLE001
                        log.warning("e-mail evolução falhou %s: %r", email, exc)
        except Exception as exc:  # noqa: BLE001
            log.warning("re-scan falhou %s: %r", url, exc)
            stats["errors"] += 1
        await asyncio.sleep(_SCAN_PAUSE)

    log.info("--- Resumo: reescaneados=%d e-mails=%d erros=%d",
             stats["rescanned"], stats["emails"], stats["errors"])


def main() -> None:
    ap = argparse.ArgumentParser(description="Monitoramento mensal das contas (KL-51 f3).")
    ap.add_argument("--limit", type=int, default=100, help="Máx. de sites por execução.")
    ap.add_argument("--age-days", type=int, default=MONITOR_AGE_DAYS,
                    help="Idade mínima do último scan (dias).")
    ap.add_argument("--dry-run", action="store_true", help="Não escaneia nem envia.")
    ap.add_argument("--no-email", action="store_true", help="Reescaneia mas não envia e-mail.")
    args = ap.parse_args()
    asyncio.run(run(args.limit, args.age_days, args.dry_run, not args.no_email))


if __name__ == "__main__":
    main()
