"""Check 19 — Redirect para domínio diferente (Severidade: MÉDIA).

Passivo: um GET sem seguir redirects. Se a raiz redireciona (3xx) para um domínio
**registrável diferente** (ex.: softwall.com.br → softwall.tech), o domínio
original pode ser abandonado e sequestrado. Redirect para o mesmo domínio
(www → apex, http → https) = PASS.
"""

from __future__ import annotations

from urllib.parse import urljoin, urlparse

import httpx

from .base import (CheckResult, Status, Severity, fetch, with_scheme,
                   domain_of, registrable_domain)

ORDER = 19
CHECK_ID = "check_19_redirect_domain"
NAME = "Redirect para domínio diferente"


async def check(url: str) -> CheckResult:
    https_url = with_scheme(url, "https")
    try:
        resp = await fetch(https_url, method="GET", follow_redirects=False)
    except (httpx.HTTPError, OSError) as exc:
        return CheckResult(name=NAME, status=Status.INCONCLUSO, severity=Severity.MEDIA,
                           evidence=f"Falha ao obter resposta: {exc!r}")

    if resp.status_code not in (301, 302, 303, 307, 308):
        return CheckResult(name=NAME, status=Status.PASS, severity=Severity.MEDIA,
                           evidence="O site não redireciona para outro domínio.")

    location = resp.headers.get("location", "")
    if not location:
        return CheckResult(name=NAME, status=Status.PASS, severity=Severity.MEDIA,
                           evidence="Redirect sem destino explícito.")

    dest = urljoin(https_url, location)
    dest_host = (urlparse(dest).hostname or "").lower()
    if not dest_host:
        return CheckResult(name=NAME, status=Status.PASS, severity=Severity.MEDIA,
                           evidence="Redirect relativo (mesmo domínio).")

    origin_reg = registrable_domain(domain_of(https_url))
    dest_reg = registrable_domain(dest_host)
    if dest_reg and origin_reg and dest_reg != origin_reg:
        return CheckResult(
            name=NAME, status=Status.FAIL, severity=Severity.MEDIA,
            evidence=f"{origin_reg} redireciona para {dest_reg} — domínio diferente. "
                     f"Se o domínio original expirar, pode ser registrado por outra pessoa.",
            details={"from": origin_reg, "to": dest_reg, "location": dest})

    return CheckResult(name=NAME, status=Status.PASS, severity=Severity.MEDIA,
                       evidence=f"Redirect para o mesmo domínio ({dest_reg}).",
                       details={"location": dest})
