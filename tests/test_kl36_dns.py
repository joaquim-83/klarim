"""Testes KL-36 — DNS security expandido (DNSSEC, CAA, MTA-STS, BIMI). Offline."""

from __future__ import annotations

import asyncio

import httpx

from scanner.checks import (
    dns_util, check_37_dnssec as dnssec, check_38_caa as caa,
    check_39_mta_sts as mta, check_40_bimi as bimi,
)
from scanner.checks.base import Status, Severity
from scanner.checks.classifications import classify

URL = "https://www.example.com.br"


def _run(coro):
    return asyncio.run(coro)


def _const(value):
    def _f(name, timeout=5.0):
        return value
    return _f


def _txt_map(mapping, default=None):
    """resolve_txt fake que devolve valores por fragmento do nome consultado."""
    def _f(name, timeout=5.0):
        for frag, val in mapping.items():
            if frag in name:
                return val
        return [] if default is None else default
    return _f


def _fetch_policy(text, status=200):
    async def _f(url, method="GET", **kw):
        return httpx.Response(status, text=text, request=httpx.Request("GET", url))
    return _f


# --- 1-2: DNSSEC ----------------------------------------------------------- #

def test_dnssec_present_pass(monkeypatch):
    monkeypatch.setattr(dns_util, "resolve_ds", _const(["12345 8 2 ABCDEF"]))
    r = _run(dnssec.check(URL))
    assert r.status == Status.PASS


def test_dnssec_absent_fail(monkeypatch):
    monkeypatch.setattr(dns_util, "resolve_ds", _const([]))
    r = _run(dnssec.check(URL))
    assert r.status == Status.FAIL and r.severity == Severity.MEDIA


def test_dnssec_dns_error_inconclusive(monkeypatch):
    monkeypatch.setattr(dns_util, "resolve_ds", _const(None))
    assert _run(dnssec.check(URL)).status == Status.INCONCLUSO


# --- 3-4: CAA -------------------------------------------------------------- #

def test_caa_present_pass_lists_ca(monkeypatch):
    monkeypatch.setattr(dns_util, "resolve_caa",
                        _const([{"flags": 0, "tag": "issue", "value": "letsencrypt.org"}]))
    r = _run(caa.check(URL))
    assert r.status == Status.PASS and "letsencrypt.org" in r.evidence


def test_caa_absent_fail(monkeypatch):
    monkeypatch.setattr(dns_util, "resolve_caa", _const([]))
    r = _run(caa.check(URL))
    assert r.status == Status.FAIL and r.severity == Severity.MEDIA


# --- 5-8: MTA-STS ---------------------------------------------------------- #

def test_mta_sts_enforce_pass(monkeypatch):
    monkeypatch.setattr(dns_util, "resolve_txt", _const(["v=STSv1; id=20260101"]))
    monkeypatch.setattr(mta, "fetch", _fetch_policy("version: STSv1\nmode: enforce\nmx: m\n"))
    assert _run(mta.check(URL)).status == Status.PASS


def test_mta_sts_testing_pass_with_note(monkeypatch):
    monkeypatch.setattr(dns_util, "resolve_txt", _const(["v=STSv1; id=1"]))
    monkeypatch.setattr(mta, "fetch", _fetch_policy("version: STSv1\nmode: testing\n"))
    r = _run(mta.check(URL))
    assert r.status == Status.PASS and "testing" in r.evidence


def test_mta_sts_dns_without_policy_fail(monkeypatch):
    monkeypatch.setattr(dns_util, "resolve_txt", _const(["v=STSv1; id=1"]))
    monkeypatch.setattr(mta, "fetch", _fetch_policy("not found", status=404))
    assert _run(mta.check(URL)).status == Status.FAIL


def test_mta_sts_absent_fail_baixa(monkeypatch):
    monkeypatch.setattr(dns_util, "resolve_txt", _const([]))
    r = _run(mta.check(URL))
    assert r.status == Status.FAIL and r.severity == Severity.BAIXA


# --- 9-11: BIMI ------------------------------------------------------------ #

def test_bimi_present_pass(monkeypatch):
    monkeypatch.setattr(dns_util, "resolve_txt", _txt_map({
        "_bimi": ["v=BIMI1; l=https://x.com.br/logo.svg"],
        "_dmarc": ["v=DMARC1; p=reject"],
    }))
    r = _run(bimi.check(URL))
    assert r.status == Status.PASS and "logo.svg" in r.evidence and "Atenção" not in r.evidence


def test_bimi_absent_fail_baixa(monkeypatch):
    monkeypatch.setattr(dns_util, "resolve_txt", _const([]))
    r = _run(bimi.check(URL))
    assert r.status == Status.FAIL and r.severity == Severity.BAIXA


def test_bimi_without_dmarc_enforce_note(monkeypatch):
    monkeypatch.setattr(dns_util, "resolve_txt", _txt_map({
        "_bimi": ["v=BIMI1; l=https://x.com.br/logo.svg"],
        "_dmarc": ["v=DMARC1; p=none"],
    }))
    r = _run(bimi.check(URL))
    assert r.status == Status.PASS and "DMARC" in r.evidence


# --- 13: classificações ---------------------------------------------------- #

def test_dns_checks_classified():
    assert classify("check_37_dnssec") == ("A02:2025 Cryptographic Failures", "CWE-350", "Art. 46")
    assert classify("check_38_caa") == ("A02:2025 Cryptographic Failures", "CWE-295", "Art. 46")
    assert classify("check_39_mta_sts") == ("A02:2025 Cryptographic Failures", "CWE-319", "Art. 46")
    assert classify("check_40_bimi") == ("A07:2025 Identification and Authentication Failures", "CWE-290", None)
