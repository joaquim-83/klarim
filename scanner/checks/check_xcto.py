"""Check 07 — X-Content-Type-Options (Severidade: MÉDIA).

Spec: verifica se ``nosniff`` está presente no header
``X-Content-Type-Options``. Any value other than ``nosniff`` is treated as a
FAIL (the header only has one meaningful value).
"""

from __future__ import annotations

import httpx

from .base import CheckResult, Status, Severity, fetch, with_scheme

ORDER = 7
CHECK_ID = "check_07_xcto"
NAME = "X-Content-Type-Options"


async def check(url: str) -> CheckResult:
    https_url = with_scheme(url, "https")
    try:
        resp = await fetch(https_url, method="GET", follow_redirects=True)
    except (httpx.HTTPError, OSError) as exc:
        return CheckResult(
            name=NAME,
            status=Status.INCONCLUSO,
            severity=Severity.MEDIA,
            evidence=f"Falha ao obter resposta HTTPS: {exc!r}",
        )

    xcto = resp.headers.get("x-content-type-options")
    if xcto and xcto.strip().lower() == "nosniff":
        return CheckResult(
            name=NAME,
            status=Status.PASS,
            severity=Severity.MEDIA,
            evidence="X-Content-Type-Options: nosniff presente.",
            details={"header": xcto},
        )

    if xcto:
        return CheckResult(
            name=NAME,
            status=Status.FAIL,
            severity=Severity.MEDIA,
            evidence=f"X-Content-Type-Options presente mas sem 'nosniff': '{xcto}'.",
            details={"header": xcto},
        )

    return CheckResult(
        name=NAME,
        status=Status.FAIL,
        severity=Severity.MEDIA,
        evidence="Header X-Content-Type-Options ausente (MIME sniffing possível).",
    )
