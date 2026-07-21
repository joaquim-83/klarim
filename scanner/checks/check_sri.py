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

# KL-92 P4 — CDNs de analytics/tag que atualizam o bundle SEM aviso: SRI (hash fixo) quebraria o
# script a cada release do provedor, então NÃO é viável e não deve contar como FAIL. Casa por
# sufixo de host (cobre subdomínios). Não é isenção genérica — só provedores de analytics.
SRI_ALLOWLIST_DOMAINS = (
    "googletagmanager.com",
    "google-analytics.com",
    "static.cloudflareinsights.com",
)


def _sri_allowlisted(host: str) -> bool:
    h = (host or "").lower()
    return any(h == d or h.endswith("." + d) for d in SRI_ALLOWLIST_DOMAINS)


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
    external_all = [s for s in scripts if s.is_external]
    # KL-92 P4: CDNs de analytics dinâmicos (gtag/GA/CF) NÃO entram no cálculo — SRI é inviável neles.
    allowlisted = sorted({s.host for s in external_all if _sri_allowlisted(s.host)})
    external = [s for s in external_all if not _sri_allowlisted(s.host)]

    if not external:
        note = (f" (allowlisted, CDN dinâmico: {', '.join(allowlisted)})" if allowlisted else "")
        return CheckResult(
            name=NAME,
            status=Status.PASS,
            severity=Severity.ALTA,
            evidence=f"Nenhum script externo exige SRI{note}.",
            details={"external_scripts": 0, "allowlisted_domains": allowlisted},
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
    if allowlisted:
        evidence += f" Ignorados (CDN dinâmico): {', '.join(allowlisted)}."

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
            "allowlisted_domains": allowlisted,
        },
    )
