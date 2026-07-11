"""Check 40 — BIMI (Severidade: BAIXA, KL-36).

BIMI (Brand Indicators for Message Identification) permite exibir o logo da marca
nos clientes de e-mail (Gmail, Apple Mail). Requer DMARC em ``p=quarantine`` ou
``p=reject``. É mais um **indicador de maturidade** de segurança de e-mail do que uma
falha grave — daí a severidade BAIXA. Verificação passiva: consulta DNS TXT.
"""

from __future__ import annotations

import asyncio
import re

from .base import CheckResult, Status, Severity, domain_of, registrable_domain
from . import dns_util

ORDER = 40
CHECK_ID = "check_40_bimi"
NAME = "BIMI"

_LOGO_RE = re.compile(r"\bl\s*=\s*([^;]+)", re.IGNORECASE)


async def check(url: str) -> CheckResult:
    domain = registrable_domain(domain_of(url))
    txt = await asyncio.to_thread(dns_util.resolve_txt, f"default._bimi.{domain}", 5.0)
    if txt is None:
        return CheckResult(name=NAME, status=Status.INCONCLUSO, severity=Severity.BAIXA,
                           evidence=f"Não foi possível consultar o DNS de {domain}.")

    record = next((r for r in txt if "v=bimi1" in r.lower()), None)
    if record is None:
        return CheckResult(
            name=NAME, status=Status.FAIL, severity=Severity.BAIXA,
            evidence=f"BIMI não configurado em {domain} — oportunidade de branding "
                     f"perdida (o logo da marca não aparece nos e-mails enviados).",
            details={"domain": domain})

    m = _LOGO_RE.search(record)
    logo = m.group(1).strip() if m else None

    # Pré-requisito: DMARC precisa estar em enforce (quarantine/reject) para o BIMI valer.
    dmarc = await asyncio.to_thread(dns_util.resolve_txt, f"_dmarc.{domain}", 5.0)
    dmarc_rec = next((r for r in (dmarc or []) if r.lower().startswith("v=dmarc1")), "")
    enforce = bool(re.search(r"p\s*=\s*(quarantine|reject)", dmarc_rec, re.IGNORECASE))
    note = "" if enforce else (" Atenção: o BIMI exige DMARC com policy enforce "
                               "(p=quarantine ou p=reject) para funcionar.")
    return CheckResult(
        name=NAME, status=Status.PASS, severity=Severity.BAIXA,
        evidence=f"BIMI configurado em {domain}"
                 f"{f' (logo: {logo})' if logo else ''}.{note}",
        details={"domain": domain, "record": record, "logo": logo, "dmarc_enforce": enforce})
