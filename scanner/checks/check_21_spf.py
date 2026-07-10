"""Check 21 — SPF ausente ou fraco (Severidade: ALTA).

Passivo: consulta TXT do domínio registrável. Sem `v=spf1` → FAIL (qualquer
servidor pode falsificar e-mail do domínio). Com `+all` (libera todo IP) → FAIL.
Com `-all`/`~all` → PASS. Erro de DNS → INCONCLUSO.
"""

from __future__ import annotations

import asyncio

from .base import CheckResult, Status, Severity, domain_of, registrable_domain
from . import dns_util

ORDER = 21
CHECK_ID = "check_21_spf"
NAME = "SPF (proteção de e-mail)"


async def check(url: str) -> CheckResult:
    domain = registrable_domain(domain_of(url))
    records = await asyncio.to_thread(dns_util.resolve_txt, domain, 5.0)
    if records is None:
        return CheckResult(name=NAME, status=Status.INCONCLUSO, severity=Severity.ALTA,
                           evidence=f"Não foi possível consultar o DNS de {domain}.")

    spf = next((r for r in records if r.lower().startswith("v=spf1")), None)
    if spf is None:
        return CheckResult(
            name=NAME, status=Status.FAIL, severity=Severity.ALTA,
            evidence=f"Registro SPF ausente em {domain} — qualquer servidor pode enviar "
                     f"e-mails se passando pelo domínio.",
            details={"domain": domain})

    low = spf.lower()
    if "+all" in low:
        return CheckResult(
            name=NAME, status=Status.FAIL, severity=Severity.ALTA,
            evidence=f"SPF de {domain} usa '+all' — permite qualquer servidor enviar "
                     f"e-mail pelo domínio (equivale a não ter SPF).",
            details={"spf": spf})

    if "-all" in low or "~all" in low:
        return CheckResult(name=NAME, status=Status.PASS, severity=Severity.ALTA,
                           evidence=f"SPF presente e restritivo em {domain}.",
                           details={"spf": spf})

    # Tem SPF mas sem 'all' explícito (política indefinida) — trata como fraco.
    return CheckResult(
        name=NAME, status=Status.FAIL, severity=Severity.ALTA,
        evidence=f"SPF de {domain} sem política restritiva (-all/~all) — não bloqueia falsificação.",
        details={"spf": spf})
