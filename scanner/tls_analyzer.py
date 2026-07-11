"""Análise TLS profunda compartilhada pelos checks 41–44 (KL-37).

Faz **um único** handshake TLS por (host, porta) e extrai tudo de uma vez — cipher
negociado, protocolo, certificado (via ``cryptography``), SAN, OCSP URI e força da
chave. O resultado é cacheado por ~2 min para que os 4 checks **compartilhem o mesmo
handshake** (o runner os roda em sequência), em vez de reconectar 4 vezes.

100% passivo: só um handshake TLS público (o mesmo que qualquer navegador faz). Não
enumera todas as cipher suites (isso exigiria N conexões, mais intrusivo) — avalia a
**negociada** (a que o servidor prefere), abordagem pragmática documentada no card.
"""

from __future__ import annotations

import asyncio
import socket
import ssl
import time
from typing import Dict, Optional, Tuple

from cryptography import x509
from cryptography.hazmat.primitives.asymmetric import dsa, ec, ed25519, ed448, rsa
from cryptography.x509.oid import AuthorityInformationAccessOID, ExtensionOID

from .checks.base import REQUEST_TIMEOUT, domain_of, _rate_limiter
from .checks.base import Severity


# --------------------------------------------------------------------------- #
# Classificação de cipher / chave (puro, testável)
# --------------------------------------------------------------------------- #

# Tokens de cipher fracos (avaliados sobre o nome OpenSSL do cipher negociado).
# Ordem importa: 3DES antes de DES.
_WEAK_CIPHERS: Tuple[Tuple[str, str], ...] = (
    ("RC4", "RC4 quebrado desde 2015 (RFC 7465)"),
    ("3DES", "3DES em fim de vida — vulnerável a Sweet32"),
    ("DES-CBC3", "3DES em fim de vida — vulnerável a Sweet32"),
    ("NULL", "sem criptografia — dados em texto claro"),
    ("EXP", "export-grade — chave deliberadamente curta"),
    ("MD5", "MD5 para MAC — colisões conhecidas"),
    ("ADH", "anônimo — sem autenticação do servidor"),
    ("AECDH", "anônimo — sem autenticação do servidor"),
)

WEAK_PROTOCOLS = {"SSLv2", "SSLv3", "TLSv1", "TLSv1.1"}


def weak_cipher_reason(name: Optional[str]) -> Optional[str]:
    """Motivo se o cipher (nome OpenSSL) for fraco, senão ``None``."""
    if not name:
        return None
    u = name.upper()
    for token, reason in _WEAK_CIPHERS:
        if token in u:
            return reason
    # DES simples (não-3DES, não-AES): "DES-CBC-SHA".
    if "DES" in u and "AES" not in u and "3DES" not in u and "DES-CBC3" not in u:
        return "DES quebrado — chave de 56 bits"
    return None


def has_forward_secrecy(name: Optional[str], protocol: Optional[str]) -> bool:
    """TLS 1.3 sempre tem FS; no TLS 1.2 exige ECDHE/DHE no cipher."""
    if protocol == "TLSv1.3":
        return True
    u = (name or "").upper()
    return "DHE" in u or "EDH" in u   # "DHE" cobre ECDHE e DHE


# (tipo, bits) -> (rótulo, severidade se problema | None se ok)
def classify_key(key: Dict) -> Tuple[str, Optional[str]]:
    t = (key or {}).get("type")
    bits = (key or {}).get("bits") or 0
    if t == "RSA":
        if bits >= 4096:
            return "excelente", None
        if bits >= 2048:
            return "aceitável", None
        if bits >= 1024:
            return "fraco", Severity.ALTA
        return "quebrado", Severity.CRITICA
    if t in ("ECDSA", "EC"):
        if bits >= 384:
            return "excelente", None
        if bits >= 256:
            return "forte", None
        return "fraco", Severity.MEDIA
    if t in ("Ed25519", "Ed448"):
        return "excelente", None
    if t == "DSA":
        return "obsoleto", Severity.ALTA
    return "desconhecido", None


_CURVE_LABEL = {"secp256r1": "P-256", "secp384r1": "P-384", "secp521r1": "P-521"}


def _key_info(cert: x509.Certificate) -> Dict:
    pk = cert.public_key()
    if isinstance(pk, rsa.RSAPublicKey):
        return {"type": "RSA", "bits": pk.key_size}
    if isinstance(pk, ec.EllipticCurvePublicKey):
        return {"type": "ECDSA", "curve": _CURVE_LABEL.get(pk.curve.name, pk.curve.name),
                "bits": pk.curve.key_size}
    if isinstance(pk, ed25519.Ed25519PublicKey):
        return {"type": "Ed25519", "bits": 256}
    if isinstance(pk, ed448.Ed448PublicKey):
        return {"type": "Ed448", "bits": 448}
    if isinstance(pk, dsa.DSAPublicKey):
        return {"type": "DSA", "bits": pk.key_size}
    return {"type": "unknown", "bits": 0}


