"""Check 37 — DNSSEC (Severidade: MÉDIA, KL-36).

DNSSEC assina criptograficamente as respostas DNS, impedindo spoofing/cache
poisoning. Verificação passiva: presença de registro **DS** (Delegation Signer) no
parent zone indica DNSSEC ativo. Sem DNSSEC, um atacante pode redirecionar os
visitantes do site para uma cópia falsa.
"""

from __future__ import annotations

import asyncio

from .base import CheckResult, Status, Severity, domain_of, registrable_domain
from . import dns_util

ORDER = 37
CHECK_ID = "check_37_dnssec"
NAME = "DNSSEC"


async def check(url: str) -> CheckResult:
    domain = registrable_domain(domain_of(url))
    ds = await asyncio.to_thread(dns_util.resolve_ds, domain, 5.0)
    if ds is None:
        return CheckResult(name=NAME, status=Status.INCONCLUSO, severity=Severity.MEDIA,
                           evidence=f"Não foi possível consultar o DNS de {domain}.")
    if ds:
        return CheckResult(
            name=NAME, status=Status.PASS, severity=Severity.MEDIA,
            evidence=f"DNSSEC configurado em {domain} — respostas DNS autenticadas "
                     f"({len(ds)} registro(s) DS).",
            details={"domain": domain, "ds_count": len(ds)})
    return CheckResult(
        name=NAME, status=Status.FAIL, severity=Severity.MEDIA,
        evidence=f"Domínio {domain} sem DNSSEC (registro DS ausente) — respostas DNS "
                 f"podem ser adulteradas (cache poisoning).",
        details={"domain": domain})
