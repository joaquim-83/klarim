"""KL-147 — detecção de SPA fallback por fingerprint (ETag / Content-Type+Content-Length).

Um SPA/app que devolve 200 + index.html para QUALQUER path gera falsos positivos massivos no
Security Gate (`/.env`, `/admin`, `/swagger` todos "200"). O engine faz um probe de controle
(HEAD num path inexistente); se responde 200, captura o fingerprint e os checks de exposição/API
comparam cada 200 com ele — mesmo fingerprint = fallback (PASS), diferente = exposição real (FAIL).
Offline: httpx.MockTransport."""
from __future__ import annotations

import asyncio

import httpx
import pytest

from security_gate import engine as sge
from security_gate.checks.api_security import check_api_security
from security_gate.checks.exposure import check_exposure
from security_gate.config import GateConfig
from security_gate.models import Status
from security_gate.utils import matches_spa_fingerprint


def _run(coro):
    return asyncio.run(coro)


def _resp(status=200, **headers):
    return httpx.Response(status, headers=headers)


# Fingerprint típico de um SPA (index.html servido para tudo).
_SPA_FP = {"etag": '"6a71ccfa-9d2"', "content_type": "text/html", "content_length": "2514"}


# =========================================================================== #
# matches_spa_fingerprint (função pura)
# =========================================================================== #

def test_match_by_etag():
    assert matches_spa_fingerprint(_resp(etag='"6a71ccfa-9d2"'), _SPA_FP) is True


def test_no_match_different_etag():
    assert matches_spa_fingerprint(_resp(etag='"outro"'), _SPA_FP) is False


def test_match_by_ct_and_cl_when_no_etag():
    fp = {"etag": None, "content_type": "text/html", "content_length": "2514"}
    r = _resp(**{"content-type": "text/html; charset=utf-8", "content-length": "2514"})
    assert matches_spa_fingerprint(r, fp) is True


def test_no_match_different_cl():
    fp = {"etag": None, "content_type": "text/html", "content_length": "2514"}
    r = _resp(**{"content-type": "text/html", "content-length": "999"})
    assert matches_spa_fingerprint(r, fp) is False


def test_no_match_empty_fingerprint():
    assert matches_spa_fingerprint(_resp(etag='"x"'), None) is False


def test_no_match_when_no_etag_and_no_cl():
    # resposta sem etag e sem content-length não pode casar por CL → False.
    fp = {"etag": None, "content_type": "text/html", "content_length": "2514"}
    assert matches_spa_fingerprint(_resp(**{"content-type": "text/html"}), fp) is False


# =========================================================================== #
# _detect_spa_fallback (probe de controle)
# =========================================================================== #

class _Handler:
    """path -> (status, headers). `default` responde ao probe (path aleatório)."""
    def __init__(self, routes=None, default=(404, {}), raise_all=False):
        self.routes = routes or {}
        self.default = default
        self.raise_all = raise_all
        self.calls = []

    def __call__(self, request):
        self.calls.append((request.method, request.url.path))
        if self.raise_all:
            raise httpx.ConnectError("boom", request=request)
        st, hdrs = self.routes.get(request.url.path, self.default)
        return httpx.Response(st, headers=hdrs)


def _client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=True)


async def _detect(handler, base="https://x.test"):
    async with _client(handler) as c:
        return await sge._detect_spa_fallback(c, base)


def test_detect_nginx_404_returns_none():
    # nginx devolve 404 p/ path inexistente → não é SPA → None (checks rodam normalmente).
    fp = _run(_detect(_Handler(default=(404, {}))))
    assert fp is None


def test_detect_spa_200_captures_fingerprint():
    h = _Handler(default=(200, {"etag": '"6a71ccfa-9d2"', "content-type": "text/html; charset=utf-8",
                                "content-length": "2514"}))
    fp = _run(_detect(h))
    assert fp is not None
    assert fp["etag"] == '"6a71ccfa-9d2"'
    assert fp["content_type"] == "text/html"          # normalizado (sem charset)
    assert fp["content_length"] == "2514"
    # o probe usa HEAD num path /_klarim_gate_probe_*
    assert h.calls and h.calls[0][0] == "HEAD" and h.calls[0][1].startswith("/_klarim_gate_probe_")


def test_detect_network_error_returns_none():
    fp = _run(_detect(_Handler(raise_all=True)))
    assert fp is None   # graceful — nunca derruba o gate


# =========================================================================== #
# Exposure com fingerprint
# =========================================================================== #

