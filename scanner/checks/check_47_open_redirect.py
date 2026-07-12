"""Check 47 — Padrões de open redirect (Severidade: BAIXA/MÉDIA, KL-38).

Passivo: detecta a **presença** de parâmetros de redirect (``?redirect=``, ``?next=``,
``?url=``…) em links/forms do HTML. **Não testa** se o redirect é explorável (isso depende
da validação no servidor) — por isso severidade reduzida.
"""

from __future__ import annotations

import re
from typing import List

import httpx

from .base import CheckResult, Status, Severity, fetch, with_scheme

ORDER = 47
CHECK_ID = "check_47_open_redirect"
NAME = "Padrões de open redirect"

REDIRECT_PARAMS = [
    "redirect", "redirect_to", "redirect_url", "redirect_uri",
    "url", "next", "return", "return_to", "return_url",
    "continue", "dest", "destination", "go", "goto",
    "out", "redir", "target", "link",
]

_REDIRECT_RE = re.compile(
    r"""(?:href|action|src)\s*=\s*["'][^"']*[?&](?:"""
    + "|".join(REDIRECT_PARAMS) + r""")\s*=""",
    re.IGNORECASE,
)
_SAMPLE_RE = re.compile(r"""["']([^"']*[?&](?:""" + "|".join(REDIRECT_PARAMS)
                        + r""")=[^"']*)["']""", re.IGNORECASE)

MANY_THRESHOLD = 5


async def check(url: str) -> CheckResult:
    https_url = with_scheme(url, "https")
    try:
        resp = await fetch(https_url, method="GET", follow_redirects=True)
    except (httpx.HTTPError, OSError) as exc:
        return CheckResult(name=NAME, status=Status.INCONCLUSO, severity=Severity.BAIXA,
                           evidence=f"Falha ao obter o HTML da página: {exc!r}")

    html = resp.text or ""
    matches = _REDIRECT_RE.findall(html)
    count = len(matches)
    if count == 0:
        return CheckResult(name=NAME, status=Status.PASS, severity=Severity.BAIXA,
                           evidence="Nenhum padrão de open redirect encontrado nos links.")

    samples: List[str] = []
    for m in _SAMPLE_RE.findall(html):
        s = m if len(m) <= 80 else m[:77] + "..."
        if s not in samples:
            samples.append(s)
        if len(samples) >= 3:
            break

    severity = Severity.MEDIA if count > MANY_THRESHOLD else Severity.BAIXA
    return CheckResult(
        name=NAME, status=Status.FAIL, severity=severity,
        evidence=f"{count} padrão(ões) de redirect detectado(s) (verificação passiva — pode "
                 f"não ser explorável): {'; '.join(samples)}. Validar que o parâmetro só "
                 f"aceita URLs do mesmo domínio.",
        details={"count": count, "samples": samples})
