"""Check 32 — Cross-Origin-Opener-Policy / COOP (Severidade: BAIXA, KL-32).

Protege contra ataques cross-origin via ``window.opener`` isolando o contexto de
navegação. Valores seguros: ``same-origin``, ``same-origin-allow-popups``. Header
moderno com adoção baixa — daí a severidade BAIXA. Passivo: só lê o header.
"""

from __future__ import annotations

import httpx

from .base import CheckResult, Status, Severity, fetch, with_scheme

ORDER = 32
CHECK_ID = "check_32_coop"
NAME = "Cross-Origin-Opener-Policy (COOP)"

_SAFE = ("same-origin", "same-origin-allow-popups")


async def check(url: str) -> CheckResult:
    https_url = with_scheme(url, "https")
    try:
        resp = await fetch(https_url, method="GET", follow_redirects=True)
    except (httpx.HTTPError, OSError) as exc:
        return CheckResult(name=NAME, status=Status.INCONCLUSO, severity=Severity.BAIXA,
                           evidence=f"Falha ao obter resposta HTTPS: {exc!r}")

    val = (resp.headers.get("cross-origin-opener-policy") or "").strip().lower()
    if not val:
        return CheckResult(name=NAME, status=Status.FAIL, severity=Severity.BAIXA,
                           evidence="Cross-Origin-Opener-Policy (COOP) ausente — sem "
                                    "isolamento do contexto contra ataques via window.opener.")
    if val == "unsafe-none":
        return CheckResult(name=NAME, status=Status.FAIL, severity=Severity.BAIXA,
                           evidence="COOP presente porém desativado (unsafe-none).",
                           details={"header": val})
    if any(val.startswith(s) for s in _SAFE):
        return CheckResult(name=NAME, status=Status.PASS, severity=Severity.BAIXA,
                           evidence=f"COOP presente com valor seguro ({val}).",
                           details={"header": val})
    return CheckResult(name=NAME, status=Status.FAIL, severity=Severity.BAIXA,
                       evidence=f"COOP presente com valor não reconhecido/inseguro ({val}).",
                       details={"header": val})
