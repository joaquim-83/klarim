"""Check 14 — Scripts de fontes arriscadas (Severidade: ALTA).

Some script origins are inherently risky as production dependencies because
*anyone* can publish to them and they carry no vendor accountability: personal
GitHub Pages sites, raw GitHub files, public S3 buckets, and paste sites. A
single compromise there is a direct supply-chain attack on the target.

This reuses the shared HTML script extraction (``base.extract_script_refs``) and
FAILs if *any* script is served from one of the hardcoded risky sources.

Note: managed CDNs such as CloudFront (``*.cloudfront.net``) are NOT risky and
are intentionally excluded.
"""

from __future__ import annotations

import re

import httpx

from .base import (
    CheckResult,
    Status,
    Severity,
    fetch,
    with_scheme,
    extract_script_refs,
)

ORDER = 14
CHECK_ID = "check_14_risky_sources"
NAME = "Scripts de fontes arriscadas"

# Matches public S3 endpoints (path-style, virtual-hosted, and regional), e.g.
#   s3.amazonaws.com / bucket.s3.amazonaws.com / s3-eu-west-1.amazonaws.com /
#   bucket.s3.us-east-1.amazonaws.com  — but never cloudfront.net.
_S3_RE = re.compile(r"(^|\.)s3([.-][a-z0-9-]+)?\.amazonaws\.com$", re.IGNORECASE)

_PASTE_HOSTS = {"pastebin.com", "www.pastebin.com", "paste.ee", "www.paste.ee"}


def _risky_label(host: str) -> str | None:
    """Return a human label if ``host`` is a risky source, else None."""
    h = host.lower()
    if h.endswith(".github.io") or h == "github.io":
        return "GitHub Pages pessoal (*.github.io)"
    if h == "raw.githubusercontent.com":
        return "arquivo cru do GitHub (raw.githubusercontent.com)"
    if h in _PASTE_HOSTS:
        return "paste site"
    if _S3_RE.search(h):
        return "bucket S3 público (amazonaws.com)"
    return None


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

    risky = []
    for s in scripts:
        label = _risky_label(s.host)
        if label:
            risky.append({"url": s.src, "host": s.host, "reason": label})

    if risky:
        listing = "; ".join(f"{r['url']} → {r['reason']}" for r in risky)
        return CheckResult(
            name=NAME,
            status=Status.FAIL,
            severity=Severity.ALTA,
            evidence=(
                f"{len(risky)} script(s) carregado(s) de fonte(s) arriscada(s): "
                f"{listing}."
            ),
            details={"risky_scripts": risky, "total_scripts": len(scripts)},
        )

    return CheckResult(
        name=NAME,
        status=Status.PASS,
        severity=Severity.ALTA,
        evidence=(
            f"Nenhum script de fonte arriscada ({len(scripts)} script(s) "
            "inspecionado(s))."
        ),
        details={"total_scripts": len(scripts)},
    )
