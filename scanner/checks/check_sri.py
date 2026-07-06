"""Check 13 — SRI ausente em scripts externos (Severidade: ALTA).

Subresource Integrity (``integrity="sha384-…"``) lets the browser reject a
third-party script whose bytes changed — the primary defense against a
compromised CDN silently serving malicious code. This check parses the page
HTML, isolates scripts loaded from a *different registrable domain* than the
target, and fails when more than half of them ship without an ``integrity``
attribute.

HTML is parsed with the stdlib ``html.parser`` (see ``base.extract_script_refs``)
— no BeautifulSoup dependency.
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

ORDER = 13
CHECK_ID = "check_13_sri"
NAME = "SRI ausente em scripts externos"

# FAIL when the share of external scripts without SRI exceeds this fraction.
MISSING_SRI_THRESHOLD = 0.5


async def check(url: str) -> CheckResult:
    https_url = with_scheme(url, "https")
    try:
        resp = await fetch(https_url, method="GET", follow_redirects=True)
    except (httpx.HTTPError, OSError) as exc:
        return CheckResult(
            name=NAME,
            status=Status.INCONCLUSO,
            severity=Severity.ALTA,
            evidence=f"Falha ao obter o HTML da página: {exc!r}",
        )

    scripts = extract_script_refs(resp.text, str(resp.url))
    external = [s for s in scripts if s.is_external]

    if not external:
        return CheckResult(
            name=NAME,
            status=Status.PASS,
            severity=Severity.ALTA,
            evidence="Nenhum script externo encontrado (nada a proteger com SRI).",
            details={"external_scripts": 0},
        )

    without_sri = [s for s in external if not s.has_sri]
    total = len(external)
    missing = len(without_sri)
    ratio = missing / total

    affected_domains = sorted({s.host for s in without_sri})
    without_sri_urls = [s.src for s in without_sri]

    if ratio > MISSING_SRI_THRESHOLD:
        status = Status.FAIL
        headline = (
            f"{missing} de {total} scripts externos sem SRI "
            f"({ratio:.0%}) — acima do limite de {MISSING_SRI_THRESHOLD:.0%}."
        )
    else:
        status = Status.PASS
        headline = (
            f"{missing} de {total} scripts externos sem SRI "
            f"({ratio:.0%}) — dentro do limite."
        )

    evidence = headline
    if affected_domains:
        evidence += " Domínios sem SRI: " + ", ".join(affected_domains) + "."

    return CheckResult(
        name=NAME,
        status=status,
        severity=Severity.ALTA,
        evidence=evidence,
        details={
            "external_scripts": total,
            "without_sri": missing,
            "ratio_without_sri": round(ratio, 3),
            "affected_domains": affected_domains,
            "without_sri_urls": without_sri_urls,
        },
    )
