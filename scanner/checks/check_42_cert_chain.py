"""Check 42 — Certificate chain (Severidade: MÉDIA, KL-37).

A validação de cadeia (leaf → intermediários → root) já é feita pelo handshake
verificado. Este check extrai os dados da cadeia para o relatório e reprova cenários
subótimos: certificado **self-signed**, cadeia que **não valida**, e alerta quando a
expiração está próxima. Compartilha o handshake com 41/43/44. 100% passivo.
"""

from __future__ import annotations

from datetime import datetime, timezone

from .base import CheckResult, Status, Severity, host_port
from ..tls_analyzer import get_tls_info

ORDER = 42
CHECK_ID = "check_42_cert_chain"
NAME = "Certificate chain"

EXPIRY_WARN_DAYS = 30


async def check(url: str) -> CheckResult:
    host, port = host_port(url, default=443)
    info = await get_tls_info(host, port)
    if not info.get("ok") or not info.get("cert"):
        return CheckResult(name=NAME, status=Status.INCONCLUSO, severity=Severity.MEDIA,
                           evidence=f"Não foi possível obter a cadeia de certificados: "
                                    f"{info.get('error') or info.get('cert_error')}")

    cert = info["cert"]
    issuer = cert.get("issuer_cn") or "n/d"
    not_after = cert.get("not_after")
    details = {
        "subject_cn": cert.get("subject_cn"), "issuer_cn": cert.get("issuer_cn"),
        "not_after": not_after.isoformat() if not_after else None,
        "san": cert.get("san"), "self_signed": cert.get("self_signed"),
        "verified": info.get("verified"),
    }

    if cert.get("self_signed"):
        return CheckResult(
            name=NAME, status=Status.FAIL, severity=Severity.MEDIA,
            evidence=f"Certificado self-signed (emissor == titular) em {host} — não é "
                     f"confiável para navegadores.",
            details=details)

    if not info.get("verified"):
        return CheckResult(
            name=NAME, status=Status.FAIL, severity=Severity.MEDIA,
            evidence=f"Cadeia de certificados não validou para {host}: "
                     f"{info.get('verify_error')}. Verifique intermediários ausentes.",
            details=details)

    san_note = f" SAN: {', '.join(cert['san'][:4])}." if cert.get("san") else ""
    if not_after is not None:
        days = (not_after - datetime.now(timezone.utc)).days
        details["days_left"] = days
        base = (f"Certificado emitido por {issuer}, válido até {not_after.date()} "
                f"({days} dias).{san_note} Cadeia completa.")
        if days <= EXPIRY_WARN_DAYS:
            return CheckResult(name=NAME, status=Status.PASS, severity=Severity.MEDIA,
                               evidence=base + " ATENÇÃO: expira em breve — renovar.",
                               details=details)
        return CheckResult(name=NAME, status=Status.PASS, severity=Severity.MEDIA,
                           evidence=base, details=details)

    return CheckResult(name=NAME, status=Status.PASS, severity=Severity.MEDIA,
                       evidence=f"Certificado emitido por {issuer}, cadeia válida.{san_note}",
                       details=details)
