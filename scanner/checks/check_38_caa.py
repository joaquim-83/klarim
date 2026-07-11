"""Check 38 — CAA / Certificate Authority Authorization (Severidade: MÉDIA, KL-36).

O registro CAA (RFC 8659) define **quais CAs** podem emitir certificados para o
domínio. Sem CAA, qualquer CA do mundo pode emitir um certificado — facilita ataques
MitM com certificados fraudulentos. Verificação passiva: consulta DNS CAA.
"""

from __future__ import annotations

import asyncio

from .base import CheckResult, Status, Severity, domain_of, registrable_domain
from . import dns_util

ORDER = 38
CHECK_ID = "check_38_caa"
NAME = "CAA (Certificate Authority Authorization)"


async def check(url: str) -> CheckResult:
    domain = registrable_domain(domain_of(url))
    records = await asyncio.to_thread(dns_util.resolve_caa, domain, 5.0)
    if records is None:
        return CheckResult(name=NAME, status=Status.INCONCLUSO, severity=Severity.MEDIA,
                           evidence=f"Não foi possível consultar o DNS de {domain}.")

    issuers = [r["value"] for r in records if r.get("tag") in ("issue", "issuewild")]
    has_iodef = any(r.get("tag") == "iodef" for r in records)
    if issuers:
        cas = ", ".join(sorted({v.split(";")[0].strip() for v in issuers if v}))
        iodef_note = " Com iodef (reporte de emissão)." if has_iodef else ""
        return CheckResult(
            name=NAME, status=Status.PASS, severity=Severity.MEDIA,
            evidence=f"CAA configurado em {domain} — apenas CA(s) autorizada(s) podem "
                     f"emitir certificados: {cas}.{iodef_note}",
            details={"domain": domain, "records": records})
    return CheckResult(
        name=NAME, status=Status.FAIL, severity=Severity.MEDIA,
        evidence=f"CAA ausente em {domain} — qualquer autoridade certificadora pode "
                 f"emitir um certificado para o domínio.",
        details={"domain": domain, "records": records})
