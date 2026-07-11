"""Check 33 — Cross-Origin-Embedder-Policy / COEP (Severidade: BAIXA, KL-32).

Controla o embedding de recursos cross-origin (exige opt-in dos recursos). Valores
seguros: ``require-corp``, ``credentialless``. Header moderno, adoção baixa. Passivo.
"""

from __future__ import annotations

import httpx

from .base import CheckResult, Status, Severity, fetch, with_scheme

ORDER = 33
CHECK_ID = "check_33_coep"
NAME = "Cross-Origin-Embedder-Policy (COEP)"

_SAFE = ("require-corp", "credentialless")


async def check(url: str) -> CheckResult:
    https_url = with_scheme(url, "https")
    try:
        resp = await fetch(https_url, method="GET", follow_redirects=True)
    except (httpx.HTTPError, OSError) as exc:
        return CheckResult(name=NAME, status=Status.INCONCLUSO, severity=Severity.BAIXA,
                           evidence=f"Falha ao obter resposta HTTPS: {exc!r}")

    val = (resp.headers.get("cross-origin-embedder-policy") or "").strip().lower()
    if not val:
        return CheckResult(name=NAME, status=Status.FAIL, severity=Severity.BAIXA,
                           evidence="Cross-Origin-Embedder-Policy (COEP) ausente — recursos "
                                    "cross-origin podem ser embutidos sem opt-in.")
    if any(val.startswith(s) for s in _SAFE):
        return CheckResult(name=NAME, status=Status.PASS, severity=Severity.BAIXA,
                           evidence=f"COEP presente com valor seguro ({val}).",
                           details={"header": val})
    return CheckResult(name=NAME, status=Status.FAIL, severity=Severity.BAIXA,
                       evidence=f"COEP presente com valor não seguro ({val}).",
                       details={"header": val})
