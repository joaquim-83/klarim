"""Check 44 — Força da chave criptográfica (Severidade: ALTA/CRÍTICA, KL-37).

Avalia tipo e tamanho da chave pública do certificado. RSA 2048+ ou ECDSA P-256+ →
PASS; RSA 1024 → FAIL (quebrável desde 2013); RSA <1024 → FAIL crítico. Compartilha o
handshake com 41/42/43 via ``tls_analyzer``. 100% passivo.
"""

from __future__ import annotations

from .base import CheckResult, Status, Severity, host_port
from ..tls_analyzer import get_tls_info, classify_key

ORDER = 44
CHECK_ID = "check_44_key_strength"
NAME = "Força da chave criptográfica"


async def check(url: str) -> CheckResult:
    host, port = host_port(url, default=443)
    info = await get_tls_info(host, port)
    if not info.get("ok") or not info.get("cert"):
        return CheckResult(name=NAME, status=Status.INCONCLUSO, severity=Severity.ALTA,
                           evidence=f"Não foi possível inspecionar o certificado: "
                                    f"{info.get('error') or info.get('cert_error')}")

    key = info["cert"].get("key") or {}
    ktype = key.get("type")
    bits = key.get("bits")
    if ktype in (None, "unknown"):
        return CheckResult(name=NAME, status=Status.INCONCLUSO, severity=Severity.ALTA,
                           evidence="Tipo de chave pública não reconhecido.",
                           details={"key": key})

    rating, severity = classify_key(key)
    label = f"{ktype} {key.get('curve') or str(bits) + ' bits'}"
    details = {"key_type": ktype, "bits": bits, "curve": key.get("curve"), "rating": rating}

    if severity is None:
        return CheckResult(
            name=NAME, status=Status.PASS, severity=Severity.ALTA,
            evidence=f"Chave pública {label} — força: {rating}.",
            details=details)
    return CheckResult(
        name=NAME, status=Status.FAIL, severity=severity,
        evidence=f"Chave pública {label} — força: {rating}. RSA 1024 é quebrável por "
                 f"fatoração (NIST deprecou em 2013). Gerar RSA 2048+ ou ECDSA P-256.",
        details=details)
