"""Offline unit tests for the KL-22 checks (16–29).

All 14 checks are exercised with the network mocked so CI stays hermetic:

* HTTP checks (16, 17, 19, 20, 24, 25) monkeypatch ``<module>.fetch``.
* CORS (18) monkeypatches ``check_18_cors._probe``.
* DNS checks (21, 22, 23, 27) monkeypatch the shared ``dns_util`` helpers.
* External-API checks (26, 28, 29) monkeypatch the module's HTTP seam
  (``_crtsh`` / ``_breaches`` / ``_query``).

Each check is asserted on the contract PASS / FAIL / INCONCLUSO, never on the
network.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest

from scanner.checks import (
    dns_util,
    check_16_api_docs,
    check_17_cookies,
    check_18_cors,
    check_19_redirect_domain,
    check_20_info_disclosure,
    check_21_spf,
    check_22_dkim,
    check_23_dmarc,
    check_24_mixed_content,
    check_25_form_security,
    check_26_subdomains,
    check_27_dangling_cname,
    check_28_hibp,
    check_29_safe_browsing,
)
from scanner.checks.base import Status, Severity
from scanner import ALL_CHECKS
from reporter.risk_messages import RISK_MESSAGES

URL = "https://www.example.com"


# --------------------------------------------------------------------------- #
# Fakes / helpers
# --------------------------------------------------------------------------- #

class FakeResp:
    """Minimal stand-in for httpx.Response (status_code / text / headers / json)."""

    def __init__(self, status_code=200, text="", headers=None, json_data=None):
        self.status_code = status_code
        self.text = text
        # headers may be a dict or a list of tuples (for repeated Set-Cookie)
        self.headers = httpx.Headers(headers or [])
        self._json = json_data

    def json(self):
        if self._json is not None:
            return self._json
        import json as _json
        return _json.loads(self.text)


def _fetch_returning(*resps):
    """Async fetch stub that returns each response in turn, then repeats the last."""
    seq = list(resps)

    async def _fetch(url, method="GET", **kwargs):
        return seq[0] if len(seq) == 1 else (seq.pop(0) if len(seq) > 1 else seq[0])

    return _fetch


def _fetch_const(resp):
    async def _fetch(url, method="GET", **kwargs):
        return resp
    return _fetch


def _run(coro):
    return asyncio.run(coro)


# --------------------------------------------------------------------------- #
# Check 16 — API docs exposed
# --------------------------------------------------------------------------- #

def test_check16_fail_when_swagger_marker(monkeypatch):
    body = "<html><body><div id='swagger-ui'></div></body></html>"
    monkeypatch.setattr(check_16_api_docs, "fetch", _fetch_const(FakeResp(200, body)))
    r = _run(check_16_api_docs.check(URL))
    assert r.status == Status.FAIL and r.severity == Severity.ALTA


def test_check16_pass_when_plain_spa(monkeypatch):
    body = "<html><body><div id='root'>app</div></body></html>"
    monkeypatch.setattr(check_16_api_docs, "fetch", _fetch_const(FakeResp(200, body)))
    r = _run(check_16_api_docs.check(URL))
    assert r.status == Status.PASS


def test_check16_pass_when_404(monkeypatch):
    monkeypatch.setattr(check_16_api_docs, "fetch", _fetch_const(FakeResp(404, "not found")))
    r = _run(check_16_api_docs.check(URL))
    assert r.status == Status.PASS


# --------------------------------------------------------------------------- #
# Check 17 — Cookies without security flags
# --------------------------------------------------------------------------- #

def test_check17_fail_session_cookie_missing_flags(monkeypatch):
    resp = FakeResp(200, headers=[("set-cookie", "sessionid=abc; Path=/")])
    monkeypatch.setattr(check_17_cookies, "fetch", _fetch_const(resp))
    r = _run(check_17_cookies.check(URL))
    assert r.status == Status.FAIL and r.severity == Severity.MEDIA


def test_check17_pass_session_cookie_with_flags(monkeypatch):
    resp = FakeResp(200, headers=[("set-cookie",
                    "sessionid=abc; Secure; HttpOnly; SameSite=Strict")])
    monkeypatch.setattr(check_17_cookies, "fetch", _fetch_const(resp))
    r = _run(check_17_cookies.check(URL))
    assert r.status == Status.PASS


def test_check17_pass_no_cookies(monkeypatch):
    monkeypatch.setattr(check_17_cookies, "fetch", _fetch_const(FakeResp(200)))
    r = _run(check_17_cookies.check(URL))
    assert r.status == Status.PASS


def test_check17_pass_non_sensitive_cookie(monkeypatch):
    # a plain preference cookie without flags is not a session cookie -> PASS
    resp = FakeResp(200, headers=[("set-cookie", "lang=pt; Path=/")])
    monkeypatch.setattr(check_17_cookies, "fetch", _fetch_const(resp))
    r = _run(check_17_cookies.check(URL))
    assert r.status == Status.PASS


# --------------------------------------------------------------------------- #
# Check 18 — Permissive CORS
# --------------------------------------------------------------------------- #

def _probe_returning(resp):
    async def _probe(url, method):
        return resp
    return _probe


def test_check18_fail_wildcard(monkeypatch):
    resp = FakeResp(200, headers={"access-control-allow-origin": "*"})
    monkeypatch.setattr(check_18_cors, "_probe", _probe_returning(resp))
    r = _run(check_18_cors.check(URL))
    assert r.status == Status.FAIL and r.severity == Severity.ALTA


def test_check18_fail_reflects_evil_origin(monkeypatch):
    resp = FakeResp(200, headers={"access-control-allow-origin": check_18_cors._EVIL})
    monkeypatch.setattr(check_18_cors, "_probe", _probe_returning(resp))
    r = _run(check_18_cors.check(URL))
    assert r.status == Status.FAIL


def test_check18_pass_no_acao(monkeypatch):
    monkeypatch.setattr(check_18_cors, "_probe", _probe_returning(FakeResp(200)))
    r = _run(check_18_cors.check(URL))
    assert r.status == Status.PASS


def test_check18_pass_specific_origin(monkeypatch):
    resp = FakeResp(200, headers={"access-control-allow-origin": "https://www.example.com"})
    monkeypatch.setattr(check_18_cors, "_probe", _probe_returning(resp))
    r = _run(check_18_cors.check(URL))
    assert r.status == Status.PASS


# --------------------------------------------------------------------------- #
# Check 19 — Redirect to a different domain
# --------------------------------------------------------------------------- #

def test_check19_fail_cross_domain(monkeypatch):
    resp = FakeResp(301, headers={"location": "https://other-domain.net/"})
    monkeypatch.setattr(check_19_redirect_domain, "fetch", _fetch_const(resp))
    r = _run(check_19_redirect_domain.check(URL))
    assert r.status == Status.FAIL and r.severity == Severity.MEDIA


def test_check19_pass_same_domain(monkeypatch):
    resp = FakeResp(301, headers={"location": "https://example.com/"})
    monkeypatch.setattr(check_19_redirect_domain, "fetch", _fetch_const(resp))
    r = _run(check_19_redirect_domain.check(URL))
    assert r.status == Status.PASS


def test_check19_pass_no_redirect(monkeypatch):
    monkeypatch.setattr(check_19_redirect_domain, "fetch", _fetch_const(FakeResp(200)))
    r = _run(check_19_redirect_domain.check(URL))
    assert r.status == Status.PASS


# --------------------------------------------------------------------------- #
# Check 20 — 403/404 differentiation on sensitive paths
# --------------------------------------------------------------------------- #

def test_check20_fail_on_403(monkeypatch):
    monkeypatch.setattr(check_20_info_disclosure, "fetch", _fetch_const(FakeResp(403)))
    r = _run(check_20_info_disclosure.check(URL))
    assert r.status == Status.FAIL and r.severity == Severity.BAIXA


def test_check20_pass_on_404(monkeypatch):
    monkeypatch.setattr(check_20_info_disclosure, "fetch", _fetch_const(FakeResp(404)))
    r = _run(check_20_info_disclosure.check(URL))
    assert r.status == Status.PASS


# --------------------------------------------------------------------------- #
# Check 21 — SPF
# --------------------------------------------------------------------------- #

def _txt_returning(value):
    def _resolve(name, timeout=5.0):
        return value
    return _resolve


def test_check21_fail_absent(monkeypatch):
    monkeypatch.setattr(dns_util, "resolve_txt", _txt_returning([]))
    r = _run(check_21_spf.check(URL))
    assert r.status == Status.FAIL and r.severity == Severity.ALTA


def test_check21_fail_plus_all(monkeypatch):
    monkeypatch.setattr(dns_util, "resolve_txt", _txt_returning(["v=spf1 +all"]))
    r = _run(check_21_spf.check(URL))
    assert r.status == Status.FAIL


def test_check21_pass_restrictive(monkeypatch):
    monkeypatch.setattr(dns_util, "resolve_txt",
                        _txt_returning(["v=spf1 include:_spf.google.com ~all"]))
    r = _run(check_21_spf.check(URL))
    assert r.status == Status.PASS


def test_check21_fail_no_all(monkeypatch):
    monkeypatch.setattr(dns_util, "resolve_txt",
                        _txt_returning(["v=spf1 include:_spf.google.com"]))
    r = _run(check_21_spf.check(URL))
    assert r.status == Status.FAIL


def test_check21_inconclusive_on_dns_error(monkeypatch):
    monkeypatch.setattr(dns_util, "resolve_txt", _txt_returning(None))
    r = _run(check_21_spf.check(URL))
    assert r.status == Status.INCONCLUSO


# --------------------------------------------------------------------------- #
# Check 22 — DKIM
# --------------------------------------------------------------------------- #

def test_check22_pass_when_selector_found(monkeypatch):
    def _resolve(name, timeout=4.0):
        if name.startswith("default._domainkey"):
            return ["v=DKIM1; k=rsa; p=MIGf..."]
        return []
    monkeypatch.setattr(dns_util, "resolve_txt", _resolve)
    r = _run(check_22_dkim.check(URL))
    assert r.status == Status.PASS


def test_check22_checks_resend_selector(monkeypatch):
    # DKIM só no seletor 'resend' (o caso do klarim.net) → PASS.
    assert "resend" in check_22_dkim.DKIM_SELECTORS

    def _resolve(name, timeout=4.0):
        if name.startswith("resend._domainkey"):
            return ["v=DKIM1; k=rsa; p=MIGfMA0..."]
        return []
    monkeypatch.setattr(dns_util, "resolve_txt", _resolve)
    r = _run(check_22_dkim.check(URL))
    assert r.status == Status.PASS
    assert r.details.get("selector") == "resend"


def test_check22_fail_when_all_absent(monkeypatch):
    monkeypatch.setattr(dns_util, "resolve_txt", _txt_returning([]))
    r = _run(check_22_dkim.check(URL))
    assert r.status == Status.FAIL and r.severity == Severity.MEDIA


def test_check22_inconclusive_when_all_dns_error(monkeypatch):
    monkeypatch.setattr(dns_util, "resolve_txt", _txt_returning(None))
    r = _run(check_22_dkim.check(URL))
    assert r.status == Status.INCONCLUSO


# --------------------------------------------------------------------------- #
# Check 23 — DMARC
# --------------------------------------------------------------------------- #

def test_check23_fail_absent(monkeypatch):
    monkeypatch.setattr(dns_util, "resolve_txt", _txt_returning([]))
    r = _run(check_23_dmarc.check(URL))
    assert r.status == Status.FAIL and r.severity == Severity.ALTA


def test_check23_fail_p_none(monkeypatch):
    monkeypatch.setattr(dns_util, "resolve_txt", _txt_returning(["v=DMARC1; p=none"]))
    r = _run(check_23_dmarc.check(URL))
    assert r.status == Status.FAIL


def test_check23_pass_reject(monkeypatch):
    monkeypatch.setattr(dns_util, "resolve_txt",
                        _txt_returning(["v=DMARC1; p=reject; rua=mailto:x@y.com"]))
    r = _run(check_23_dmarc.check(URL))
    assert r.status == Status.PASS


def test_check23_inconclusive_on_dns_error(monkeypatch):
    monkeypatch.setattr(dns_util, "resolve_txt", _txt_returning(None))
    r = _run(check_23_dmarc.check(URL))
    assert r.status == Status.INCONCLUSO


# --------------------------------------------------------------------------- #
# Check 24 — Mixed content
# --------------------------------------------------------------------------- #

def test_check24_fail_http_resource(monkeypatch):
    html = '<html><head><script src="http://cdn.insecure.net/x.js"></script></head></html>'
    monkeypatch.setattr(check_24_mixed_content, "fetch", _fetch_const(FakeResp(200, html)))
    r = _run(check_24_mixed_content.check(URL))
    assert r.status == Status.FAIL and r.severity == Severity.MEDIA


def test_check24_pass_all_https(monkeypatch):
    html = '<html><head><script src="https://cdn.ok.net/x.js"></script></head></html>'
    monkeypatch.setattr(check_24_mixed_content, "fetch", _fetch_const(FakeResp(200, html)))
    r = _run(check_24_mixed_content.check(URL))
    assert r.status == Status.PASS


def test_check24_pass_localhost_ignored(monkeypatch):
    html = '<html><head><img src="http://localhost:3000/dev.png"></head></html>'
    monkeypatch.setattr(check_24_mixed_content, "fetch", _fetch_const(FakeResp(200, html)))
    r = _run(check_24_mixed_content.check(URL))
    assert r.status == Status.PASS


# --------------------------------------------------------------------------- #
# Check 25 — Insecure forms
# --------------------------------------------------------------------------- #

def test_check25_fail_http_action(monkeypatch):
    html = '<form method="post" action="http://insecure.net/submit"></form>'
    monkeypatch.setattr(check_25_form_security, "fetch", _fetch_const(FakeResp(200, html)))
    r = _run(check_25_form_security.check(URL))
    assert r.status == Status.FAIL and r.severity == Severity.ALTA


def test_check25_fail_cross_origin(monkeypatch):
    html = '<form method="post" action="https://other-site.org/submit"></form>'
    monkeypatch.setattr(check_25_form_security, "fetch", _fetch_const(FakeResp(200, html)))
    r = _run(check_25_form_security.check(URL))
    assert r.status == Status.FAIL


def test_check25_pass_relative_action(monkeypatch):
    html = '<form method="post" action="/submit"></form>'
    monkeypatch.setattr(check_25_form_security, "fetch", _fetch_const(FakeResp(200, html)))
    r = _run(check_25_form_security.check(URL))
    assert r.status == Status.PASS


def test_check25_pass_no_forms(monkeypatch):
    monkeypatch.setattr(check_25_form_security, "fetch",
                        _fetch_const(FakeResp(200, "<html></html>")))
    r = _run(check_25_form_security.check(URL))
    assert r.status == Status.PASS


# --------------------------------------------------------------------------- #
# Check 26 — Subdomains via CT logs
# --------------------------------------------------------------------------- #

def _crtsh_returning(rows=None, raise_exc=None):
    async def _crtsh(domain):
        if raise_exc:
            raise raise_exc
        return rows or []
    return _crtsh


def test_check26_fail_many_and_sensitive(monkeypatch):
    subs = [f"host{i}.example.com" for i in range(25)] + ["admin.example.com",
                                                          "staging.example.com"]
    rows = [{"name_value": s} for s in subs]
    monkeypatch.setattr(check_26_subdomains, "_crtsh", _crtsh_returning(rows))
    r = _run(check_26_subdomains.check(URL))
    assert r.status == Status.FAIL and r.severity == Severity.MEDIA


def test_check26_pass_few_subdomains(monkeypatch):
    rows = [{"name_value": "www.example.com"}, {"name_value": "blog.example.com"}]
    monkeypatch.setattr(check_26_subdomains, "_crtsh", _crtsh_returning(rows))
    r = _run(check_26_subdomains.check(URL))
    assert r.status == Status.PASS


def test_check26_inconclusive_on_error(monkeypatch):
    monkeypatch.setattr(check_26_subdomains, "_crtsh",
                        _crtsh_returning(raise_exc=httpx.HTTPError("down")))
    r = _run(check_26_subdomains.check(URL))
    assert r.status == Status.INCONCLUSO


# --------------------------------------------------------------------------- #
# Check 27 — Dangling CNAME
# --------------------------------------------------------------------------- #

def test_check27_fail_takeover(monkeypatch):
    def _cname(name, timeout=3.0):
        return "myapp.herokuapp.com" if name.startswith("www.") else None

    def _exists(name, timeout=3.0):
        return False  # herokuapp target is gone -> takeover possible
    monkeypatch.setattr(dns_util, "resolve_cname", _cname)
    monkeypatch.setattr(dns_util, "host_exists", _exists)
    r = _run(check_27_dangling_cname.check(URL))
    assert r.status == Status.FAIL and r.severity == Severity.CRITICA


def test_check27_pass_safe_cname(monkeypatch):
    def _cname(name, timeout=3.0):
        return "d123.cloudfront.net" if name.startswith("www.") else None
    monkeypatch.setattr(dns_util, "resolve_cname", _cname)
    monkeypatch.setattr(dns_util, "host_exists", lambda n, t=3.0: True)
    r = _run(check_27_dangling_cname.check(URL))
    assert r.status == Status.PASS


def test_check27_inconclusive_no_dns(monkeypatch):
    monkeypatch.setattr(dns_util, "resolve_cname", lambda n, t=3.0: None)
    r = _run(check_27_dangling_cname.check(URL))
    assert r.status == Status.INCONCLUSO


def test_check27_pass_takeover_target_alive(monkeypatch):
    # CNAME to a takeover-prone service, but the target still exists -> not dangling
    def _cname(name, timeout=3.0):
        return "myapp.herokuapp.com" if name.startswith("www.") else None
    monkeypatch.setattr(dns_util, "resolve_cname", _cname)
    monkeypatch.setattr(dns_util, "host_exists", lambda n, t=3.0: True)
    r = _run(check_27_dangling_cname.check(URL))
    assert r.status == Status.PASS


# --------------------------------------------------------------------------- #
# Check 28 — HIBP domain breaches
# --------------------------------------------------------------------------- #

def _breaches_returning(resp=None, raise_exc=None):
    async def _breaches(domain):
        if raise_exc:
            raise raise_exc
        return resp
    return _breaches


def test_check28_pass_on_404(monkeypatch):
    monkeypatch.setattr(check_28_hibp, "_breaches", _breaches_returning(FakeResp(404)))
    r = _run(check_28_hibp.check(URL))
    assert r.status == Status.PASS


def test_check28_fail_on_breaches(monkeypatch):
    resp = FakeResp(200, json_data=[{"Name": "SomeLeak"}, {"Name": "Another"}])
    monkeypatch.setattr(check_28_hibp, "_breaches", _breaches_returning(resp))
    r = _run(check_28_hibp.check(URL))
    assert r.status == Status.FAIL and r.severity == Severity.MEDIA


def test_check28_inconclusive_on_error(monkeypatch):
    monkeypatch.setattr(check_28_hibp, "_breaches",
                        _breaches_returning(raise_exc=httpx.HTTPError("rate limit")))
    r = _run(check_28_hibp.check(URL))
    assert r.status == Status.INCONCLUSO


# --------------------------------------------------------------------------- #
# Check 29 — Google Safe Browsing
# --------------------------------------------------------------------------- #

def _query_returning(resp):
    async def _query(target, key):
        return resp
    return _query


def test_check29_inconclusive_without_key(monkeypatch):
    monkeypatch.delenv("GOOGLE_SAFE_BROWSING_KEY", raising=False)
    r = _run(check_29_safe_browsing.check(URL))
    assert r.status == Status.INCONCLUSO


def test_check29_pass_when_clean(monkeypatch):
    monkeypatch.setenv("GOOGLE_SAFE_BROWSING_KEY", "test-key")
    monkeypatch.setattr(check_29_safe_browsing, "_query",
                        _query_returning(FakeResp(200, json_data={})))
    r = _run(check_29_safe_browsing.check(URL))
    assert r.status == Status.PASS


def test_check29_fail_when_flagged(monkeypatch):
    monkeypatch.setenv("GOOGLE_SAFE_BROWSING_KEY", "test-key")
    resp = FakeResp(200, json_data={"matches": [{"threatType": "MALWARE"}]})
    monkeypatch.setattr(check_29_safe_browsing, "_query", _query_returning(resp))
    r = _run(check_29_safe_browsing.check(URL))
    assert r.status == Status.FAIL and r.severity == Severity.CRITICA


# --------------------------------------------------------------------------- #
# Registry + reporter coverage (guards against forgetting a new check)
# --------------------------------------------------------------------------- #

def test_all_29_checks_registered():
    ids = [cid for cid, _ in ALL_CHECKS]
    assert len(ids) == 29
    assert len(set(ids)) == 29
    for i in range(16, 30):
        assert any(cid.startswith(f"check_{i}_") for cid in ids), i


def test_risk_messages_cover_all_checks():
    ids = [cid for cid, _ in ALL_CHECKS]
    for cid in ids:
        assert cid in RISK_MESSAGES, cid
        entry = RISK_MESSAGES[cid]
        assert entry.get("headline") and entry.get("risk") and entry.get("icon")


def test_reporter_content_covers_all_checks():
    try:  # generator importa WeasyPrint (libs nativas); pula se ausentes
        from reporter.generator import ACCESSIBLE, TECHNICAL
    except Exception:  # noqa: BLE001
        pytest.skip("bibliotecas nativas do WeasyPrint indisponíveis")
    ids = [cid for cid, _ in ALL_CHECKS]
    for cid in ids:
        assert cid in ACCESSIBLE, f"ACCESSIBLE missing {cid}"
        assert cid in TECHNICAL, f"TECHNICAL missing {cid}"
        assert "impact" in TECHNICAL[cid] and "fix" in TECHNICAL[cid]
