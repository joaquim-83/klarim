"""Check 28 — Domínio em vazamentos conhecidos (Have I Been Pwned) — Severidade: MÉDIA.

Passivo: consulta a API pública e gratuita do HIBP `GET /api/v3/breaches?domain=`
(lista os vazamentos **daquele** domínio; não exige chave). Vazamentos → FAIL.
Nenhum → PASS. API indisponível/rate-limit → INCONCLUSO.
"""

from __future__ import annotations

import json

import httpx

from .base import CheckResult, Status, Severity, domain_of, registrable_domain, USER_AGENT

ORDER = 28
CHECK_ID = "check_28_hibp"
NAME = "Vazamentos de dados (HIBP)"


async def _breaches(domain: str) -> httpx.Response:
    api = f"https://haveibeenpwned.com/api/v3/breaches?domain={domain}"
    async with httpx.AsyncClient(follow_redirects=True, timeout=10.0,
                                 headers={"User-Agent": USER_AGENT}) as client:
        return await client.get(api)


async def check(url: str) -> CheckResult:
    domain = registrable_domain(domain_of(url))
    try:
        resp = await _breaches(domain)
    except (httpx.HTTPError, OSError):
        return CheckResult(name=NAME, status=Status.INCONCLUSO, severity=Severity.MEDIA,
                           evidence="Não foi possível consultar o Have I Been Pwned.")

    if resp.status_code == 404:
        # HIBP responde 404 quando não há breaches para o domínio.
        return CheckResult(name=NAME, status=Status.PASS, severity=Severity.MEDIA,
                           evidence=f"{domain} não aparece em vazamentos conhecidos.")
    if resp.status_code != 200:
        return CheckResult(name=NAME, status=Status.INCONCLUSO, severity=Severity.MEDIA,
                           evidence=f"HIBP indisponível (status {resp.status_code}).")

    try:
        breaches = resp.json()
    except (json.JSONDecodeError, ValueError):
        return CheckResult(name=NAME, status=Status.INCONCLUSO, severity=Severity.MEDIA,
                           evidence="Resposta inesperada do HIBP.")

    if isinstance(breaches, list) and breaches:
        names = ", ".join(str(b.get("Name", "?")) for b in breaches[:3])
        return CheckResult(
            name=NAME, status=Status.FAIL, severity=Severity.MEDIA,
            evidence=f"{domain} aparece em {len(breaches)} vazamento(s) conhecido(s): {names}.",
            details={"count": len(breaches)})

    return CheckResult(name=NAME, status=Status.PASS, severity=Severity.MEDIA,
                       evidence=f"{domain} não aparece em vazamentos conhecidos.")
