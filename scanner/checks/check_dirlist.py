"""Check 11 — Directory listing ativo (Severidade: ALTA).

Spec: tenta GET em diretórios comuns (``/static/``, ``/uploads/``,
``/backup/``) e verifica se retorna uma listagem de diretório (autoindex).

Directory listing exposes the full inventory of a folder to anyone. We only
flag a directory when the response body carries a recognisable autoindex
signature (Apache "Index of /", nginx autoindex, IIS listing) — a normal 200
HTML page or a redirect does not count.
"""

from __future__ import annotations

import re
from urllib.parse import urljoin

import httpx

from .base import CheckResult, Status, Severity, fetch, base_url

ORDER = 11
CHECK_ID = "check_11_dirlist"
NAME = "Directory listing ativo"

_DIRS = ["/static/", "/uploads/", "/backup/", "/images/", "/assets/", "/files/"]

# Signatures of common autoindex pages.
_SIGNATURES = [
    re.compile(r"<title>\s*Index of /", re.IGNORECASE),        # Apache
    re.compile(r"<h1>\s*Index of /", re.IGNORECASE),           # Apache
    re.compile(r">\s*Parent Directory\s*<", re.IGNORECASE),    # Apache
    re.compile(r"Directory listing for ", re.IGNORECASE),      # Python http.server
    re.compile(r"<pre><a href=\"\?C=", re.IGNORECASE),         # nginx/Apache sort links
]


async def check(url: str) -> CheckResult:
    root = base_url(url) + "/"
    listable: list[str] = []
    probed: list[str] = []
    responded = 0  # quantas sondas obtiveram resposta HTTP (não exceção)

    for d in _DIRS:
        target = urljoin(root, d.lstrip("/"))
        probed.append(target)
        try:
            resp = await fetch(target, method="GET", follow_redirects=False)
        except (httpx.HTTPError, OSError):
            continue
        responded += 1
        if resp.status_code != 200:
            continue
        body = resp.text[:8192]
        if any(sig.search(body) for sig in _SIGNATURES):
            listable.append(target)

    # Nenhuma sonda respondeu (site inacessível) → não dá para afirmar PASS.
    if responded == 0:
        return CheckResult(
            name=NAME,
            status=Status.INCONCLUSO,
            severity=Severity.ALTA,
            evidence="Não foi possível acessar o conteúdo para verificação.",
            details={"probed": probed},
        )

    if listable:
        return CheckResult(
            name=NAME,
            status=Status.FAIL,
            severity=Severity.ALTA,
            evidence=(
                f"Directory listing habilitado em: {', '.join(listable)}."
            ),
            details={"listable": listable, "probed": probed},
        )

    return CheckResult(
        name=NAME,
        status=Status.PASS,
        severity=Severity.ALTA,
        evidence=(
            f"Nenhuma listagem de diretório encontrada ({len(probed)} "
            "diretório(s) testado(s))."
        ),
        details={"probed": probed},
    )
