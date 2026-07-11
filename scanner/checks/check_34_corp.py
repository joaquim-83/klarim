"""Check 34 — Cross-Origin-Resource-Policy / CORP (Severidade: BAIXA, KL-32).

Controla quem pode carregar os recursos do site (``same-site``, ``same-origin``,
``cross-origin``). Ausente = qualquer site pode embutir os recursos. Passivo.
"""

from __future__ import annotations

import httpx

from .base import CheckResult, Status, Severity, fetch, with_scheme

ORDER = 34
CHECK_ID = "check_34_corp"
NAME = "Cross-Origin-Resource-Policy (CORP)"

_VALID = ("same-site", "same-origin", "cross-origin")


async def check(url: str) -> CheckResult:
    https_url = with_scheme(url, "https")
    try:
        resp = await fetch(https_url, method="GET", follow_redirects=True)
    except (httpx.HTTPError, OSError) as exc:
        return CheckResult(name=NAME, status=Status.INCONCLUSO, severity=Severity.BAIXA,
                           evidence=f"Falha ao obter resposta HTTPS: {exc!r}")

    val = (resp.headers.get("cross-origin-resource-policy") or "").strip().lower()
    if not val:
        return CheckResult(name=NAME, status=Status.FAIL, severity=Severity.BAIXA,
                           evidence="Cross-Origin-Resource-Policy (CORP) ausente — os recursos "
                                    "do site podem ser carregados por qualquer origem.")
    if val in _VALID:
        return CheckResult(name=NAME, status=Status.PASS, severity=Severity.BAIXA,
                           evidence=f"CORP presente ({val}).", details={"header": val})
    return CheckResult(name=NAME, status=Status.FAIL, severity=Severity.BAIXA,
                       evidence=f"CORP presente com valor não reconhecido ({val}).",
                       details={"header": val})
