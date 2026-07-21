"""Testes KL-32 — análise de qualidade de headers + headers modernos. Offline."""

from __future__ import annotations

import asyncio

import httpx

from scanner.checks import (
    check_csp, check_hsts, check_17_cookies,
    check_31_permissions_policy as pp,
    check_32_coop as coop,
    check_33_coep as coep,
    check_34_corp as corp,
    check_35_referrer_policy as ref,
    check_36_cache_control_forms as cache,
)
from scanner.checks.base import Status, Severity
from scanner.checks.classifications import classify

URL = "https://x.com.br"

# Padding inerte (>100 chars, sem <form>/<input>) para que o ``content_guard``
# (KL-94) do check_36 (Tipo B) não trate estes fixtures curtos como "resposta
# vazia/mínima". Não altera a detecção de formulário/senha.
_BODY_PAD = (
    "<p>Conteudo institucional de exemplo para uma pagina real com texto "
    "suficiente para representar um site legitimo em producao com varios "
    "paragrafos sobre a empresa.</p>"
)


def _resp(headers=None, text=""):
    return httpx.Response(200, headers=headers or {}, text=text,
                          request=httpx.Request("GET", URL + "/"))


def _patch(monkeypatch, mod, headers=None, text=""):
    resp = _resp(headers, text)

    async def _f(url, method="GET", **kw):
        return resp
    monkeypatch.setattr(mod, "fetch", _f)


def _run(coro):
    return asyncio.run(coro)


# --- check_05 CSP: análise de qualidade ------------------------------------ #

def test_csp_unsafe_inline_fails(monkeypatch):
    _patch(monkeypatch, check_csp, {"content-security-policy": "script-src 'self' 'unsafe-inline'"})
    r = _run(check_csp.check(URL))
    assert r.status == Status.FAIL and "unsafe-inline" in r.evidence


def test_csp_unsafe_eval_fails(monkeypatch):
    _patch(monkeypatch, check_csp, {"content-security-policy": "default-src 'self'; script-src 'self' 'unsafe-eval'"})
    assert _run(check_csp.check(URL)).status == Status.FAIL


def test_csp_wildcard_fails(monkeypatch):
    _patch(monkeypatch, check_csp, {"content-security-policy": "default-src *"})
    assert _run(check_csp.check(URL)).status == Status.FAIL


def test_csp_missing_default_src_is_pass_with_note(monkeypatch):
    _patch(monkeypatch, check_csp, {"content-security-policy": "script-src 'self'"})
    r = _run(check_csp.check(URL))
    assert r.status == Status.PASS and "default-src" in r.evidence


def test_csp_clean_pass(monkeypatch):
    _patch(monkeypatch, check_csp, {"content-security-policy":
        "default-src 'self'; script-src 'self'; object-src 'none'; base-uri 'self'; frame-ancestors 'none'"})
    assert _run(check_csp.check(URL)).status == Status.PASS


def test_csp_absent_fails(monkeypatch):
    _patch(monkeypatch, check_csp, {})
    assert _run(check_csp.check(URL)).status == Status.FAIL


# --- check_02 HSTS: qualidade ---------------------------------------------- #

def test_hsts_short_max_age_fails(monkeypatch):
    _patch(monkeypatch, check_hsts, {"strict-transport-security": "max-age=86400"})
    assert _run(check_hsts.check(URL)).status == Status.FAIL


def test_hsts_good_max_age_pass(monkeypatch):
    _patch(monkeypatch, check_hsts, {"strict-transport-security": "max-age=31536000"})
    assert _run(check_hsts.check(URL)).status == Status.PASS


def test_hsts_zero_fails(monkeypatch):
    _patch(monkeypatch, check_hsts, {"strict-transport-security": "max-age=0"})
    assert _run(check_hsts.check(URL)).status == Status.FAIL


def test_hsts_full_pass_no_notes(monkeypatch):
    _patch(monkeypatch, check_hsts,
           {"strict-transport-security": "max-age=31536000; includeSubDomains; preload"})
    r = _run(check_hsts.check(URL))
    assert r.status == Status.PASS and "Observações" not in r.evidence


# --- check_17 cookies: granular -------------------------------------------- #

def test_cookie_samesite_none_without_secure_fails(monkeypatch):
    _patch(monkeypatch, check_17_cookies, [("set-cookie", "pref=x; SameSite=None")])
    r = _run(check_17_cookies.check(URL))
    assert r.status == Status.FAIL and "SameSite=None" in r.evidence


def test_cookie_secure_prefix_without_secure_fails(monkeypatch):
    _patch(monkeypatch, check_17_cookies, [("set-cookie", "__Secure-id=x; Path=/")])
    assert _run(check_17_cookies.check(URL)).status == Status.FAIL


