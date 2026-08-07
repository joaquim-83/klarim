"""KL-149 — check de segurança de cookies. Passivo: lê os `Set-Cookie` da resposta e valida as
flags HttpOnly / Secure / SameSite dos cookies de SESSÃO (ignora analytics/consent/theme/lang)."""
from __future__ import annotations

from typing import List

import httpx

from ..models import Result, Severity, Status

# Cookies não-sessão (rastreamento/preferência) — flags de segurança não se aplicam.
_SKIP_COOKIE_SUBSTR = ("_ga", "_gid", "consent", "theme", "lang", "_gcl", "_fbp", "cf_")


def _set_cookies(response: httpx.Response) -> List[str]:
    """Lista dos headers Set-Cookie (httpx expõe `get_list`; fallback via multi_items)."""
    headers = response.headers
    if hasattr(headers, "get_list"):
        return headers.get_list("set-cookie")
    return [v for k, v in headers.multi_items() if k.lower() == "set-cookie"]


async def check_cookies(client: httpx.AsyncClient, base_url: str, config=None) -> List[Result]:
    """Cada cookie de sessão deve ter HttpOnly (anti-XSS), Secure (só HTTPS) e SameSite (anti-CSRF)."""
    try:
        r = await client.get(base_url)
    except httpx.HTTPError as exc:
        return [Result("cookies_error", "cookies", "/", Status.ERROR, Severity.INFO,
                       f"Não foi possível verificar cookies: {exc!r}")]

    results: List[Result] = []
    for cookie_str in _set_cookies(r):
        name = cookie_str.split("=", 1)[0].strip()
        lower = cookie_str.lower()
        if any(skip in name.lower() for skip in _SKIP_COOKIE_SUBSTR):
            continue   # cookie de rastreamento/preferência → não é de sessão
        if "httponly" not in lower:
            results.append(Result("cookie_no_httponly", "cookies", "/", Status.FAIL,
                                  Severity.CRITICAL,
                                  f"Cookie '{name}' sem HttpOnly (roubável via XSS)"))
        if "secure" not in lower:
            results.append(Result("cookie_no_secure", "cookies", "/", Status.FAIL, Severity.HIGH,
                                  f"Cookie '{name}' sem Secure (pode ir em HTTP claro)"))
        if "samesite" not in lower:
            results.append(Result("cookie_no_samesite", "cookies", "/", Status.FAIL, Severity.HIGH,
                                  f"Cookie '{name}' sem SameSite (exposto a CSRF)"))

    if not results:
        results.append(Result("cookies_ok", "cookies", "/", Status.PASS, Severity.CRITICAL,
                              "Cookies de sessão com HttpOnly/Secure/SameSite (ou nenhum cookie de sessão)"))
    return results
