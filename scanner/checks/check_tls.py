"""Check 04 — TLS 1.2+ only (Severidade: ALTA).

Spec: tenta handshake TLS 1.0 e TLS 1.1. Se o servidor aceitar qualquer um
deles, FAIL (protocolos legados/inseguros habilitados).

Note on environment: modern OpenSSL builds (3.x) frequently disable TLS 1.0/1.1
at the library level, so *forcing* those versions locally may raise before a
byte reaches the server. We distinguish three outcomes per legacy version:

  * handshake completed        -> server ACCEPTS legacy TLS  -> FAIL
  * server rejected handshake  -> server refuses legacy TLS  -> good
  * local OpenSSL cannot offer  -> INCONCLUSO for that version

The overall result is FAIL if any legacy version was accepted; PASS if both
were positively refused by the server; INCONCLUSO if we could not test either.
"""

from __future__ import annotations

import asyncio
import socket
import ssl

from .base import (
    CheckResult,
    Status,
    Severity,
    REQUEST_TIMEOUT,
    host_port,
    domain_of,
    _rate_limiter,
)

ORDER = 4
CHECK_ID = "check_04_tls"
NAME = "TLS 1.2+ only"

_LEGACY_VERSIONS = [
    ("TLS 1.0", ssl.TLSVersion.TLSv1),
    ("TLS 1.1", ssl.TLSVersion.TLSv1_1),
]


async def check(url: str) -> CheckResult:
    host, port = host_port(url, default=443)
    if not host:
        return CheckResult(
            name=NAME,
            status=Status.INCONCLUSO,
            severity=Severity.ALTA,
            evidence="Não foi possível extrair o host da URL.",
        )

    domain = domain_of(url)
    outcomes = {}
    for label, version in _LEGACY_VERSIONS:
        await _rate_limiter.acquire(domain)
        try:
            outcomes[label] = await asyncio.to_thread(
                _try_legacy_handshake, host, port, version
            )
        finally:
            _rate_limiter.release(domain)

    accepted = [lbl for lbl, state in outcomes.items() if state == "accepted"]
    refused = [lbl for lbl, state in outcomes.items() if state == "refused"]
    untestable = [lbl for lbl, state in outcomes.items() if state == "untestable"]

    if accepted:
        return CheckResult(
            name=NAME,
            status=Status.FAIL,
            severity=Severity.ALTA,
            evidence=(
                f"Servidor aceitou protocolo(s) legado(s): {', '.join(accepted)}. "
                "TLS 1.0/1.1 são considerados inseguros."
            ),
            details=outcomes,
        )

    if refused and not accepted:
        note = f"Servidor recusou {', '.join(refused)}."
        if untestable:
            note += (
                f" ({', '.join(untestable)} não pôde(ram) ser testado(s) pelo "
                "OpenSSL local, mas nenhum legado foi aceito.)"
            )
        return CheckResult(
            name=NAME,
            status=Status.PASS,
            severity=Severity.ALTA,
            evidence=note,
            details=outcomes,
        )

    return CheckResult(
        name=NAME,
        status=Status.INCONCLUSO,
        severity=Severity.ALTA,
        evidence=(
            "OpenSSL local não permite negociar TLS 1.0/1.1; não foi possível "
            "testar a aceitação de protocolos legados neste ambiente."
        ),
        details=outcomes,
    )


def _try_legacy_handshake(host: str, port: int, version: ssl.TLSVersion) -> str:
    """Return 'accepted' | 'refused' | 'untestable' for a forced TLS version."""
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    # We only test protocol negotiation, so do not require a valid cert here.
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    try:
        ctx.minimum_version = version
        ctx.maximum_version = version
    except (ValueError, OSError):
        # Library refuses to even configure this legacy version.
        return "untestable"

    try:
        with socket.create_connection((host, port), timeout=REQUEST_TIMEOUT) as sock:
            with ctx.wrap_socket(sock, server_hostname=host):
                return "accepted"
    except ssl.SSLError as exc:
        msg = str(exc).lower()
        # OpenSSL refusing to offer the legacy version locally, not the server.
        if "unsupported protocol" in msg or "no protocols available" in msg:
            return "untestable"
        return "refused"
    except (socket.timeout, socket.gaierror, ConnectionError, OSError):
        return "refused"
