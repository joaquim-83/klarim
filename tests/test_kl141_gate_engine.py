"""KL-141 Prompt 1 — Security Gate: models, engine, e checks de exposição/headers/SSL.
Offline: httpx.MockTransport para os checks HTTP; `get_tls_info` mockado no check de SSL."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from security_gate import engine as sge
from security_gate.checks.exposure import check_exposure, _is_directory_listing
from security_gate.checks.headers import check_headers
from security_gate.checks import ssl as gssl
from security_gate.models import GateReport, Result, Severity, Status


def _run(coro):
    return asyncio.run(coro)


def _mk(check, status, sev):
    return Result(check, "x", "/", status, sev, "d")


# =========================================================================== #
# Models
# =========================================================================== #

def test_score_no_fails_is_100():
    r = GateReport("u", [_mk("a", Status.PASS, Severity.CRITICAL)])
    assert r.score == 100


def test_score_one_critical_fail_is_80():
    r = GateReport("u", [_mk("a", Status.FAIL, Severity.CRITICAL)])
    assert r.score == 80


def test_score_two_high_fails_is_80():
    r = GateReport("u", [_mk("a", Status.FAIL, Severity.HIGH), _mk("b", Status.FAIL, Severity.HIGH)])
    assert r.score == 80


def test_score_floors_at_zero():
    r = GateReport("u", [_mk(str(i), Status.FAIL, Severity.CRITICAL) for i in range(10)])
    assert r.score == 0


def test_passed_true_without_critical():
    r = GateReport("u", [_mk("a", Status.FAIL, Severity.HIGH), _mk("b", Status.FAIL, Severity.MEDIUM)])
    assert r.passed is True


def test_passed_false_with_critical():
    r = GateReport("u", [_mk("a", Status.FAIL, Severity.CRITICAL)])
    assert r.passed is False


def test_counts():
    r = GateReport("u", [
        _mk("a", Status.FAIL, Severity.CRITICAL),
        _mk("b", Status.FAIL, Severity.HIGH), _mk("c", Status.FAIL, Severity.HIGH),
        _mk("d", Status.FAIL, Severity.MEDIUM),
        _mk("e", Status.PASS, Severity.CRITICAL),   # PASS não conta
    ])
    assert r.critical_count == 1 and r.high_count == 2 and r.medium_count == 1


# =========================================================================== #
# Engine
# =========================================================================== #

async def _no_spa(client, url):
    return None   # KL-147: sem probe real de rede nos testes de engine com client real


def test_run_all_returns_report(monkeypatch):
    async def _fake(client, url, config=None, spa_fingerprint=None):
        return [_mk("h", Status.PASS, Severity.HIGH)]
    for k in list(sge._CHECKS):
        monkeypatch.setitem(sge._CHECKS, k, _fake)
    monkeypatch.setattr(sge, "_detect_spa_fallback", _no_spa)
    rep = _run(sge.run_all("https://x.test"))
    # KL-149: 18 checks (5 do KL-141 + 13 novos). KL-154: +email_security = 19.
    assert isinstance(rep, GateReport) and len(rep.results) == len(sge._DEFAULT_ORDER) == 19
    assert rep.duration_ms >= 0


def test_run_all_filters_checks(monkeypatch):
    ran = []

    def _maker(name):
        async def _fn(client, url, config=None, spa_fingerprint=None):
            ran.append(name)
            return []
        return _fn
    for k in list(sge._CHECKS):
        monkeypatch.setitem(sge._CHECKS, k, _maker(k))
    _run(sge.run_all("https://x.test", checks=["headers"]))
    assert ran == ["headers"]   # só o filtrado rodou (headers não é spa-aware → sem probe)


def test_run_all_check_error_isolated(monkeypatch):
    async def _boom(client, url, config=None, spa_fingerprint=None):
        raise RuntimeError("kaboom")
    async def _ok(client, url, config=None, spa_fingerprint=None):
        return [_mk("ok", Status.PASS, Severity.LOW)]
    for k in list(sge._CHECKS):
        monkeypatch.setitem(sge._CHECKS, k, _ok)
    monkeypatch.setitem(sge._CHECKS, "headers", _boom)   # só headers estoura
    monkeypatch.setattr(sge, "_detect_spa_fallback", _no_spa)
    rep = _run(sge.run_all("https://x.test"))
    assert any(r.status == Status.ERROR and "headers" in r.check for r in rep.results)
    # os outros 18 checks seguiram (isolamento do erro).
    assert sum(1 for r in rep.results if r.status == Status.PASS) == len(sge._DEFAULT_ORDER) - 1 == 18


def test_engine_respects_timeout(monkeypatch):
    captured = {}

    class _FakeClient:
        def __init__(self, **kw):
            captured.update(kw)
        async def __aenter__(self):
            return self
        async def __aexit__(self, *a):
            return False
    monkeypatch.setattr(sge.httpx, "AsyncClient", _FakeClient)
    async def _noop(client, url, config=None, spa_fingerprint=None):
        return []
    for k in list(sge._CHECKS):
        monkeypatch.setitem(sge._CHECKS, k, _noop)
    # _FakeClient não tem .head → _detect_spa_fallback captura o erro e devolve None (graceful).
    _run(sge.run_all("https://x.test", timeout=17))
    assert captured["timeout"] == 17
    assert captured["headers"]["User-Agent"] == "Klarim Security Gate/1.0"   # UA honesto


# =========================================================================== #
# Exposure
# =========================================================================== #

class _Recorder:
    """Handler de MockTransport: mapa path→status, corpos opcionais, paths que dão timeout."""
    def __init__(self, status_map=None, bodies=None, timeout_paths=()):
        self.status_map = status_map or {}
        self.bodies = bodies or {}
        self.timeout_paths = set(timeout_paths)
        self.calls = []   # (method, path)

    def __call__(self, request):
        path = request.url.path
        self.calls.append((request.method, path))
        if path in self.timeout_paths:
            raise httpx.TimeoutException("timeout", request=request)
        return httpx.Response(self.status_map.get(path, 404), text=self.bodies.get(path, ""))


def _client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=True)


async def _exposure(handler, base="https://x.test"):
    async with _client(handler) as c:
        return await check_exposure(c, base)


def _find(results, check):
    return next(r for r in results if r.check == check)


def test_env_exposed_fail_critical():
    res = _run(_exposure(_Recorder({"/.env": 200})))
    r = _find(res, "env_exposed")
    assert r.status == Status.FAIL and r.severity == Severity.CRITICAL and r.http_status == 200


def test_env_not_found_pass():
    res = _run(_exposure(_Recorder({})))   # tudo 404
    assert _find(res, "env_exposed").status == Status.PASS


def test_env_forbidden_pass():
    res = _run(_exposure(_Recorder({"/.env": 403})))
    assert _find(res, "env_exposed").status == Status.PASS


def test_git_exposed_fail_critical():
    res = _run(_exposure(_Recorder({"/.git/config": 200})))
    assert _find(res, "git_exposed").status == Status.FAIL
    assert _find(res, "git_exposed").severity == Severity.CRITICAL


def test_swagger_exposed_fail_high():
    res = _run(_exposure(_Recorder({"/swagger": 200})))
    r = _find(res, "api_docs_exposed")
    assert r.status == Status.FAIL and r.severity == Severity.HIGH


def test_debug_exposed_fail_high():
    res = _run(_exposure(_Recorder({"/debug": 200})))
    r = _find(res, "debug_exposed")
    assert r.status == Status.FAIL and r.severity == Severity.HIGH


def test_directory_listing_real_fail_medium():
    h = _Recorder({"/uploads/": 200}, bodies={"/uploads/": "<h1>Index of /uploads</h1><pre>"})
    res = _run(_exposure(h))
    r = _find(res, "directory_listing")
    assert r.status == Status.FAIL and r.severity == Severity.MEDIUM


def test_directory_listing_spa_pass():
    # SPA devolve 200 + HTML da app (sem marcadores de listagem) → NÃO é listagem real → PASS.
    h = _Recorder({"/uploads/": 200}, bodies={"/uploads/": "<html><body>App</body></html>"})
    res = _run(_exposure(h))
    assert _find(res, "directory_listing").status == Status.PASS


def test_timeout_becomes_error_not_crash():
    h = _Recorder(timeout_paths={"/.env"})
    res = _run(_exposure(h))
    # ao menos um ERROR foi registrado p/ o env (timeout); o gate não quebrou.
    assert any(r.status == Status.ERROR and r.check == "env_exposed" for r in res)


def test_head_used_get_not_called_for_simple_path():
    h = _Recorder({"/.env": 200})
    _run(_exposure(h))
    assert ("HEAD", "/.env") in h.calls
    assert ("GET", "/.env") not in h.calls   # nunca baixa o body de um path simples


def test_directory_listing_uses_get_to_confirm():
    h = _Recorder({"/uploads/": 200}, bodies={"/uploads/": "app"})
    _run(_exposure(h))
    assert ("HEAD", "/uploads/") in h.calls and ("GET", "/uploads/") in h.calls


def test_is_directory_listing_heuristic():
    assert _is_directory_listing("<title>Index of /uploads</title>")
    assert _is_directory_listing("[To Parent Directory]")
    assert not _is_directory_listing("<html><body>minha app react</body></html>")


# =========================================================================== #
# Headers
# =========================================================================== #

def _headers_handler(hdrs):
    def _h(request):
        return httpx.Response(200, headers=hdrs)
    return _h


async def _headers(hdrs, base="https://x.test"):
    async with _client(_headers_handler(hdrs)) as c:
        return await check_headers(c, base)


def test_hsts_strong_pass():
    res = _run(_headers({"Strict-Transport-Security": "max-age=31536000; includeSubDomains"}))
    assert _find(res, "header_hsts").status == Status.PASS


def test_hsts_absent_fail_high():
    res = _run(_headers({}))
    r = _find(res, "header_hsts")
    assert r.status == Status.FAIL and r.severity == Severity.HIGH


def test_hsts_short_max_age_fail():
    res = _run(_headers({"Strict-Transport-Security": "max-age=3600"}))
    assert _find(res, "header_hsts").status == Status.FAIL


def test_csp_present_pass():
    res = _run(_headers({"Content-Security-Policy": "default-src 'self'"}))
    assert _find(res, "header_csp").status == Status.PASS


def test_csp_absent_fail_high():
    res = _run(_headers({}))
    r = _find(res, "header_csp")
    assert r.status == Status.FAIL and r.severity == Severity.HIGH


def test_nosniff_pass():
    res = _run(_headers({"X-Content-Type-Options": "nosniff"}))
    assert _find(res, "header_x_content_type").status == Status.PASS


def test_server_exposes_version_fail_low():
    res = _run(_headers({"Server": "nginx/1.25.3"}))
    r = _find(res, "header_server_info")
    assert r.status == Status.FAIL and r.severity == Severity.LOW


def test_server_no_version_pass():
    res = _run(_headers({"Server": "nginx"}))
    assert _find(res, "header_server_info").status == Status.PASS


def test_server_absent_pass():
    res = _run(_headers({}))
    assert _find(res, "header_server_info").status == Status.PASS


# =========================================================================== #
# SSL (get_tls_info mockado)
# =========================================================================== #

def _tls(protocol="TLSv1.3", days=87, verified=True, ok=True, error=None):
    not_after = datetime.now(timezone.utc) + timedelta(days=days)
    return {"ok": ok, "error": error, "verified": verified, "verify_error": None,
            "protocol": protocol, "cert": {"not_after": not_after}}


def _mock_tls(monkeypatch, info=None, raises=None):
    async def _fake(host, port=443):
        if raises:
            raise raises
        return info
    monkeypatch.setattr(gssl, "get_tls_info", _fake)


async def _ssl(base="https://x.test"):
    async with _client(_Recorder()) as c:
        return await gssl.check_ssl(c, base)


def test_ssl_valid_87_days_pass(monkeypatch):
    _mock_tls(monkeypatch, _tls(days=87))
    res = _run(_ssl())
    assert _find(res, "ssl_valid").status == Status.PASS
    assert _find(res, "ssl_protocol").status == Status.PASS


def test_ssl_7_days_fail_critical(monkeypatch):
    _mock_tls(monkeypatch, _tls(days=7))
    res = _run(_ssl())
    r = _find(res, "ssl_expiring")
    assert r.status == Status.FAIL and r.severity == Severity.CRITICAL


def test_ssl_14_days_fail_high(monkeypatch):
    _mock_tls(monkeypatch, _tls(days=13))
    res = _run(_ssl())
    r = _find(res, "ssl_expiring")
    assert r.status == Status.FAIL and r.severity == Severity.HIGH


def test_ssl_expired_fail_critical(monkeypatch):
    _mock_tls(monkeypatch, _tls(days=-1, verified=False))
    res = _run(_ssl())
    r = _find(res, "ssl_expired")
    assert r.status == Status.FAIL and r.severity == Severity.CRITICAL


def test_ssl_invalid_fail_critical(monkeypatch):
    info = _tls(days=200, verified=False)
    info["verify_error"] = "hostname mismatch"
    _mock_tls(monkeypatch, info)
    res = _run(_ssl())
    r = _find(res, "ssl_invalid")
    assert r.status == Status.FAIL and r.severity == Severity.CRITICAL


def test_ssl_tls13_protocol_pass(monkeypatch):
    _mock_tls(monkeypatch, _tls(protocol="TLSv1.3"))
    assert _find(_run(_ssl()), "ssl_protocol").status == Status.PASS


def test_ssl_tls10_protocol_fail_critical(monkeypatch):
    # Python devolve "TLSv1" para TLS 1.0 → está em WEAK_PROTOCOLS.
    _mock_tls(monkeypatch, _tls(protocol="TLSv1"))
    r = _find(_run(_ssl()), "ssl_protocol")
    assert r.status == Status.FAIL and r.severity == Severity.CRITICAL


def test_ssl_error_not_crash(monkeypatch):
    _mock_tls(monkeypatch, {"ok": False, "error": "connection refused"})
    r = _find(_run(_ssl()), "ssl_error")
    assert r.status == Status.ERROR


def test_ssl_handshake_raises_becomes_error(monkeypatch):
    _mock_tls(monkeypatch, raises=OSError("boom"))
    assert _find(_run(_ssl()), "ssl_error").status == Status.ERROR
