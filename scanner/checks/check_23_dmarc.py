"""Check 23 — DMARC ausente ou permissivo (Severidade: ALTA).

Passivo: TXT de `_dmarc.{domain}`. Ausente → FAIL. `p=none` (só monitora, não
bloqueia) → FAIL. `p=quarantine`/`p=reject` → PASS. Erro de DNS → INCONCLUSO.
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

    dmarc = next((r for r in records if r.lower().startswith("v=dmarc1")), None)
    if dmarc is None:
        return CheckResult(
            name=NAME, status=Status.FAIL, severity=Severity.ALTA,
            evidence=f"DMARC ausente em {domain} — sem política contra falsificação de e-mail.",
            details={"domain": domain})

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
