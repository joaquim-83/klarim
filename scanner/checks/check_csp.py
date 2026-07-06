"""Check 05 — Content-Security-Policy (Severidade: ALTA).

Spec: verifica a presença do header ``Content-Security-Policy`` na resposta
HTTPS. A CSP report-only header does not enforce anything, so it counts as a
FAIL with an explanatory note.
"""

from __future__ import annotations

import httpx

from .base import CheckResult, Status, Severity, fetch, with_scheme

ORDER = 5
CHECK_ID = "check_05_csp"
NAME = "Content-Security-Policy"


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

    csp = resp.headers.get("content-security-policy")
    if csp:
        preview = csp if len(csp) <= 120 else csp[:117] + "..."
        return CheckResult(
            name=NAME,
            status=Status.PASS,
            severity=Severity.ALTA,
            evidence=f"Content-Security-Policy presente: '{preview}'.",
            details={"header": csp},
        )

    report_only = resp.headers.get("content-security-policy-report-only")
    if report_only:
        return CheckResult(
            name=NAME,
            status=Status.FAIL,
            severity=Severity.ALTA,
            evidence=(
                "Apenas Content-Security-Policy-Report-Only presente (não "
                "bloqueia nada, só reporta)."
            ),
            details={"report_only": report_only},
        )

    return CheckResult(
        name=NAME,
        status=Status.FAIL,
        severity=Severity.ALTA,
        evidence="Header Content-Security-Policy ausente.",
    )
