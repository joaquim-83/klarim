"""Check 26 — Subdomínios expostos via CT logs (Severidade: MÉDIA).

Passivo: consulta a API pública do **crt.sh** (Certificate Transparency) e conta
os subdomínios do domínio. Muitos subdomínios **e** algum com nome sensível
(admin/staging/dev/api/…) amplia a superfície de ataque → FAIL. crt.sh lento/
offline → INCONCLUSO.
"""

from __future__ import annotations

import json

import httpx

from .base import (CheckResult, Status, Severity, domain_of, registrable_domain,
                   USER_AGENT)

ORDER = 26
CHECK_ID = "check_26_subdomains"
NAME = "Subdomínios expostos (CT logs)"

_SENSITIVE = ("admin", "staging", "dev", "test", "api", "internal", "vpn",
              "mail", "db", "backend", "homolog", "hml", "qa")
_THRESHOLD = 20


async def _crtsh(domain: str) -> list:
    url = f"https://crt.sh/?q=%.{domain}&output=json"
    async with httpx.AsyncClient(follow_redirects=True, timeout=10.0,
                                 headers={"User-Agent": USER_AGENT}) as client:
        resp = await client.get(url)
    if resp.status_code != 200:
        raise httpx.HTTPError(f"crt.sh status {resp.status_code}")
    return resp.json() if resp.text.strip() else []


async def check(url: str) -> CheckResult:
    domain = registrable_domain(domain_of(url))
    try:
        rows = await _crtsh(domain)
    except (httpx.HTTPError, OSError, json.JSONDecodeError, ValueError):
        return CheckResult(name=NAME, status=Status.INCONCLUSO, severity=Severity.MEDIA,
                           evidence=f"Não foi possível consultar o crt.sh para {domain}.")

    subs: set[str] = set()
    for row in rows:
        for name in str(row.get("name_value", "")).split("\n"):
            host = name.strip().lstrip("*.").lower()
            if host.endswith(domain) and host != domain:
                subs.add(host)

    sensitive = sorted(h for h in subs if any(s in h.split(".")[0] for s in _SENSITIVE))

    if len(subs) > _THRESHOLD and sensitive:
        listing = ", ".join(sensitive[:3])
        return CheckResult(
            name=NAME, status=Status.FAIL, severity=Severity.MEDIA,
            evidence=f"{len(subs)} subdomínios públicos; sensíveis expostos: {listing}"
                     f"{' …' if len(sensitive) > 3 else ''}.",
            details={"count": len(subs), "sensitive": sensitive[:20]})

    return CheckResult(
        name=NAME, status=Status.PASS, severity=Severity.MEDIA,
        evidence=f"{len(subs)} subdomínio(s) público(s) — superfície de ataque controlada.",
        details={"count": len(subs)})
