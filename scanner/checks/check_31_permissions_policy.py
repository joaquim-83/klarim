"""Check 31 — Permissions-Policy (Severidade: MÉDIA, KL-32).

Controla o acesso do site a APIs sensíveis do navegador (câmera, microfone,
geolocalização, pagamento, USB). Ausente = o navegador permite tudo por default.
Aceita o legado ``Feature-Policy``. Passivo: só lê o header.
"""

from __future__ import annotations

import re

import httpx

from .base import CheckResult, Status, Severity, fetch, with_scheme

ORDER = 31
CHECK_ID = "check_31_permissions_policy"
NAME = "Permissions-Policy"

SENSITIVE_FEATURES = ("camera", "microphone", "geolocation", "payment", "usb")


def _wide_open(header: str, feature: str) -> bool:
    """True se ``feature`` é liberado para qualquer origem (``=*`` ou ``(*)`` ou ` *`)."""
    low = header.lower()
    # Permissions-Policy: camera=* / camera=(*) ; Feature-Policy: camera *
    return bool(re.search(rf"{feature}\s*=\s*\(?\s*\*", low)
                or re.search(rf"{feature}\s+\*", low))


async def check(url: str) -> CheckResult:
    https_url = with_scheme(url, "https")
    try:
        resp = await fetch(https_url, method="GET", follow_redirects=True)
    except (httpx.HTTPError, OSError) as exc:
        return CheckResult(name=NAME, status=Status.INCONCLUSO, severity=Severity.MEDIA,
                           evidence=f"Falha ao obter resposta HTTPS: {exc!r}")

    header = resp.headers.get("permissions-policy") or resp.headers.get("feature-policy")
    if not header or not header.strip():
        return CheckResult(
            name=NAME, status=Status.FAIL, severity=Severity.MEDIA,
            evidence="Permissions-Policy ausente — o navegador permite acesso a câmera, "
                     "microfone, geolocalização e outras APIs sensíveis por default.")

    wide = [f for f in SENSITIVE_FEATURES if _wide_open(header, f)]
    if wide:
        return CheckResult(
            name=NAME, status=Status.FAIL, severity=Severity.MEDIA,
            evidence=f"Permissions-Policy libera feature(s) sensível(is) para qualquer "
                     f"origem: {', '.join(wide)}.",
            details={"header": header, "wide_open": wide})

    return CheckResult(name=NAME, status=Status.PASS, severity=Severity.MEDIA,
                       evidence=f"Permissions-Policy presente e restringindo APIs sensíveis.",
                       details={"header": header})
