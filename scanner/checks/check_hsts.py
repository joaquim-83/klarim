"""Check 02 — HSTS: qualidade da política (Severidade: ALTA).

Não basta o header existir (KL-32): um ``max-age`` curto dá proteção efêmera. Este
check avalia ``max-age`` (mínimo 6 meses, ideal 1 ano), ``includeSubDomains`` e
``preload``. FAIL quando ``max-age`` é ausente/0/curto; PASS (com notas) quando é
aceitável mas poderia ser mais forte.
"""

from __future__ import annotations

import re

import httpx

from .base import CheckResult, Status, Severity, fetch, with_scheme

ORDER = 2
CHECK_ID = "check_02_hsts"
NAME = "HSTS presente"

HSTS_MAX_AGE_MIN = 15_768_000     # 6 meses (mínimo aceitável)
HSTS_MAX_AGE_RECOMMENDED = 31_536_000  # 1 ano (recomendado)


def _human_age(seconds: int) -> str:
    if seconds >= 86400:
        return f"{seconds // 86400} dia(s)"
    if seconds >= 3600:
        return f"{seconds // 3600} hora(s)"
    return f"{seconds}s"


async def check(url: str) -> CheckResult:
    https_url = with_scheme(url, "https")
    try:
        resp = await fetch(https_url, method="GET", follow_redirects=True)
    except (httpx.HTTPError, OSError) as exc:
        return CheckResult(name=NAME, status=Status.INCONCLUSO, severity=Severity.ALTA,
                           evidence=f"Falha ao obter resposta HTTPS: {exc!r}")

    hsts = resp.headers.get("strict-transport-security")
    if not hsts:
        return CheckResult(name=NAME, status=Status.FAIL, severity=Severity.ALTA,
                           evidence="Header Strict-Transport-Security ausente na resposta HTTPS.")

    low = hsts.lower()
    m = re.search(r"max-age\s*=\s*(\d+)", low)
    max_age = int(m.group(1)) if m else None
    include_sub = "includesubdomains" in low
    preload = "preload" in low
    details = {"header": hsts, "max_age": max_age,
               "include_subdomains": include_sub, "preload": preload}

    if max_age is None:
        return CheckResult(name=NAME, status=Status.FAIL, severity=Severity.ALTA,
                           evidence=f"HSTS presente sem max-age válido: '{hsts}'.",
                           details=details)
    if max_age == 0:
        return CheckResult(name=NAME, status=Status.FAIL, severity=Severity.ALTA,
                           evidence=f"HSTS presente porém desativado (max-age=0): '{hsts}'.",
                           details=details)
    if max_age < HSTS_MAX_AGE_MIN:
        return CheckResult(
            name=NAME, status=Status.FAIL, severity=Severity.ALTA,
            evidence=f"HSTS presente com max-age muito curto ({_human_age(max_age)}): "
                     f"proteção efêmera. Recomendado: max-age={HSTS_MAX_AGE_RECOMMENDED} "
                     f"(1 ano), includeSubDomains e preload.",
            details=details)

    # Aceitável (>= 6 meses). Notas do que ainda pode melhorar.
    notes = []
    if max_age < HSTS_MAX_AGE_RECOMMENDED:
        notes.append(f"max-age de {_human_age(max_age)} é abaixo do recomendado de 1 ano")
    if not include_sub:
        notes.append("sem includeSubDomains (subdomínios não protegidos)")
    if not preload:
        notes.append("sem preload (não elegível para a HSTS preload list)")
    note = f" Observações: {'; '.join(notes)}." if notes else ""
    return CheckResult(
        name=NAME, status=Status.PASS, severity=Severity.ALTA,
        evidence=f"HSTS presente com max-age adequado ({_human_age(max_age)}).{note}",
        details=details)
