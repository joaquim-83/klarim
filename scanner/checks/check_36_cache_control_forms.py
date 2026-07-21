"""Check 36 — Cache-Control em páginas sensíveis (Severidade: MÉDIA, KL-32).

Páginas com ``<form>`` ou ``<input type="password">`` devem enviar ``Cache-Control:
no-store`` (ou ``no-cache``/``private``) para não deixar dados sensíveis no cache do
navegador/proxy — risco real em computadores compartilhados. Passivo: reusa o HTML +
headers da mesma resposta. Página sem formulário = não aplicável (PASS).
"""

from __future__ import annotations

import re

import httpx

from .base import CheckResult, Status, Severity, fetch, with_scheme, content_guard

ORDER = 36
CHECK_ID = "check_36_cache_control_forms"
NAME = "Cache-Control em páginas sensíveis"

_FORM_RE = re.compile(r"<form\b", re.IGNORECASE)
_PASSWORD_RE = re.compile(r"<input\b[^>]*\btype\s*=\s*[\"']?password", re.IGNORECASE)
# Diretivas que impedem cache sensível.
_SAFE_TOKENS = ("no-store", "no-cache", "private")


async def check(url: str) -> CheckResult:
    https_url = with_scheme(url, "https")
    try:
        resp = await fetch(https_url, method="GET", follow_redirects=True)
    except (httpx.HTTPError, OSError) as exc:
        return CheckResult(name=NAME, status=Status.INCONCLUSO, severity=Severity.MEDIA,
                           evidence=f"Falha ao obter resposta HTTPS: {exc!r}")

    guard = content_guard(resp, NAME, Severity.MEDIA)
    if guard:
        return guard

    html = resp.text or ""
    has_password = bool(_PASSWORD_RE.search(html))
    has_form = bool(_FORM_RE.search(html))
    if not has_form and not has_password:
        return CheckResult(name=NAME, status=Status.PASS, severity=Severity.MEDIA,
                           evidence="Página sem formulário — Cache-Control sensível não se aplica.")

    cache = (resp.headers.get("cache-control") or "").strip().lower()
    what = "campo de senha" if has_password else "formulário"
    if any(tok in cache for tok in _SAFE_TOKENS):
        return CheckResult(
            name=NAME, status=Status.PASS, severity=Severity.MEDIA,
            evidence=f"Página com {what} e Cache-Control adequado ('{cache}').",
            details={"cache_control": cache, "has_password": has_password})
    detail = f"Cache-Control: '{cache}'" if cache else "sem Cache-Control"
    return CheckResult(
        name=NAME, status=Status.FAIL, severity=Severity.MEDIA,
        evidence=f"Página com {what} sem proteção de cache ({detail}) — dados sensíveis "
                 f"podem ficar no cache do navegador/proxy. Recomendado 'no-store'.",
        details={"cache_control": cache, "has_password": has_password})
