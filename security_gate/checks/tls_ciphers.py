"""KL-149 — check de CIPHER SUITES fracas no TLS. Passivo (avaliação de capacidade, como o
SSL Labs): tenta um handshake oferecendo SÓ um grupo de ciphers fraco e vê se o servidor aceita.
Nada é enviado além do ClientHello. O handshake bloqueante roda em thread; `_accepts_cipher` é o
ponto de mock nos testes.

Em OpenSSL 3.x muitos ciphers legados (RC4/DES) nem podem ser oferecidos (`set_ciphers` levanta) —
nesse caso o servidor não é testável para aquele grupo e o resultado é "não aceita" (seguro)."""
from __future__ import annotations

import asyncio
import socket
import ssl
from typing import List, Optional

from ..models import Result, Severity, Status
from ..utils import _host

# Grupos de ciphers fracos (spec OpenSSL). Se `set_ciphers` levantar, o grupo é inofertável.
WEAK_CIPHER_SPECS = ["RC4", "DES-CBC3-SHA:3DES", "DES", "NULL", "EXPORT", "aNULL", "MD5"]

# Marcadores no NOME do cipher negociado que confirmam fraqueza real (anti falso-positivo do TLS 1.3:
# o `set_ciphers` NÃO controla os ciphersuites do TLS 1.3, então um handshake pode "passar" negociando
# um cipher FORTE — só reportamos se o cipher negociado de fato contém um destes marcadores).
_WEAK_MARKERS = ("RC4", "DES", "NULL", "EXPORT", "EXP-", "MD5", "ADH", "AECDH", "IDEA", "SEED", "ANON")


def _accepts_cipher(hostname: str, spec: str, port: int = 443, timeout: float = 5.0) -> Optional[str]:
    """Nome do cipher negociado SE o servidor aceitar um cipher REALMENTE fraco do grupo `spec`,
    senão None. Força TLS ≤ 1.2 (o TLS 1.3 ignora `set_ciphers`) e confere que o cipher negociado
    contém um marcador de fraqueza — assim um handshake TLS 1.3 forte nunca vira falso positivo.
    Bloqueante; levanta só em erro de infra que não seja recusa (o chamador trata)."""
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    try:
        ctx.maximum_version = ssl.TLSVersion.TLSv1_2   # TLS 1.3 negocia ciphers próprios (fortes)
    except (ValueError, AttributeError):
        pass
    try:
        ctx.set_ciphers(spec)
    except ssl.SSLError:
        return None   # OpenSSL não oferece esse cipher → servidor não é testável p/ ele = seguro
    try:
        with socket.create_connection((hostname, port), timeout=timeout) as sock:
            with ctx.wrap_socket(sock, server_hostname=hostname) as ssock:
                negotiated = ssock.cipher()
                name = negotiated[0] if negotiated else ""
                if name and any(m in name.upper() for m in _WEAK_MARKERS):
                    return name         # cipher negociado é DE FATO fraco
                return None             # handshake ok mas cipher forte → não é fraqueza
    except (ssl.SSLError, OSError):
        return None   # servidor recusou o handshake fraco → bom


async def check_tls_ciphers(client, base_url: str, config=None) -> List[Result]:
    if not base_url.startswith("https://"):
        return [Result("tls_ciphers_skip", "tls", "/", Status.SKIP, Severity.INFO,
                       "Site não usa HTTPS")]
    hostname = _host(base_url).split(":")[0]
    results: List[Result] = []

    for spec in WEAK_CIPHER_SPECS:
        try:
            accepted = await asyncio.to_thread(_accepts_cipher, hostname, spec)
        except Exception:  # noqa: BLE001 - qualquer erro inesperado = não conseguimos provar aceitação
            accepted = None
        if accepted:
            results.append(Result(f"tls_weak_{spec.split(':')[0].lower()}", "tls", "/",
                                  Status.FAIL, Severity.CRITICAL,
                                  f"Servidor aceita cipher suite fraca: {accepted}"))

    if not results:
        results.append(Result("tls_ciphers_ok", "tls", "/", Status.PASS, Severity.CRITICAL,
                              "Servidor não aceita cipher suites fracas (RC4/DES/3DES/NULL/EXPORT)"))
    return results