def test_cookie_ok_pass(monkeypatch):
    _patch(monkeypatch, check_17_cookies,
           [("set-cookie", "sessionid=x; Secure; HttpOnly; SameSite=Lax")])
    assert _run(check_17_cookies.check(URL)).status == Status.PASS


# --- check_31 Permissions-Policy ------------------------------------------- #

def test_permissions_policy_present_pass(monkeypatch):
    _patch(monkeypatch, pp, {"permissions-policy": "camera=(), microphone=()"})
    assert _run(pp.check(URL)).status == Status.PASS


def test_permissions_policy_absent_fails(monkeypatch):
    _patch(monkeypatch, pp, {})
    r = _run(pp.check(URL))
    assert r.status == Status.FAIL and r.severity == Severity.MEDIA


def test_permissions_policy_wide_open_fails(monkeypatch):
    _patch(monkeypatch, pp, {"permissions-policy": "camera=*"})
    r = _run(pp.check(URL))
    assert r.status == Status.FAIL and "camera" in r.evidence


# --- check_32 COOP --------------------------------------------------------- #

def test_coop_safe_pass(monkeypatch):
    _patch(monkeypatch, coop, {"cross-origin-opener-policy": "same-origin"})
    assert _run(coop.check(URL)).status == Status.PASS


def test_coop_absent_fails_baixa(monkeypatch):
    _patch(monkeypatch, coop, {})
    r = _run(coop.check(URL))
    assert r.status == Status.FAIL and r.severity == Severity.BAIXA


def test_coop_unsafe_none_fails(monkeypatch):
    _patch(monkeypatch, coop, {"cross-origin-opener-policy": "unsafe-none"})
    assert _run(coop.check(URL)).status == Status.FAIL


# --- check_33 COEP / check_34 CORP ----------------------------------------- #

def test_coep_safe_pass_and_absent_fail(monkeypatch):
    _patch(monkeypatch, coep, {"cross-origin-embedder-policy": "require-corp"})
    assert _run(coep.check(URL)).status == Status.PASS
    _patch(monkeypatch, coep, {})
    r = _run(coep.check(URL))
    assert r.status == Status.FAIL and r.severity == Severity.BAIXA


def test_corp_present_pass_and_absent_fail(monkeypatch):
    _patch(monkeypatch, corp, {"cross-origin-resource-policy": "same-site"})
    assert _run(corp.check(URL)).status == Status.PASS
    _patch(monkeypatch, corp, {})
    r = _run(corp.check(URL))
    assert r.status == Status.FAIL and r.severity == Severity.BAIXA


# --- check_35 Referrer-Policy ---------------------------------------------- #

def test_referrer_recommended_pass(monkeypatch):
    _patch(monkeypatch, ref, {"referrer-policy": "strict-origin-when-cross-origin"})
    assert _run(ref.check(URL)).status == Status.PASS


def test_referrer_unsafe_url_fails(monkeypatch):
    _patch(monkeypatch, ref, {"referrer-policy": "unsafe-url"})
    r = _run(ref.check(URL))
    assert r.status == Status.FAIL and r.severity == Severity.MEDIA


def test_referrer_absent_fails_baixa(monkeypatch):
    _patch(monkeypatch, ref, {})
    r = _run(ref.check(URL))
    assert r.status == Status.FAIL and r.severity == Severity.BAIXA


# --- check_36 Cache-Control em formulários --------------------------------- #

def test_cache_form_with_no_store_pass(monkeypatch):
    _patch(monkeypatch, cache, {"cache-control": "no-store"}, text="<form><input type='password'></form>" + _BODY_PAD)
    assert _run(cache.check(URL)).status == Status.PASS


def test_cache_form_without_header_fails(monkeypatch):
    _patch(monkeypatch, cache, {}, text="<form action='/login'>...</form>" + _BODY_PAD)
    assert _run(cache.check(URL)).status == Status.FAIL


def test_cache_no_form_pass(monkeypatch):
    _patch(monkeypatch, cache, {}, text="<html><body>sem formulário</body></html>" + _BODY_PAD)
    assert _run(cache.check(URL)).status == Status.PASS


# --- classificações KL-34/35 dos novos checks ------------------------------ #

def test_new_checks_classified():
    assert classify("check_31_permissions_policy") == ("A05:2025 Security Misconfiguration", "CWE-693", "Art. 46")
    assert classify("check_32_coop") == ("A05:2025 Security Misconfiguration", "CWE-346", "Art. 46")
    assert classify("check_33_coep") == ("A05:2025 Security Misconfiguration", "CWE-346", "Art. 46")
    assert classify("check_34_corp") == ("A05:2025 Security Misconfiguration", "CWE-346", "Art. 46")
    assert classify("check_35_referrer_policy") == ("A05:2025 Security Misconfiguration", "CWE-200", "Art. 46")
    assert classify("check_36_cache_control_forms") == ("A05:2025 Security Misconfiguration", "CWE-524", "Art. 46")
