"""Check 43 — OCSP stapling (Severidade: BAIXA, KL-37).

OCSP stapling embute a resposta de revogação no handshake — mais rápido e mais
privado (o navegador não consulta a CA). **Limitação técnica documentada:** a ``ssl``
stdlib do Python não expõe se o servidor faz stapling. Abordagem pragmática (card):
reportar a presença do **OCSP URI** no certificado (AIA) — se a CA suporta OCSP, o
stapling é possível; ausência de URI é a lacuna verificável. Compartilha o handshake.
"""

from __future__ import annotations

from .base import CheckResult, Status, Severity, host_port
from ..tls_analyzer import get_tls_info

ORDER = 43
CHECK_ID = "check_43_ocsp_stapling"
NAME = "OCSP stapling"


async def check(url: str) -> CheckResult:
    host, port = host_port(url, default=443)
    info = await get_tls_info(host, port)
    if not info.get("ok") or not info.get("cert"):
        return CheckResult(name=NAME, status=Status.INCONCLUSO, severity=Severity.BAIXA,
                           evidence=f"Não foi possível inspecionar o certificado: "
                                    f"{info.get('error') or info.get('cert_error')}")

    ocsp_uri = info["cert"].get("ocsp_uri")
    if ocsp_uri:
        return CheckResult(
            name=NAME, status=Status.PASS, severity=Severity.BAIXA,
            evidence=f"OCSP suportado pela CA (URI: {ocsp_uri}). O stapling em si não é "
                     f"verificável nesta análise passiva.",
            details={"ocsp_uri": ocsp_uri})
    return CheckResult(
        name=NAME, status=Status.FAIL, severity=Severity.BAIXA,
        evidence=f"Certificado de {host} sem OCSP URI (AIA) — verificação de revogação "
                 f"indisponível para os navegadores.",
        details={"ocsp_uri": None})