def _ocsp_uri(cert: x509.Certificate) -> Optional[str]:
    try:
        aia = cert.extensions.get_extension_for_oid(
            ExtensionOID.AUTHORITY_INFORMATION_ACCESS).value
    except x509.ExtensionNotFound:
        return None
    for desc in aia:
        if desc.access_method == AuthorityInformationAccessOID.OCSP:
            return str(desc.access_location.value)
    return None


def _san(cert: x509.Certificate) -> list:
    try:
        ext = cert.extensions.get_extension_for_oid(ExtensionOID.SUBJECT_ALTERNATIVE_NAME)
        return list(ext.value.get_values_for_type(x509.DNSName))
    except x509.ExtensionNotFound:
        return []


def _issuer_cn(cert: x509.Certificate) -> Optional[str]:
    try:
        return cert.issuer.get_attributes_for_oid(x509.NameOID.COMMON_NAME)[0].value
    except (IndexError, x509.ExtensionNotFound):
        return None


def _subject_cn(cert: x509.Certificate) -> Optional[str]:
    try:
        return cert.subject.get_attributes_for_oid(x509.NameOID.COMMON_NAME)[0].value
    except (IndexError, x509.ExtensionNotFound):
        return None


def _cert_info(der: bytes) -> Dict:
    cert = x509.load_der_x509_certificate(der)
    try:
        not_after = cert.not_valid_after_utc
        not_before = cert.not_valid_before_utc
    except AttributeError:  # cryptography < 42 (naive)
        not_after = cert.not_valid_after
        not_before = cert.not_valid_before
    return {
        "subject_cn": _subject_cn(cert),
        "issuer_cn": _issuer_cn(cert),
        "not_before": not_before,
        "not_after": not_after,
        "san": _san(cert),
        "self_signed": cert.subject == cert.issuer,
        "ocsp_uri": _ocsp_uri(cert),
        "key": _key_info(cert),
    }


# --------------------------------------------------------------------------- #
# Handshake + cache
# --------------------------------------------------------------------------- #

def _handshake(host: str, port: int, verify: bool) -> Tuple[tuple, str, bytes]:
    """Retorna (cipher_tuple, protocol, der_leaf). Levanta em falha de conexão."""
    if verify:
        ctx = ssl.create_default_context()
    else:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    with socket.create_connection((host, port), timeout=REQUEST_TIMEOUT) as sock:
        with ctx.wrap_socket(sock, server_hostname=host) as ssock:
            return ssock.cipher(), ssock.version(), ssock.getpeercert(binary_form=True)


def _blocking_tls_info(host: str, port: int) -> Dict:
    verified = True
    verify_error = None
    try:
        cipher, protocol, der = _handshake(host, port, verify=True)
    except ssl.SSLCertVerificationError as exc:
        verified = False
        verify_error = f"{getattr(exc, 'verify_message', None) or exc}"
        try:
            cipher, protocol, der = _handshake(host, port, verify=False)
        except (ssl.SSLError, OSError) as exc2:
            return {"ok": False, "error": repr(exc2)}
    except (ssl.SSLError, socket.timeout, socket.gaierror, OSError) as exc:
        return {"ok": False, "error": repr(exc)}

    info: Dict = {
        "ok": True, "error": None,
        "verified": verified, "verify_error": verify_error,
        "cipher_name": cipher[0] if cipher else None,
        "protocol": protocol,
        "bits": cipher[2] if cipher else None,
    }
    info["forward_secrecy"] = has_forward_secrecy(info["cipher_name"], protocol)
    info["weak_cipher"] = weak_cipher_reason(info["cipher_name"])
    try:
        info["cert"] = _cert_info(der)
    except Exception as exc:  # noqa: BLE001 - DER ilegível: parte cert fica vazia
        info["cert"] = None
        info["cert_error"] = repr(exc)
    return info


_CACHE: Dict[Tuple[str, int], Tuple[float, Dict]] = {}
_CACHE_TTL = 120.0
_lock = asyncio.Lock()


async def get_tls_info(host: str, port: int = 443) -> Dict:
    """Info TLS de (host, porta), cacheada — os 4 checks compartilham o handshake."""
    if not host:
        return {"ok": False, "error": "host vazio"}
    key = (host, port)
    now = time.monotonic()
    async with _lock:
        ent = _CACHE.get(key)
        if ent and ent[0] > now:
            return ent[1]
    domain = domain_of(f"https://{host}")
    await _rate_limiter.acquire(domain)
    try:
        info = await asyncio.to_thread(_blocking_tls_info, host, port)
    finally:
        _rate_limiter.release(domain)
    async with _lock:
        _CACHE[key] = (time.monotonic() + _CACHE_TTL, info)
    return info
