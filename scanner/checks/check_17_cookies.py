"""Check 17 — Cookies sem flags de segurança: análise por cookie (Severidade: MÉDIA).

Passivo: GET na URL e leitura dos headers ``Set-Cookie``. Além das flags básicas em
cookies de sessão (Secure/HttpOnly/SameSite), o KL-32 avalia combinações perigosas
por cookie: ``SameSite=None`` sem ``Secure`` (rejeitado por navegadores modernos),
``Domain`` amplo demais (ex.: ``.com.br``) e prefixos ``__Secure-``/``__Host-`` sem a
flag exigida. Ausência de cookies = PASS.
"""

from __future__ import annotations

from typing import Dict, List, Tuple

import httpx

from .base import CheckResult, Status, Severity, fetch, with_scheme, _TWO_LABEL_SUFFIXES

ORDER = 17
CHECK_ID = "check_17_cookies"
NAME = "Cookies sem flags de segurança"

_SENSITIVE = ("session", "sess", "sid", "phpsessid", "token", "auth", "csrf", "jwt")


def _parse_cookie(raw: str) -> Tuple[str, Dict[str, object]]:
    parts = [p.strip() for p in raw.split(";")]
    name = parts[0].split("=", 1)[0].strip()
    attrs: Dict[str, object] = {}
    for p in parts[1:]:
        if "=" in p:
            k, v = p.split("=", 1)
            attrs[k.strip().lower()] = v.strip()
        elif p:
            attrs[p.strip().lower()] = True
    return name, attrs


def _is_broad_domain(dom: str) -> bool:
    dom = dom.lstrip(".").lower()
    if not dom:
        return False
    if "." not in dom:
        return True                    # TLD isolado (ex.: "com")
    return dom in _TWO_LABEL_SUFFIXES   # public suffix (ex.: "com.br")


def analyze_cookie(raw: str) -> Tuple[str, List[str]]:
    """Retorna ``(nome, [problemas])`` de um header ``Set-Cookie``."""
    name, attrs = _parse_cookie(raw)
    lname = name.lower()
    secure = "secure" in attrs
    httponly = "httponly" in attrs
    samesite = str(attrs.get("samesite", "")).lower()
    is_session = any(s in lname for s in _SENSITIVE)
    issues: List[str] = []

    if samesite == "none" and not secure:
        issues.append("SameSite=None sem Secure (rejeitado por navegadores modernos)")
    if lname.startswith("__secure-") and not secure:
        issues.append("prefixo __Secure- sem a flag Secure")
    if lname.startswith("__host-") and (not secure or attrs.get("domain")):
        issues.append("prefixo __Host- sem Secure/Path corretos")
    dom = str(attrs.get("domain", ""))
    if _is_broad_domain(dom):
        issues.append(f"Domain muito amplo ({dom}) — enviado a outros sites do sufixo")
    if is_session:
        if not httponly:
            issues.append("cookie de sessão sem HttpOnly")
        if not secure:
            issues.append("cookie de sessão sem Secure")
        if not samesite:
            issues.append("cookie de sessão sem SameSite")
    return name, issues


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

    problems: List[dict] = []
    for raw in set_cookies:
        name, issues = analyze_cookie(raw)
        if issues:
            problems.append({"cookie": name, "issues": issues})

    if problems:
        p = problems[0]
        extra = f" (+{len(problems) - 1} outro(s) cookie(s))" if len(problems) > 1 else ""
        return CheckResult(
            name=NAME, status=Status.FAIL, severity=Severity.MEDIA,
            evidence=f"Cookie '{p['cookie']}': {'; '.join(p['issues'])}.{extra}",
            details={"problems": problems})

    return CheckResult(name=NAME, status=Status.PASS, severity=Severity.MEDIA,
                       evidence=f"Cookies com as flags de segurança adequadas "
                                f"({len(set_cookies)} cookie(s)).")
