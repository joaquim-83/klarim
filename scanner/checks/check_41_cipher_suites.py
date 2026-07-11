"""Check 41 — Cipher suites (Severidade: ALTA, KL-37).

Avalia o cipher **negociado** no handshake TLS (o que o servidor prefere). Cipher
fraco (RC4/DES/3DES/NULL/EXPORT/anon), protocolo obsoleto (TLS 1.0/1.1) ou ausência
de forward secrecy (no TLS 1.2) reprovam. TLS 1.3 só tem ciphers fortes → PASS.
Compartilha o handshake com os checks 42–44 via ``tls_analyzer``. 100% passivo.
"""

from __future__ import annotations

from .base import CheckResult, Status, Severity, host_port
from ..tls_analyzer import get_tls_info, WEAK_PROTOCOLS

ORDER = 41
CHECK_ID = "check_41_cipher_suites"
NAME = "Cipher suites"


async def check(url: str) -> CheckResult:
    host, port = host_port(url, default=443)
    info = await get_tls_info(host, port)
    if not info.get("ok"):
        return CheckResult(name=NAME, status=Status.INCONCLUSO, severity=Severity.ALTA,
                           evidence=f"Não foi possível concluir o handshake TLS: {info.get('error')}")

    name = info.get("cipher_name")
    protocol = info.get("protocol")
    bits = info.get("bits")
    details = {"cipher": name, "protocol": protocol, "bits": bits,
               "forward_secrecy": info.get("forward_secrecy")}

    if info.get("weak_cipher"):
        return CheckResult(
            name=NAME, status=Status.FAIL, severity=Severity.ALTA,
            evidence=f"Cipher negociado {name} ({bits} bits, {protocol}) — "
                     f"{info['weak_cipher']}. Recomendação: desabilitar e usar ECDHE+AES-GCM.",
            details=details)

    if protocol in WEAK_PROTOCOLS:
        return CheckResult(
            name=NAME, status=Status.FAIL, severity=Severity.ALTA,
            evidence=f"Protocolo obsoleto negociado: {protocol} — vulnerável (BEAST/POODLE). "
                     f"Aceitar apenas TLS 1.2+.",
            details=details)

    if not info.get("forward_secrecy"):
        return CheckResult(
            name=NAME, status=Status.FAIL, severity=Severity.MEDIA,
            evidence=f"Cipher {name} ({protocol}) sem forward secrecy (RSA key exchange) — "
                     f"uma chave comprometida expõe o tráfego passado. Configurar ECDHE.",
            details=details)

    return CheckResult(
        name=NAME, status=Status.PASS, severity=Severity.ALTA,
        evidence=f"Cipher forte negociado: {name} ({bits} bits, {protocol}), com forward secrecy.",
        details=details)
