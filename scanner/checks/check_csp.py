"""Check 05 — Content-Security-Policy: análise de qualidade (Severidade: ALTA).

Não basta o header existir (KL-32): uma CSP com ``'unsafe-inline'`` +
``'unsafe-eval'`` ou ``*`` em ``script-src``/``default-src`` **equivale a não ter
CSP** — o navegador continua executando qualquer script. Este check faz o parse da
policy e reprova configurações que anulam a proteção contra XSS; diretivas
essenciais ausentes viram nota (PASS com observação).
"""

from __future__ import annotations

from typing import Dict, List, Tuple

import httpx

from .base import CheckResult, Status, Severity, fetch, with_scheme

ORDER = 5
CHECK_ID = "check_05_csp"
NAME = "Content-Security-Policy"

# Valores perigosos em script-src/default-src que anulam a proteção contra XSS.
CSP_ISSUES = {
    "'unsafe-inline'": "permite scripts inline (anula a proteção contra XSS)",
    "'unsafe-eval'": "permite eval() (execução de código dinâmico)",
    "'unsafe-hashes'": "permite hashes inline (reduz a proteção)",
    "*": "wildcard na source (aceita qualquer origem)",
    "data:": "permite data: URIs em scripts (vetor de XSS)",
    "blob:": "permite blob: URIs (pode carregar scripts arbitrários)",
}

# Diretivas essenciais cuja ausência deixa lacunas.
CSP_MISSING_DIRECTIVES = {
    "default-src": "sem fallback — diretivas não declaradas ficam abertas",
    "script-src": "sem restrição de scripts",
    "object-src": "sem restrição de plugins (Flash/Java)",
    "base-uri": "sem restrição de base URI (hijack de paths relativos)",
    "frame-ancestors": "sem proteção contra framing (complementa X-Frame-Options)",
}


def parse_csp(header: str) -> Dict[str, List[str]]:
    """Header CSP -> ``{diretiva: [sources]}`` (diretiva em minúsculas)."""
    directives: Dict[str, List[str]] = {}
    for part in (header or "").split(";"):
        toks = part.strip().split()
        if not toks:
            continue
        directives[toks[0].lower()] = [t for t in toks[1:]]
    return directives


def analyze_csp(header: str) -> Tuple[List[str], List[str]]:
    """Retorna ``(perigosos, essenciais_ausentes)``.

    ``perigosos``: valores de ``CSP_ISSUES`` presentes em ``script-src`` ou (na sua
    ausência) ``default-src`` — o que realmente controla execução de script.
    """
    d = parse_csp(header)
    script_like = d.get("script-src", d.get("default-src", []))
    default = d.get("default-src", [])
    combined = {t.lower() for t in script_like} | {t.lower() for t in default}
    dangerous = [tok for tok in CSP_ISSUES if tok in combined]
    missing = [k for k in CSP_MISSING_DIRECTIVES if k not in d]
    return dangerous, missing


async def check(url: str) -> CheckResult:
    https_url = with_scheme(url, "https")
    try:
        resp = await fetch(https_url, method="GET", follow_redirects=True)
    except (httpx.HTTPError, OSError) as exc:
        return CheckResult(name=NAME, status=Status.INCONCLUSO, severity=Severity.ALTA,
                           evidence=f"Falha ao obter resposta HTTPS: {exc!r}")

    csp = resp.headers.get("content-security-policy")
    if not csp:
        report_only = resp.headers.get("content-security-policy-report-only")
        if report_only:
            return CheckResult(
                name=NAME, status=Status.FAIL, severity=Severity.ALTA,
                evidence="Apenas Content-Security-Policy-Report-Only presente (não "
                         "bloqueia nada, só reporta).",
                details={"report_only": report_only})
        return CheckResult(name=NAME, status=Status.FAIL, severity=Severity.ALTA,
                           evidence="Header Content-Security-Policy ausente.")

    dangerous, missing = analyze_csp(csp)
    details = {"header": csp, "dangerous": dangerous, "missing_directives": missing}

    if dangerous:
        issues = "; ".join(f"{tok} ({CSP_ISSUES[tok]})" for tok in dangerous)
        note = ""
        if "default-src" in missing:
            note = " default-src ausente."
        return CheckResult(
            name=NAME, status=Status.FAIL, severity=Severity.ALTA,
            evidence=f"CSP presente mas ineficaz: {issues}.{note} Equivale a não ter CSP.",
            details=details)

    if missing:
        notes = ", ".join(missing)
        return CheckResult(
            name=NAME, status=Status.PASS, severity=Severity.ALTA,
            evidence=f"CSP presente e sem valores perigosos. Diretivas recomendadas "
                     f"ausentes: {notes}.",
            details=details)

    preview = csp if len(csp) <= 120 else csp[:117] + "..."
    return CheckResult(name=NAME, status=Status.PASS, severity=Severity.ALTA,
                       evidence=f"Content-Security-Policy presente e bem configurada: '{preview}'.",
                       details=details)
