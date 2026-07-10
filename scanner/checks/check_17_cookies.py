"""Check 17 — Cookies de sessão sem flags de segurança (Severidade: MÉDIA).

Passivo: GET na URL e leitura dos headers ``Set-Cookie``. Cookies de
sessão/autenticação (nome com session/sid/token/auth/csrf) precisam de `Secure`,
`HttpOnly` e `SameSite`. Ausência de cookies = PASS (não há o que proteger).
"""

from __future__ import annotations

import httpx

from .base import CheckResult, Status, Severity, fetch, with_scheme

ORDER = 17
CHECK_ID = "check_17_cookies"
NAME = "Cookies sem flags de segurança"

_SENSITIVE = ("session", "sid", "token", "auth", "csrf", "jwt")


def _cookie_name(raw: str) -> str:
    return raw.split("=", 1)[0].strip().lower()


async def check(url: str) -> CheckResult:
    https_url = with_scheme(url, "https")
    try:
        resp = await fetch(https_url, method="GET", follow_redirects=True)
    except (httpx.HTTPError, OSError) as exc:
        return CheckResult(name=NAME, status=Status.INCONCLUSO, severity=Severity.MEDIA,
                           evidence=f"Falha ao obter resposta: {exc!r}")

    set_cookies = resp.headers.get_list("set-cookie")
    if not set_cookies:
        return CheckResult(name=NAME, status=Status.PASS, severity=Severity.MEDIA,
                           evidence="Nenhum cookie definido pela página (nada a proteger).")

    problems: list[dict] = []
    for raw in set_cookies:
        name = _cookie_name(raw)
        if not any(s in name for s in _SENSITIVE):
            continue
        low = raw.lower()
        missing = [flag for flag, tok in
                   (("Secure", "secure"), ("HttpOnly", "httponly"), ("SameSite", "samesite"))
                   if tok not in low]
        if missing:
            problems.append({"cookie": name, "missing": missing})

    if problems:
        p = problems[0]
        return CheckResult(
            name=NAME, status=Status.FAIL, severity=Severity.MEDIA,
            evidence=f"Cookie '{p['cookie']}' sem flag(s) {', '.join(p['missing'])} "
                     f"— pode ser roubado por script malicioso.",
            details={"problems": problems})

    return CheckResult(name=NAME, status=Status.PASS, severity=Severity.MEDIA,
                       evidence=f"Cookies de sessão com as flags de segurança "
                                f"({len(set_cookies)} cookie(s)).")
