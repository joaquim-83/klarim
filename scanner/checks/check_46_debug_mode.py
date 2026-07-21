"""Check 46 — Indicadores de modo debug em produção (Severidade: ALTA/MÉDIA, KL-38).

Passivo: procura stack traces / erros de framework no HTML da homepage **e** numa página
de erro (GET numa URL inexistente — o que qualquer navegador faria), além de headers de
debug (Symfony). Debug em produção vaza paths, versões e lógica interna.
"""

from __future__ import annotations

import re
from typing import List

import httpx

from .base import CheckResult, Status, Severity, fetch, with_scheme, base_url, content_guard

ORDER = 46
CHECK_ID = "check_46_debug_mode"
NAME = "Indicadores de modo debug em produção"

# Path claramente inofensivo e único (não é probing de arquivo real).
_PROBE_PATH = "/klarim-nonexistent-debug-check-404"

_SCRIPT_STYLE_RE = re.compile(r"<(script|style)\b[^>]*>.*?</\1>", re.DOTALL | re.IGNORECASE)

_DEBUG_PATTERNS = (
    (re.compile(r"(?i)Traceback \(most recent call last\)"), "Python stack trace"),
    (re.compile(r"(?i)Fatal error:.*in\s+/"), "PHP fatal error com path"),
    (re.compile(r"(?i)(Notice|Warning):.*on line \d+"), "PHP notice/warning com linha"),
    (re.compile(r"(?i)SQLSTATE\["), "SQL error exposto"),
    (re.compile(r"(?i)(mysql_connect|mysqli_connect)\(\)"), "erro de conexão MySQL"),
    (re.compile(r"(?i)at\s+[\w.$]+\(\w+\.java:\d+\)"), "Java stack trace"),
    (re.compile(r"(?i)Exception in thread"), "Java exception"),
    (re.compile(r"(?i)Microsoft OLE DB Provider"), "erro OLE DB (ASP)"),
    (re.compile(r"(?i)Django\s+Version:\s*\d"), "Django debug page"),
    (re.compile(r"(?i)Whoops(,| )|laravel\s+error"), "Laravel debug page (Whoops)"),
    (re.compile(r"(?i)WP_DEBUG"), "WordPress debug ativo"),
)

_DEBUG_HEADERS = ("x-debug-token", "x-debug-token-link", "symfony-debug-toolbar-replace")


def scan_debug(text: str) -> List[str]:
    """Motivos de debug encontrados no texto (sem script/style)."""
    clean = _SCRIPT_STYLE_RE.sub(" ", text or "")
    return [reason for pat, reason in _DEBUG_PATTERNS if pat.search(clean)]


async def check(url: str) -> CheckResult:
    https_url = with_scheme(url, "https")
    try:
        resp = await fetch(https_url, method="GET", follow_redirects=True)
    except (httpx.HTTPError, OSError) as exc:
        return CheckResult(name=NAME, status=Status.INCONCLUSO, severity=Severity.ALTA,
                           evidence=f"Falha ao obter o HTML da página: {exc!r}")

    guard = content_guard(resp, NAME, Severity.ALTA)
    if guard:
        return guard

    reasons = scan_debug(resp.text or "")

    # Página de erro (GET numa URL inexistente) — best-effort.
    try:
        err = await fetch(base_url(https_url) + _PROBE_PATH, method="GET", follow_redirects=True)
        reasons += scan_debug(err.text or "")
    except (httpx.HTTPError, OSError):
        pass

    reasons = list(dict.fromkeys(reasons))  # dedup preservando ordem
    if reasons:
        return CheckResult(
            name=NAME, status=Status.FAIL, severity=Severity.ALTA,
            evidence=f"Modo debug detectado em produção: {', '.join(reasons)}.",
            details={"indicators": reasons})

    debug_headers = [h for h in _DEBUG_HEADERS if h in resp.headers]
    if debug_headers:
        return CheckResult(
            name=NAME, status=Status.FAIL, severity=Severity.MEDIA,
            evidence=f"Headers de debug expostos: {', '.join(debug_headers)}.",
            details={"headers": debug_headers})

    return CheckResult(name=NAME, status=Status.PASS, severity=Severity.ALTA,
                       evidence="Nenhum indicador de modo debug em produção.")
