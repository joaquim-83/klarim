"""Check 06 — X-Frame-Options (Severidade: MÉDIA).

Spec: verifica presença do header ``X-Frame-Options`` (proteção contra
clickjacking). A modern equivalent is a CSP ``frame-ancestors`` directive; if
XFO is absent but ``frame-ancestors`` is present, we still PASS but note it.
"""

from __future__ import annotations

import httpx

from .base import CheckResult, Status, Severity, fetch, with_scheme

ORDER = 6
CHECK_ID = "check_06_xfo"
NAME = "X-Frame-Options"

_VALID_XFO = {"deny", "sameorigin"}


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

    xfo = resp.headers.get("x-frame-options")
    if xfo:
        value = xfo.strip().lower()
        if value in _VALID_XFO or value.startswith("allow-from"):
            return CheckResult(
                name=NAME,
                status=Status.PASS,
                severity=Severity.MEDIA,
                evidence=f"X-Frame-Options presente: '{xfo}'.",
                details={"header": xfo},
            )
        return CheckResult(
            name=NAME,
            status=Status.FAIL,
            severity=Severity.MEDIA,
            evidence=f"X-Frame-Options presente porém com valor inválido: '{xfo}'.",
            details={"header": xfo},
        )

    # Fallback: CSP frame-ancestors provides equivalent protection.
    csp = resp.headers.get("content-security-policy", "")
    if "frame-ancestors" in csp.lower():
        return CheckResult(
            name=NAME,
            status=Status.PASS,
            severity=Severity.MEDIA,
            evidence=(
                "X-Frame-Options ausente, porém CSP 'frame-ancestors' fornece "
                "proteção equivalente contra clickjacking."
            ),
            details={"csp": csp},
        )

    return CheckResult(
        name=NAME,
        status=Status.FAIL,
        severity=Severity.MEDIA,
        evidence="Header X-Frame-Options ausente (sem proteção contra clickjacking).",
    )
