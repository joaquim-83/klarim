"""Check 18 — CORS permissivo (Severidade: ALTA).

Passivo: um preflight ``OPTIONS`` (sem payload) com ``Origin`` forjado. Se a
resposta libera ``Access-Control-Allow-Origin: *`` ou reflete a origem forjada,
qualquer site pode chamar a API. Sem ACAO / 405 / 404 = CORS não liberado = PASS.
"""

from __future__ import annotations

import httpx

from .base import CheckResult, Status, Severity, with_scheme, USER_AGENT, REQUEST_TIMEOUT

ORDER = 18
CHECK_ID = "check_18_cors"
NAME = "CORS permissivo"

_EVIL = "https://evil-test.example"


async def _probe(url: str, method: str) -> httpx.Response:
    headers = {"User-Agent": USER_AGENT, "Origin": _EVIL,
               "Access-Control-Request-Method": "GET"}
    async with httpx.AsyncClient(verify=False, follow_redirects=True,
                                 timeout=REQUEST_TIMEOUT, headers=headers) as client:
        return await client.request(method, url)


async def check(url: str) -> CheckResult:
    https_url = with_scheme(url, "https")
    acao = None
    allow_creds = False
    for method in ("OPTIONS", "GET"):  # alguns servidores só refletem no GET
        try:
            resp = await _probe(https_url, method)
        except (httpx.HTTPError, OSError):
            continue
        val = resp.headers.get("access-control-allow-origin")
        if val:
            acao = val.strip()
            allow_creds = resp.headers.get("access-control-allow-credentials", "").strip().lower() == "true"
            break

    if acao is None:
        return CheckResult(name=NAME, status=Status.PASS, severity=Severity.ALTA,
                           evidence="CORS não libera origens externas (sem Access-Control-Allow-Origin).")

    permissive = acao == "*" or acao.lower() == _EVIL
    if permissive:
        details = {"acao": acao, "allow_credentials": allow_creds}
        # A combinação permissivo + credenciais é a vulnerabilidade real: qualquer
        # site pode ler respostas autenticadas (exfiltração cross-origin).
        if allow_creds:
            reflected = " (origem refletida)" if acao.lower() == _EVIL else ""
            return CheckResult(
                name=NAME, status=Status.FAIL, severity=Severity.ALTA,
                evidence=f"CORS permissivo COM credenciais: Access-Control-Allow-Origin: "
                         f"{acao}{reflected} + Access-Control-Allow-Credentials: true — "
                         f"qualquer site pode ler respostas autenticadas da sua API.",
                details=details)
        return CheckResult(
            name=NAME, status=Status.FAIL, severity=Severity.MEDIA,
            evidence=f"CORS permissivo: Access-Control-Allow-Origin: {acao} — "
                     f"qualquer site pode fazer requisições à sua API (sem credenciais).",
            details=details)

    return CheckResult(name=NAME, status=Status.PASS, severity=Severity.ALTA,
                       evidence=f"CORS restrito a origem específica (Access-Control-Allow-Origin: {acao}).",
                       details={"acao": acao, "allow_credentials": allow_creds})
