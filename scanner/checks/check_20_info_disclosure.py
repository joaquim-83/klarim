"""Check 20 — Information disclosure via 403 em paths sensíveis (Severidade: BAIXA).

Passivo: HEAD em paths internos conhecidos. Um **403 (Forbidden)** confirma que o
arquivo existe (só está bloqueado) — é information disclosure. O ideal é **404**
(o servidor nem revela a existência). 200 com SPA = catch-all = PASS.
"""

from __future__ import annotations

from urllib.parse import urljoin

import httpx

from .base import CheckResult, Status, Severity, fetch, base_url

ORDER = 20
CHECK_ID = "check_20_info_disclosure"
NAME = "Diferenciação 403/404 em paths sensíveis"

_PATHS = [".git/config", ".env", "wp-admin/", ".htaccess"]


async def check(url: str) -> CheckResult:
    root = base_url(url) + "/"
    leaks: list[str] = []
    probed: list[str] = []
    responded = 0  # quantas sondas obtiveram resposta HTTP (não exceção)

    for path in _PATHS:
        target = urljoin(root, path)
        probed.append(path)
        try:
            resp = await fetch(target, method="HEAD", follow_redirects=False)
        except (httpx.HTTPError, OSError):
            try:  # alguns servidores não respondem a HEAD
                resp = await fetch(target, method="GET", follow_redirects=False)
            except (httpx.HTTPError, OSError):
                continue
        responded += 1
        if resp.status_code == 403:
            leaks.append(path)

    # Nenhuma sonda respondeu (site inacessível) → não dá para afirmar PASS.
    if responded == 0:
        return CheckResult(
            name=NAME, status=Status.INCONCLUSO, severity=Severity.BAIXA,
            evidence="Não foi possível acessar o conteúdo para verificação.",
            details={"probed": probed})

    if leaks:
        return CheckResult(
            name=NAME, status=Status.FAIL, severity=Severity.BAIXA,
            evidence=f"Path(s) retornando 403 (confirma que existe(m) no servidor): "
                     f"{', '.join('/' + p for p in leaks)}. O ideal é 404.",
            details={"forbidden": leaks, "probed": probed})

    return CheckResult(name=NAME, status=Status.PASS, severity=Severity.BAIXA,
                       evidence=f"Paths sensíveis não retornam 403 ({len(probed)} testado(s)).",
                       details={"probed": probed})
