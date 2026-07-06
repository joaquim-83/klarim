"""Check 03 — Certificado SSL válido (Severidade: CRÍTICA).

Spec: verifica expiração, CA confiável e match de domínio.

Strategy: perform a real TLS handshake with a *verifying* default context
(``ssl.create_default_context`` checks the chain against the system trust store
and matches the hostname). If the handshake verifies, the certificate is
trusted, hostname-matched and not expired. We then re-read the certificate
(without verification) via ``cryptography`` to report the exact validity window
and warn when expiry is near.
"""

from __future__ import annotations

import asyncio
import socket
import ssl
from datetime import datetime, timezone

from cryptography import x509
from cryptography.hazmat.backends import default_backend

from .base import (
    CheckResult,
    Status,
    Severity,
    REQUEST_TIMEOUT,
    host_port,
    domain_of,
    _rate_limiter,
)

ORDER = 3
CHECK_ID = "check_03_ssl"
NAME = "Certificado SSL válido"

# Warn (but still PASS) when the certificate expires within this many days.
EXPIRY_WARN_DAYS = 15


async def check(url: str) -> CheckResult:
    host, port = host_port(url, default=443)
    if not host:
        return CheckResult(
            name=NAME,
            status=Status.INCONCLUSO,
            severity=Severity.CRITICA,
            evidence="Não foi possível extrair o host da URL.",
        )

    domain = domain_of(url)
    await _rate_limiter.acquire(domain)
    try:
        result = await asyncio.to_thread(_inspect_cert, host, port)
    finally:
        _rate_limiter.release(domain)
    return result


def _inspect_cert(host: str, port: int) -> CheckResult:
    # 1) Verifying handshake: chain trust + hostname match + not-expired.
    verify_error = None
    ctx = ssl.create_default_context()
    try:
        with socket.create_connection((host, port), timeout=REQUEST_TIMEOUT) as sock:
            with ctx.wrap_socket(sock, server_hostname=host):
                pass  # handshake succeeded -> cert is valid & trusted
    except ssl.SSLCertVerificationError as exc:
        verify_error = f"{exc.verify_message or exc}"
    except (ssl.SSLError, socket.timeout, socket.gaierror, OSError) as exc:
        return CheckResult(
            name=NAME,
            status=Status.INCONCLUSO,
            severity=Severity.CRITICA,
            evidence=f"Não foi possível concluir o handshake TLS: {exc!r}",
        )

    # 2) Re-read the leaf certificate (no verification) for exact dates/subject.
    not_before = not_after = subject_cn = None
    try:
        raw = _fetch_cert_der(host, port)
        cert = x509.load_der_x509_certificate(raw, default_backend())
        not_before = cert.not_valid_before_utc
        not_after = cert.not_valid_after_utc
        try:
            subject_cn = cert.subject.get_attributes_for_oid(
                x509.NameOID.COMMON_NAME
            )[0].value
        except (IndexError, x509.ExtensionNotFound):
            subject_cn = None
    except Exception:  # noqa: BLE001 - best-effort enrichment only
        pass

    details = {
        "subject_cn": subject_cn,
        "not_before": not_before.isoformat() if not_before else None,
        "not_after": not_after.isoformat() if not_after else None,
    }

    if verify_error:
        return CheckResult(
            name=NAME,
            status=Status.FAIL,
            severity=Severity.CRITICA,
            evidence=f"Certificado inválido para {host}: {verify_error}.",
            details=details,
        )

    # Handshake verified. Add an expiry-window note.
    now = datetime.now(timezone.utc)
    if not_after is not None:
        days_left = (not_after - now).days
        details["days_left"] = days_left
        if days_left < 0:
            # Should have been caught by verification, but be explicit.
            return CheckResult(
                name=NAME,
                status=Status.FAIL,
                severity=Severity.CRITICA,
                evidence=f"Certificado expirado em {not_after.date()}.",
                details=details,
            )
        note = (
            f"Certificado válido e confiável (CN={subject_cn or 'n/d'}), "
            f"expira em {not_after.date()} ({days_left} dias)."
        )
        if days_left <= EXPIRY_WARN_DAYS:
            note += " ATENÇÃO: expira em breve."
        return CheckResult(
            name=NAME,
            status=Status.PASS,
            severity=Severity.CRITICA,
            evidence=note,
            details=details,
        )

    return CheckResult(
        name=NAME,
        status=Status.PASS,
        severity=Severity.CRITICA,
        evidence=f"Certificado válido e confiável para {host}.",
        details=details,
    )


def _fetch_cert_der(host: str, port: int) -> bytes:
    """Return the leaf certificate in DER form without verifying the chain."""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    with socket.create_connection((host, port), timeout=REQUEST_TIMEOUT) as sock:
        with ctx.wrap_socket(sock, server_hostname=host) as ssock:
            return ssock.getpeercert(binary_form=True)
