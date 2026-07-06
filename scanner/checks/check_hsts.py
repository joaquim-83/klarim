"""Check 02 — HSTS presente (Severidade: ALTA).

Spec: verifica o header ``Strict-Transport-Security`` na resposta HTTPS.
Also inspects ``max-age`` so a token-but-weak policy (max-age=0) is reported.
"""

from __future__ import annotations

import re

import httpx

from .base import CheckResult, Status, Severity, fetch, with_scheme

ORDER = 2
CHECK_ID = "check_02_hsts"
NAME = "HSTS presente"


async def check(url: str) -> CheckResult:
    https_url = with_scheme(url, "https")
    try:
        resp = await fetch(https_url, method="GET", follow_redirects=True)
    except (httpx.HTTPError, OSError) as exc:
        return CheckResult(
            name=NAME,
            status=Status.INCONCLUSO,
            severity=Severity.ALTA,
            evidence=f"Falha ao obter resposta HTTPS: {exc!r}",
        )

    hsts = resp.headers.get("strict-transport-security")
    if not hsts:
        return CheckResult(
            name=NAME,
            status=Status.FAIL,
            severity=Severity.ALTA,
            evidence="Header Strict-Transport-Security ausente na resposta HTTPS.",
        )

    m = re.search(r"max-age\s*=\s*(\d+)", hsts, re.IGNORECASE)
    max_age = int(m.group(1)) if m else None

    if max_age is not None and max_age == 0:
        return CheckResult(
            name=NAME,
            status=Status.FAIL,
            severity=Severity.ALTA,
            evidence=f"HSTS presente porém desativado (max-age=0): '{hsts}'.",
            details={"header": hsts, "max_age": max_age},
        )

    return CheckResult(
        name=NAME,
        status=Status.PASS,
        severity=Severity.ALTA,
        evidence=f"Strict-Transport-Security presente: '{hsts}'.",
        details={"header": hsts, "max_age": max_age},
    )
