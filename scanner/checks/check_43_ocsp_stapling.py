"""Check 43 — OCSP stapling (Severidade: BAIXA, KL-37).

OCSP stapling embute a resposta de revogação no handshake — mais rápido e mais
privado (o navegador não consulta a CA). **Limitação técnica documentada:** a ``ssl``
stdlib do Python não expõe se o servidor faz stapling. Abordagem pragmática (card):
reportar a presença do **OCSP URI** no certificado (AIA).

**Atualização 2026 (KL-51 f3 fix):** o OCSP está sendo **descontinuado** pela indústria —
a Let's Encrypt (CA dominante) parou de emitir OCSP e **removeu o OCSP URI dos
certificados** (a revogação passou a depender de CRL / certificados de vida curta).
Logo, a **ausência de OCSP URI num certificado moderno NÃO é uma falha** — penalizá-la
gera falso-positivo na maioria dos sites (todos com cert Let's Encrypt recente). Por isso
a ausência agora é **INCONCLUSO** (neutro, não derruba o score), não FAIL; a presença
continua PASS. O stapling em si nunca foi verificável nesta análise passiva.
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
    # Sem OCSP URI: com a descontinuação do OCSP (Let's Encrypt removeu o URI em 2025),
    # isso é o NOVO NORMAL de certificados modernos — não é falha. INCONCLUSO (neutro).
    return CheckResult(
        name=NAME, status=Status.INCONCLUSO, severity=Severity.BAIXA,
        evidence=f"Certificado de {host} sem OCSP URI (AIA). O OCSP está em descontinuação "
                 f"(a Let's Encrypt removeu o URI dos certificados); a revogação passou a "
                 f"depender de CRL / certificados de vida curta — não é uma falha.",
        details={"ocsp_uri": None})
