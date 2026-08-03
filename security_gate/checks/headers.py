"""KL-141 — checks de HEADERS de segurança (HSTS, CSP, X-Frame-Options, X-Content-Type-
Options, Referrer-Policy, Permissions-Policy, Server).

O `scanner/` já valida estes headers, mas os checks lá são coroutines acopladas ao próprio
`fetch()` (com rate-limit por domínio) — não são validadores puros importáveis, e o engine do
Gate usa UM único response compartilhado (`client.get`). Então reusamos o que é reusável — o
**threshold do HSTS** (`HSTS_MAX_AGE_RECOMMENDED`, importado do scanner p/ não divergir) — e a
extração/validação fica local, no modelo de 1-request do Gate."""
from __future__ import annotations

import re
from typing import List, Optional, Tuple

import httpx

from scanner.checks.check_hsts import HSTS_MAX_AGE_RECOMMENDED  # reuso do threshold (1 ano)

from ..models import Result, Severity, Status

_MAX_AGE_RE = re.compile(r"max-age\s*=\s*(\d+)", re.I)
_SERVER_VERSION_RE = re.compile(r"/\s*\d")   # "nginx/1.25.3", "Apache/2.4" → expõe versão


def _check_hsts(header: str, value: Optional[str]) -> Tuple[Status, str]:
    if not value:
        return Status.FAIL, "Strict-Transport-Security ausente na resposta HTTPS."
    m = _MAX_AGE_RE.search(value)
    max_age = int(m.group(1)) if m else 0
    if max_age >= HSTS_MAX_AGE_RECOMMENDED:
        return Status.PASS, f"HSTS presente (max-age={max_age})."
    if max_age <= 0:
        return Status.FAIL, f"HSTS presente porém sem max-age válido: '{value}'."
    return (Status.FAIL,
            f"HSTS com max-age curto ({max_age}s); recomendado ≥ {HSTS_MAX_AGE_RECOMMENDED} (1 ano).")


def _check_csp(header: str, value: Optional[str]) -> Tuple[Status, str]:
    if value and value.strip():
        return Status.PASS, "Content-Security-Policy presente."
    return Status.FAIL, "Content-Security-Policy ausente."


def _check_present(header: str, value: Optional[str]) -> Tuple[Status, str]:
    if value and value.strip():
        return Status.PASS, f"{header} presente: {value}."
    return Status.FAIL, f"{header} ausente."


def _check_nosniff(header: str, value: Optional[str]) -> Tuple[Status, str]:
    if value and value.strip().lower() == "nosniff":
        return Status.PASS, "X-Content-Type-Options: nosniff presente."
    if value:
        return Status.FAIL, f"X-Content-Type-Options presente mas não é 'nosniff': '{value}'."
    return Status.FAIL, "X-Content-Type-Options ausente."


def _check_server_not_verbose(header: str, value: Optional[str]) -> Tuple[Status, str]:
    # Absente ou sem versão → PASS (não vaza nada). Com versão (nginx/1.x) → FAIL.
    if not value:
        return Status.PASS, "Header Server ausente (não vaza versão)."
    if _SERVER_VERSION_RE.search(value):
        return Status.FAIL, f"Header Server expõe a versão: '{value}'."
    return Status.PASS, f"Header Server sem versão: '{value}'."


# (nome_curto, nome_do_header, severidade, validador)
_HEADER_CHECKS = (
    ("hsts", "Strict-Transport-Security", Severity.HIGH, _check_hsts),
    ("csp", "Content-Security-Policy", Severity.HIGH, _check_csp),
    ("x_frame_options", "X-Frame-Options", Severity.MEDIUM, _check_present),
    ("x_content_type", "X-Content-Type-Options", Severity.MEDIUM, _check_nosniff),
    ("referrer_policy", "Referrer-Policy", Severity.MEDIUM, _check_present),
    ("permissions_policy", "Permissions-Policy", Severity.LOW, _check_present),
    ("server_info", "Server", Severity.LOW, _check_server_not_verbose),
)


async def check_headers(client: httpx.AsyncClient, base_url: str) -> List[Result]:
    """Um GET no alvo → valida os 7 headers de segurança sobre o MESMO response."""
    r = await client.get(base_url)
    results: List[Result] = []
    for check_name, header, severity, validator in _HEADER_CHECKS:
        value = r.headers.get(header)   # httpx.Headers.get é case-insensitive
        status, detail = validator(header, value)
        results.append(Result(
            check=f"header_{check_name}", category="headers", path="/",
            status=status, severity=severity, detail=detail))
    return results
