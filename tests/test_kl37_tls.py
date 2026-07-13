"""Testes KL-37 — TLS profundo (cipher, cadeia, OCSP, chave). Offline (mock get_tls_info)."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from scanner import tls_analyzer as ta
from scanner.tls_analyzer import weak_cipher_reason, has_forward_secrecy, classify_key
from scanner.checks import (
    check_41_cipher_suites as cip, check_42_cert_chain as chain,
    check_43_ocsp_stapling as ocsp, check_44_key_strength as key,
)
from scanner.checks.base import Status, Severity
from scanner.checks.classifications import classify

URL = "https://www.example.com.br"


def _run(coro):
    return asyncio.run(coro)


def _cert(**kw):
    base = {
        "subject_cn": "example.com.br", "issuer_cn": "R3",
        "not_after": datetime.now(timezone.utc) + timedelta(days=90),
        "san": ["example.com.br", "www.example.com.br"],
        "self_signed": False, "ocsp_uri": "http://r3.o.lencr.org",
        "key": {"type": "RSA", "bits": 2048},
    }
    base.update(kw)
    return base


def _info(**kw):
    base = {
        "ok": True, "verified": True, "verify_error": None,
        "cipher_name": "TLS_AES_256_GCM_SHA384", "protocol": "TLSv1.3", "bits": 256,
        "forward_secrecy": True, "weak_cipher": None, "cert": _cert(),
    }
    base.update(kw)
    return base


def _patch(monkeypatch, mod, info):
    async def _f(host, port=443):
        return info
    monkeypatch.setattr(mod, "get_tls_info", _f)


# --- 1-4: cipher suites ---------------------------------------------------- #

def test_cipher_tls13_pass(monkeypatch):
    _patch(monkeypatch, cip, _info())
    assert _run(cip.check(URL)).status == Status.PASS


def test_cipher_tls12_strong_pass(monkeypatch):
    _patch(monkeypatch, cip, _info(cipher_name="ECDHE-RSA-AES256-GCM-SHA384",
                                   protocol="TLSv1.2", bits=256, forward_secrecy=True))
    assert _run(cip.check(URL)).status == Status.PASS


def test_cipher_rc4_fail_alta(monkeypatch):
    _patch(monkeypatch, cip, _info(cipher_name="TLS_RSA_WITH_RC4_128_SHA",
                                   protocol="TLSv1.2", bits=128, forward_secrecy=False,
                                   weak_cipher="RC4 quebrado desde 2015 (RFC 7465)"))
    r = _run(cip.check(URL))
    assert r.status == Status.FAIL and r.severity == Severity.ALTA


def test_cipher_no_forward_secrecy_fail_media(monkeypatch):
    _patch(monkeypatch, cip, _info(cipher_name="AES128-SHA", protocol="TLSv1.2",
                                   bits=128, forward_secrecy=False, weak_cipher=None))
    r = _run(cip.check(URL))
    assert r.status == Status.FAIL and r.severity == Severity.MEDIA


# --- 5-7: cert chain ------------------------------------------------------- #

def test_cert_chain_valid_pass(monkeypatch):
    _patch(monkeypatch, chain, _info())
    r = _run(chain.check(URL))
    assert r.status == Status.PASS and "R3" in r.evidence


def test_cert_chain_expiring_note(monkeypatch):
    _patch(monkeypatch, chain, _info(cert=_cert(
        not_after=datetime.now(timezone.utc) + timedelta(days=7))))
    r = _run(chain.check(URL))
    assert r.status == Status.PASS and "expira em breve" in r.evidence


def test_cert_chain_self_signed_fail(monkeypatch):
    _patch(monkeypatch, chain, _info(verified=False, cert=_cert(self_signed=True)))
    assert _run(chain.check(URL)).status == Status.FAIL


def test_cert_chain_not_verified_fail(monkeypatch):
    _patch(monkeypatch, chain, _info(verified=False, verify_error="unable to get local issuer",
                                     cert=_cert(self_signed=False)))
    assert _run(chain.check(URL)).status == Status.FAIL


# --- 8-9: OCSP ------------------------------------------------------------- #

def test_ocsp_uri_present_pass(monkeypatch):
    _patch(monkeypatch, ocsp, _info())
    assert _run(ocsp.check(URL)).status == Status.PASS


def test_ocsp_no_uri_inconcluso(monkeypatch):
    # KL-51 f3 fix: sem OCSP URI é o novo normal (Let's Encrypt descontinuou o OCSP) —
    # INCONCLUSO (neutro), não FAIL.
    _patch(monkeypatch, ocsp, _info(cert=_cert(ocsp_uri=None)))
    r = _run(ocsp.check(URL))
    assert r.status == Status.INCONCLUSO and r.severity == Severity.BAIXA


# --- 10-12: key strength --------------------------------------------------- #

def test_key_rsa2048_pass(monkeypatch):
    _patch(monkeypatch, key, _info(cert=_cert(key={"type": "RSA", "bits": 2048})))
    assert _run(key.check(URL)).status == Status.PASS


def test_key_rsa1024_fail_alta(monkeypatch):
    _patch(monkeypatch, key, _info(cert=_cert(key={"type": "RSA", "bits": 1024})))
    r = _run(key.check(URL))
    assert r.status == Status.FAIL and r.severity == Severity.ALTA


def test_key_ecdsa_p256_pass(monkeypatch):
    _patch(monkeypatch, key, _info(cert=_cert(key={"type": "ECDSA", "curve": "P-256", "bits": 256})))
    r = _run(key.check(URL))
    assert r.status == Status.PASS and "forte" in r.evidence


# --- 13: conexão falha -> INCONCLUSO para todos ---------------------------- #

def test_all_inconclusive_on_connect_fail(monkeypatch):
    fail = {"ok": False, "error": "timeout"}
    for mod in (cip, chain, ocsp, key):
        _patch(monkeypatch, mod, fail)
        assert _run(mod.check(URL)).status == Status.INCONCLUSO


# --- 14: classificações ---------------------------------------------------- #

def test_tls_checks_classified():
    assert classify("check_41_cipher_suites") == ("A02:2025 Cryptographic Failures", "CWE-327", "Art. 46")
    assert classify("check_42_cert_chain") == ("A02:2025 Cryptographic Failures", "CWE-295", "Art. 46")
    assert classify("check_43_ocsp_stapling") == ("A02:2025 Cryptographic Failures", "CWE-299", "Art. 46")
    assert classify("check_44_key_strength") == ("A02:2025 Cryptographic Failures", "CWE-326", "Art. 46")


# --- 15: helpers puros ----------------------------------------------------- #

def test_pure_helpers():
    assert weak_cipher_reason("ECDHE-RSA-AES256-GCM-SHA384") is None
    assert "RC4" in weak_cipher_reason("TLS_RSA_WITH_RC4_128_SHA")
    assert "3DES" in weak_cipher_reason("ECDHE-RSA-DES-CBC3-SHA")
    assert has_forward_secrecy("TLS_AES_256_GCM_SHA384", "TLSv1.3") is True
    assert has_forward_secrecy("ECDHE-RSA-AES128-GCM-SHA256", "TLSv1.2") is True
    assert has_forward_secrecy("AES128-SHA", "TLSv1.2") is False
    assert classify_key({"type": "RSA", "bits": 4096}) == ("excelente", None)
    assert classify_key({"type": "RSA", "bits": 2048}) == ("aceitável", None)
    assert classify_key({"type": "RSA", "bits": 1024})[1] == Severity.ALTA
    assert classify_key({"type": "RSA", "bits": 512})[1] == Severity.CRITICA
    assert classify_key({"type": "ECDSA", "bits": 256}) == ("forte", None)
