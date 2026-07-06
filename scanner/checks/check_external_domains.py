"""Check 15 — Quantidade excessiva de domínios externos (Severidade: variável).

Every distinct third-party domain that serves a script to the page is an
additional link in the supply chain — one more party that, if compromised, can
run code in the visitor's browser. This check counts the unique *registrable
domains* (excluding the target's own) that load scripts and grades the result:

    ≤ 5 domains        -> PASS
    6–10 domains       -> PASS (registrado como observação)
    11–15 domains      -> FAIL (Média)
    16+ domains        -> FAIL (Alta)

Reuses the shared HTML script extraction (``base.extract_script_refs``).
"""

from __future__ import annotations

import httpx

from .base import (
    CheckResult,
    Status,
    Severity,
    fetch,
    with_scheme,
    extract_script_refs,
)

ORDER = 15
CHECK_ID = "check_15_external_domains"
NAME = "Domínios externos carregando scripts"

# Thresholds (inclusive upper bounds).
OK_MAX = 5          # ≤ 5 -> clean PASS
WATCH_MAX = 10      # 6–10 -> PASS with observation
MEDIUM_MAX = 15     # 11–15 -> FAIL Média; 16+ -> FAIL Alta


async def check(url: str) -> CheckResult:
    https_url = with_scheme(url, "https")
    try:
        resp = await fetch(https_url, method="GET", follow_redirects=True)
    except (httpx.HTTPError, OSError) as exc:
        return CheckResult(
            name=NAME,
            status=Status.INCONCLUSO,
            severity=Severity.MEDIA,
            evidence=f"Falha ao obter o HTML da página: {exc!r}",
        )

    scripts = extract_script_refs(resp.text, str(resp.url))
    external_domains = sorted({s.registrable for s in scripts if s.is_external})
    n = len(external_domains)
    listing = ", ".join(external_domains) if external_domains else "nenhum"

    if n <= OK_MAX:
        status, severity = Status.PASS, Severity.MEDIA
        note = f"{n} domínio(s) externo(s) detectado(s) (dentro do saudável)."
    elif n <= WATCH_MAX:
        status, severity = Status.PASS, Severity.MEDIA
        note = (
            f"{n} domínios externos detectados — observação: superfície de "
            "terceiros já considerável, monitorar."
        )
    elif n <= MEDIUM_MAX:
        status, severity = Status.FAIL, Severity.MEDIA
        note = f"{n} domínios externos detectados — superfície de terceiros alta."
    else:
        status, severity = Status.FAIL, Severity.ALTA
        note = (
            f"{n} domínios externos detectados — superfície de terceiros "
            "excessiva."
        )

    return CheckResult(
        name=NAME,
        status=status,
        severity=severity,
        evidence=f"{note} Domínios: [{listing}].",
        details={"count": n, "external_domains": external_domains},
    )