async def _exposure(routes, spa_fp, base="https://x.test"):
    async with _client(_Handler(routes)) as c:
        return await check_exposure(c, base, GateConfig(exposure_allowlist=[]), spa_fp)


def _find(results, check):
    return next(r for r in results if r.check == check)


def test_admin_same_fingerprint_is_pass():
    # /admin com o MESMO ETag do probe → fallback de SPA → PASS (path SEM extensão que o guard
    # de Content-Type não pegaria).
    res = _run(_exposure({"/admin": (200, {"etag": '"6a71ccfa-9d2"'})}, _SPA_FP))
    assert _find(res, "admin_panel_exposed").status == Status.PASS


def test_env_same_fingerprint_is_pass():
    res = _run(_exposure({"/.env": (200, {"etag": '"6a71ccfa-9d2"'})}, _SPA_FP))
    assert _find(res, "env_exposed").status == Status.PASS


def test_env_different_fingerprint_is_fail():
    # /.env com ETag DIFERENTE + content-type binário (não text/html, senão o guard nonhtml pegaria)
    # → arquivo real exposto → FAIL crítico.
    res = _run(_exposure(
        {"/.env": (200, {"etag": '"real-env"', "content-type": "application/octet-stream"})}, _SPA_FP))
    r = _find(res, "env_exposed")
    assert r.status == Status.FAIL and r.http_status == 200


def test_admin_json_is_fail():
    # /admin devolvendo JSON (ETag diferente, não é o index.html) → endpoint real → FAIL.
    res = _run(_exposure(
        {"/admin": (200, {"etag": '"api"', "content-type": "application/json"})}, _SPA_FP))
    assert _find(res, "admin_panel_exposed").status == Status.FAIL


def test_admin_match_by_cl_no_etag_is_pass():
    # sem ETag: casa por Content-Type + Content-Length iguais ao probe → PASS.
    fp = {"etag": None, "content_type": "text/html", "content_length": "2514"}
    res = _run(_exposure(
        {"/admin": (200, {"content-type": "text/html", "content-length": "2514"})}, fp))
    assert _find(res, "admin_panel_exposed").status == Status.PASS


def test_admin_different_cl_is_fail():
    # mesmo Content-Type mas Content-Length diferente → response diferente do fallback → FAIL.
    fp = {"etag": None, "content_type": "text/html", "content_length": "2514"}
    res = _run(_exposure(
        {"/admin": (200, {"content-type": "text/html", "content-length": "9999"})}, fp))
    assert _find(res, "admin_panel_exposed").status == Status.FAIL


def test_exposure_no_fingerprint_still_fails_real():
    # Não-regressão: sem fingerprint (site não-SPA), um /.env real segue FAIL.
    res = _run(_exposure({"/.env": (200, {"content-type": "application/octet-stream"})}, None))
    assert _find(res, "env_exposed").status == Status.FAIL


# =========================================================================== #
# API security com fingerprint
# =========================================================================== #

async def _api(routes, spa_fp, endpoints=("/api/admin/",)):
    cfg = GateConfig(api_root_path="/api/", protected_endpoints=list(endpoints))
    async with _client(_Handler(routes)) as c:
        return await check_api_security(c, "https://x.test", cfg, spa_fp)


def test_api_endpoint_same_fingerprint_is_pass():
    res = _run(_api({"/api/": (200, {}), "/api/admin/": (200, {"etag": '"6a71ccfa-9d2"'})}, _SPA_FP))
    r = _find(res, "api_protected")
    assert r.status == Status.PASS and "fallback de SPA" in r.detail


def test_api_endpoint_401_is_pass():
    res = _run(_api({"/api/": (200, {}), "/api/admin/": (401, {})}, _SPA_FP))
    r = _find(res, "api_protected")
    assert r.status == Status.PASS and "auth" in r.detail


def test_api_endpoint_200_different_fingerprint_is_fail():
    res = _run(_api({"/api/": (200, {}), "/api/admin/": (200, {"etag": '"real-api"'})}, _SPA_FP))
    r = _find(res, "api_unprotected")
    assert r.status == Status.FAIL


def test_api_no_fingerprint_200_is_fail():
    # Não-regressão: sem fingerprint, um endpoint protegido em 200 segue FAIL.
    res = _run(_api({"/api/": (200, {}), "/api/admin/": (200, {})}, None))
    assert _find(res, "api_unprotected").status == Status.FAIL
