"""Ingestão de scans no banco (KL-17) — registra o alvo + salva o scan com origem.

Ponte entre um `ScanReport` (do scanner) e as tabelas `targets`/`scans`. Usado por:
- scans **públicos** (klarim.net) — em background, `source='public'`;
- o fluxo **admin** (painel: escanear + enviar) — síncrono, `source='admin'`.

Faz o mesmo enriquecimento do Discovery Worker (fingerprint + setor + e-mail), a
partir de um GET do HTML servido. Idempotente por URL: `register_target` faz
UPSERT (não duplica alvo) e `update_scan_result` atualiza `last_scan_*`.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

import httpx

from scanner.checks.base import fetch, registrable_domain, domain_of
from .fingerprint import detect_platform
from .contact import extract_email
from .classifier import classify_sector


async def _fetch_html(url: str) -> Optional[str]:
    try:
        resp = await fetch(url, method="GET", follow_redirects=True)
    except (httpx.HTTPError, OSError):
        return None
    return resp.text if resp.status_code < 400 else None


async def ingest_scan(store, url: str, report, source: str,
                      scanned_by_email: Optional[str] = None) -> Dict[str, Any]:
    """Registra/atualiza o alvo e salva o scan (com origem). Retorna metadados.

    ``scanned_by_email`` (KL-25): e-mail do visitante que pediu o scan público —
    liga o scan ao lead. Retorna: target_id, scan_id, platform, sector, contact_email.
    """
    domain = registrable_domain(domain_of(url))
    platform, email = "unknown", None
    html = await _fetch_html(url)
    # Classifica em cascata (domínio + HTML): funciona mesmo se o fetch falhar.
    sector, tier, confidence = classify_sector(html, url)
    if html:
        platform = detect_platform(url, html)
        email = await extract_email(html, url)

    target_id = await store.register_target(
        url, domain, platform, sector, tier, email, source=source, status="scanned",
        confidence=confidence)

    scan_id = None
    s = report.score
    status = getattr(report, "status", "ok")
    if s is not None:
        scan_id = await store.save_scan(
            target_id, url, s.score, s.semaphore, s.passed, s.failed,
            s.inconclusive, report.to_dict(), source=source,
            scanned_by_email=scanned_by_email)
        await store.update_scan_result(target_id, scan_id, s.score)
    elif status == "unreachable":
        # KL-94/KL-57: registra a indisponibilidade (score NULL) para analytics de disponibilidade.
        # Não atualiza `update_scan_result` (não sobrescreve o último score válido com NULL).
        scan_id = await store.save_scan(
            target_id, url, None, None, 0, 0, 0, report.to_dict(), source=source,
            scanned_by_email=scanned_by_email, status="unreachable")

    return {
        "target_id": target_id, "scan_id": scan_id,
        "platform": platform, "sector": sector, "contact_email": email,
        "classification_confidence": confidence,
    }
