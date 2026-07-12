"""Check 48 — Campos de senha sem proteções (Severidade: BAIXA, KL-38).

Passivo: procura ``<input type="password">`` no HTML e verifica proteções — ``autocomplete``
que impeça o navegador de salvar a senha (``off``/``new-password``) e presença de ``name``/
``id`` (campo anônimo pode ser phishing). Página sem campo de senha = não aplicável (PASS).
"""

from __future__ import annotations

import re
from typing import List

import httpx

from .base import CheckResult, Status, Severity, fetch, with_scheme

ORDER = 48
CHECK_ID = "check_48_password_fields"
NAME = "Campos de senha sem proteções"

_INPUT_RE = re.compile(r"<input\b[^>]*>", re.IGNORECASE)
_TYPE_PW_RE = re.compile(r"""type\s*=\s*["']?password""", re.IGNORECASE)
_AUTOCOMPLETE_RE = re.compile(r"""autocomplete\s*=\s*["']?([^"'\s>]+)""", re.IGNORECASE)
_NAME_RE = re.compile(r"""\b(name|id)\s*=""", re.IGNORECASE)

_PROTECTED = {"off", "new-password"}


def analyze_password_fields(html: str) -> List[dict]:
    """Retorna ``[{tag, issues}]`` dos campos de senha com problema."""
    out: List[dict] = []
    for tag in _INPUT_RE.findall(html or ""):
        if not _TYPE_PW_RE.search(tag):
            continue
        issues: List[str] = []
        m = _AUTOCOMPLETE_RE.search(tag)
        ac = m.group(1).lower() if m else None
        if ac not in _PROTECTED:
            issues.append("sem autocomplete='new-password'/'off' (o navegador pode salvar a senha)")
        if not _NAME_RE.search(tag):
            issues.append("sem name/id (campo anônimo — possível phishing)")
        if issues:
            snippet = tag if len(tag) <= 90 else tag[:87] + "...>"
            out.append({"tag": snippet, "issues": issues})
    return out


async def check(url: str) -> CheckResult:
    https_url = with_scheme(url, "https")
    try:
        resp = await fetch(https_url, method="GET", follow_redirects=True)
    except (httpx.HTTPError, OSError) as exc:
        return CheckResult(name=NAME, status=Status.INCONCLUSO, severity=Severity.BAIXA,
                           evidence=f"Falha ao obter o HTML da página: {exc!r}")

    html = resp.text or ""
    if not _TYPE_PW_RE.search(html):
        return CheckResult(name=NAME, status=Status.PASS, severity=Severity.BAIXA,
                           evidence="Nenhum campo de senha na página (não aplicável).")

    problems = analyze_password_fields(html)
    if not problems:
        return CheckResult(name=NAME, status=Status.PASS, severity=Severity.BAIXA,
                           evidence="Campos de senha com proteção de autocomplete adequada.")

    p = problems[0]
    extra = f" (+{len(problems) - 1} outro(s))" if len(problems) > 1 else ""
    return CheckResult(
        name=NAME, status=Status.FAIL, severity=Severity.BAIXA,
        evidence=f"{len(problems)} campo(s) de senha sem proteção: {'; '.join(p['issues'])}.{extra} "
                 f"Risco: senha salva/autocompletada em computador compartilhado.",
        details={"problems": problems})
