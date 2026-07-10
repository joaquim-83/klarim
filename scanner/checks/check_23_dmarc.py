"""Check 23 — DMARC ausente, duplicado ou permissivo (Severidade: ALTA).

Passivo: TXT de `_dmarc.{domain}`. Ausente → FAIL. **Múltiplos** registros DMARC →
FAIL (RFC 7489 §6.6.3: receptores ignoram todos, o DMARC fica sem efeito). `p=none`
(só monitora) → FAIL. `p=quarantine`/`p=reject` (registro único) → PASS. Erro de DNS
→ INCONCLUSO.
"""

from __future__ import annotations

import asyncio
import re

from .base import CheckResult, Status, Severity, domain_of, registrable_domain
from . import dns_util

ORDER = 23
CHECK_ID = "check_23_dmarc"
NAME = "DMARC (proteção contra phishing)"


async def check(url: str) -> CheckResult:
    domain = registrable_domain(domain_of(url))
    records = await asyncio.to_thread(dns_util.resolve_txt, f"_dmarc.{domain}", 5.0)
    if records is None:
        return CheckResult(name=NAME, status=Status.INCONCLUSO, severity=Severity.ALTA,
                           evidence=f"Não foi possível consultar o DNS de _dmarc.{domain}.")

    # Todos os registros DMARC (não só o primeiro que o DNS retornou).
    dmarc_records = [r.strip() for r in records if r.strip().lower().startswith("v=dmarc1")]

    if len(dmarc_records) > 1:
        # RFC 7489 §6.6.3: com múltiplos registros DMARC, receptores ignoram TODOS
        # — o DMARC fica sem efeito (e o resultado antes oscilava com a ordem do DNS).
        listing = "; ".join(dmarc_records[:3])
        return CheckResult(
            name=NAME, status=Status.FAIL, severity=Severity.ALTA,
            evidence=f"Múltiplos registros DMARC em {domain} ({len(dmarc_records)}). "
                     f"Pela RFC 7489, receptores ignoram todos — o DMARC está sem efeito. "
                     f"Registros: {listing}.",
            details={"domain": domain, "count": len(dmarc_records), "records": dmarc_records})

    if not dmarc_records:
        return CheckResult(
            name=NAME, status=Status.FAIL, severity=Severity.ALTA,
            evidence=f"DMARC ausente em {domain} — sem política contra falsificação de e-mail.",
            details={"domain": domain})

    dmarc = dmarc_records[0]
    m = re.search(r"p\s*=\s*(none|quarantine|reject)", dmarc, re.IGNORECASE)
    policy = (m.group(1).lower() if m else "none")
    if policy in ("quarantine", "reject"):
        return CheckResult(name=NAME, status=Status.PASS, severity=Severity.ALTA,
                           evidence=f"DMARC ativo em {domain} (p={policy}).",
                           details={"dmarc": dmarc, "policy": policy})

    return CheckResult(
        name=NAME, status=Status.FAIL, severity=Severity.ALTA,
        evidence=f"DMARC de {domain} com política permissiva (p=none) — não bloqueia "
                 f"e-mails falsificados, só monitora.",
        details={"dmarc": dmarc, "policy": policy})
