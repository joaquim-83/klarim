"""KL-149 — check de ERROR DISCLOSURE: páginas de erro (404/5xx) não podem vazar stack trace,
caminhos internos, nome de framework ou modo debug.

⚠️ Passivo por design (o Gate "não é DAST" — ver `security_gate/__init__.py`). NÃO enviamos
payloads de injeção (SQLi/XSS) — regra inviolável da Klarim. O 404 é um path aleatório inexistente;
o teste de 5xx usa inputs **malformados benignos** (percent-encoding inválido, valor muito longo)
que costumam disparar erro de parsing SEM ser um ataque. Só LEMOS o corpo em busca de marcadores."""
from __future__ import annotations

import uuid
from typing import List

import httpx

from ..models import Result, Severity, Status

# Marcadores que denunciam vazamento de informação interna no corpo do erro.
STACK_MARKERS = [
    "traceback", "exception", "stack trace", "stacktrace", "at line", 'file "',
    "/home/", "/app/", "/var/www/", "/opt/", "node_modules/", "site-packages/",
    "django", "flask", "werkzeug", "express", "laravel", "rails", "spring",
    "sqlalchemy", "psycopg", "pymongo", "sequelize", "nullpointerexception",
    "debug mode", "debug=true", "django_settings", "whoops\\",
    "syntax error", "undefined variable", "fatal error", "warning:",
]

# Inputs malformados BENIGNOS (não são payloads de ataque) — provocam erro de parsing/encoding.
_MALFORMED_PROBES = ["?q=%c0%ae", "?q=" + "A" * 6000, "?%ff%fe=1"]


def _find_marker(body: str) -> str:
    low = body[:5000].lower()
    for marker in STACK_MARKERS:
        if marker in low:
            return marker
    return ""


async def check_error_disclosure(client: httpx.AsyncClient, base_url: str, config=None) -> List[Result]:
    base = base_url.rstrip("/")
    results: List[Result] = []

    # 404 — path aleatório inexistente (totalmente passivo).
    try:
        r = await client.get(f"{base}/klarim_gate_404_probe_{uuid.uuid4().hex[:6]}")
        if r.status_code >= 400:
            marker = _find_marker(r.text)
            if marker:
                results.append(Result("error_disclosure_404", "error_disclosure", "/404",
                                      Status.FAIL, Severity.HIGH,
                                      f"Página de erro 404 expõe informação interna ('{marker}')"))
    except httpx.HTTPError:
        pass

    # 5xx — inputs malformados benignos (sem SQLi/XSS). Só vira finding se der 5xx COM marcador.
    for probe in _MALFORMED_PROBES:
        try:
            r = await client.get(f"{base}/{probe}")
        except httpx.HTTPError:
            continue
        if r.status_code >= 500:
            marker = _find_marker(r.text)
            if marker:
                results.append(Result("error_disclosure_500", "error_disclosure", "/500",
                                      Status.FAIL, Severity.HIGH,
                                      f"Página de erro 5xx expõe stack trace ('{marker}')"))
                break

    if not results:
        results.append(Result("error_disclosure_ok", "error_disclosure", "/", Status.PASS,
                              Severity.HIGH, "Páginas de erro não expõem informação interna"))
    return results
