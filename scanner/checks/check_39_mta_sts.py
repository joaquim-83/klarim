"""Check 39 — MTA-STS (Severidade: BAIXA, KL-36).

MTA-STS (RFC 8461) força TLS nas conexões SMTP de entrada, impedindo downgrade
(STARTTLS stripping) que deixa e-mails legíveis em trânsito. Verificação passiva em
2 etapas: (1) TXT em ``_mta-sts.<domínio>``; (2) se presente, um GET público na
policy ``https://mta-sts.<domínio>/.well-known/mta-sts.txt`` (URL definida pela RFC).
"""

from __future__ import annotations

import asyncio
import re

import httpx

from .base import CheckResult, Status, Severity, domain_of, registrable_domain, fetch
from . import dns_util

ORDER = 39
CHECK_ID = "check_39_mta_sts"
NAME = "MTA-STS"

_MODE_RE = re.compile(r"mode\s*:\s*(enforce|testing|none)", re.IGNORECASE)


async def check(url: str) -> CheckResult:
    domain = registrable_domain(domain_of(url))
    txt = await asyncio.to_thread(dns_util.resolve_txt, f"_mta-sts.{domain}", 5.0)
    if txt is None:
        return CheckResult(name=NAME, status=Status.INCONCLUSO, severity=Severity.BAIXA,
                           evidence=f"Não foi possível consultar o DNS de {domain}.")

    declared = any(r.lower().startswith("v=stsv1") for r in txt)
    if not declared:
        return CheckResult(
            name=NAME, status=Status.FAIL, severity=Severity.BAIXA,
            evidence=f"MTA-STS não configurado em {domain} — e-mails podem ser "
                     f"interceptados em trânsito (downgrade de TLS).",
            details={"domain": domain})

    # DNS declara MTA-STS: buscar a policy pública.
    try:
        resp = await fetch(f"https://mta-sts.{domain}/.well-known/mta-sts.txt",
                           method="GET", follow_redirects=True)
        policy = resp.text if resp.status_code == 200 else None
    except (httpx.HTTPError, OSError):
        policy = None

    if not policy:
        return CheckResult(
            name=NAME, status=Status.FAIL, severity=Severity.BAIXA,
            evidence=f"MTA-STS declarado no DNS de {domain} mas a policy não está "
                     f"acessível (/.well-known/mta-sts.txt).",
            details={"domain": domain, "dns": True})

    m = _MODE_RE.search(policy)
    mode = m.group(1).lower() if m else None
    details = {"domain": domain, "dns": True, "mode": mode}
    if mode == "enforce":
        return CheckResult(name=NAME, status=Status.PASS, severity=Severity.BAIXA,
                           evidence=f"MTA-STS ativo em modo enforce em {domain} — TLS "
                                    f"obrigatório para e-mails recebidos.",
                           details=details)
    if mode == "testing":
        return CheckResult(name=NAME, status=Status.PASS, severity=Severity.BAIXA,
                           evidence=f"MTA-STS presente em {domain} porém em modo testing "
                                    f"— TLS recomendado mas ainda não obrigatório.",
                           details=details)
    return CheckResult(
        name=NAME, status=Status.FAIL, severity=Severity.BAIXA,
        evidence=f"MTA-STS declarado em {domain} mas a policy não aplica TLS "
                 f"(mode: {mode or 'ausente'}).",
        details=details)
