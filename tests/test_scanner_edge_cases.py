"""KL-26 — edge cases do scanner. Mocks de HTTP/DNS simulam condições adversas; o scanner
deve sobreviver a tudo (INCONCLUSO/FAIL, nunca crash nem PASS falso). Sem rede real.
"""
from __future__ import annotations

import asyncio

import httpx
import pytest

import scanner.runner as runner
from scanner.checks import dns_util
from scanner.checks import base as checks_base
from scanner.checks.base import CheckResult, Status, Severity, content_guard
from scanner.checks import check_hsts, check_ssl
from discovery.contact import _collect_emails, _ranked_emails


def _resp(status=200, headers=None, text="", url="https://www.example.com"):
    return httpx.Response(status, headers=headers or {}, text=text,
                          request=httpx.Request("GET", url))


def _fetch_returning(resp):
    async def _f(url, method="GET", **kw):
        return resp
    return _f


def _fetch_raising(exc):
    async def _f(url, method="GET", **kw):
        raise exc
    return _f


# --------------------------------------------------------------------------- #
# Gate de acessibilidade (KL-94)
# --------------------------------------------------------------------------- #

def test_gate_nxdomain_is_domain_not_found(monkeypatch):
    monkeypatch.setattr(dns_util, "resolve_host_status", lambda d, **k: "nxdomain")
    out = asyncio.run(runner._accessibility_gate("https://naoexiste.invalid"))
    assert out[0] == "domain_not_found"


def test_gate_dns_error_is_dns_error(monkeypatch):
    monkeypatch.setattr(dns_util, "resolve_host_status", lambda d, **k: "error")
    out = asyncio.run(runner._accessibility_gate("https://x.com"))
    assert out[0] == "dns_error"


def test_gate_offline_site_is_unreachable(monkeypatch):
    monkeypatch.setattr(dns_util, "resolve_host_status", lambda d, **k: "found")
    monkeypatch.setattr(checks_base, "fetch", _fetch_raising(httpx.ConnectError("refused")))
    out = asyncio.run(runner._accessibility_gate("https://offline.com"))
    assert out[0] == "unreachable"


def test_gate_reachable_returns_none(monkeypatch):
    monkeypatch.setattr(dns_util, "resolve_host_status", lambda d, **k: "found")
    monkeypatch.setattr(checks_base, "fetch", _fetch_returning(_resp(403)))
    out = asyncio.run(runner._accessibility_gate("https://x.com"))
    assert out is None  # QUALQUER resposta (mesmo 403) = acessível


# --------------------------------------------------------------------------- #
# Check de header (HSTS) sob condições adversas de rede
# --------------------------------------------------------------------------- #

def test_check_timeout_is_inconcluso_not_pass(monkeypatch):
    monkeypatch.setattr(check_hsts, "fetch", _fetch_raising(httpx.TimeoutException("slow")))
    r = asyncio.run(check_hsts.check("https://x.com"))
    assert r.status == Status.INCONCLUSO  # nunca PASS falso


def test_check_connect_error_is_inconcluso(monkeypatch):
    monkeypatch.setattr(check_hsts, "fetch", _fetch_raising(httpx.ConnectError("refused")))
    r = asyncio.run(check_hsts.check("https://x.com"))
    assert r.status == Status.INCONCLUSO


def test_check_infinite_redirect_is_inconcluso(monkeypatch):
    # httpx levanta TooManyRedirects quando o follow_redirects estoura o limite.
    monkeypatch.setattr(check_hsts, "fetch",
                        _fetch_raising(httpx.TooManyRedirects("loop")))
    r = asyncio.run(check_hsts.check("https://x.com"))
    assert r.status == Status.INCONCLUSO


def test_check_missing_header_is_fail(monkeypatch):
    monkeypatch.setattr(check_hsts, "fetch", _fetch_returning(_resp(200, headers={})))
    r = asyncio.run(check_hsts.check("https://x.com"))
    assert r.status == Status.FAIL


def test_check_strong_hsts_is_pass(monkeypatch):
    hdr = {"strict-transport-security": "max-age=63072000; includeSubDomains; preload"}
    monkeypatch.setattr(check_hsts, "fetch", _fetch_returning(_resp(200, headers=hdr)))
    r = asyncio.run(check_hsts.check("https://x.com"))
    assert r.status == Status.PASS


def test_check_malicious_content_type_header_is_inert(monkeypatch):
    # Content-Type com path traversal não deve causar acesso a arquivo nem crash.
    hdr = {"content-type": "../../../../etc/passwd"}
    monkeypatch.setattr(check_hsts, "fetch", _fetch_returning(_resp(200, headers=hdr)))
    r = asyncio.run(check_hsts.check("https://x.com"))
    assert r.status == Status.FAIL  # sem HSTS; o Content-Type é ignorado, sem efeito colateral


# --------------------------------------------------------------------------- #
# content_guard — checks Tipo B nunca dão PASS falso (KL-94)
# --------------------------------------------------------------------------- #

def test_content_guard_5xx_is_inconcluso():
    g = content_guard(_resp(503, text="x" * 500), "T", Severity.MEDIA)
    assert g is not None and g.status == Status.INCONCLUSO


def test_content_guard_empty_body_is_inconcluso():
    g = content_guard(_resp(200, text=""), "T", Severity.MEDIA)
    assert g is not None and g.status == Status.INCONCLUSO


def test_content_guard_good_body_is_none():
    g = content_guard(_resp(200, text="a" * 500), "T", Severity.MEDIA)
    assert g is None  # seguro para analisar


def test_content_guard_survives_mixed_encoding_body():
    # bytes latin-1 declarados como utf-8 → .text não deve crashar o guard.
    resp = httpx.Response(200, content="café ção".encode("latin-1"),
                          headers={"content-type": "text/html; charset=utf-8"},
                          request=httpx.Request("GET", "https://x.com"))
    g = content_guard(resp, "T", Severity.MEDIA)  # não levanta
    assert g is None or g.status == Status.INCONCLUSO


# --------------------------------------------------------------------------- #
# Robustez do parser de HTML (extração usada no enrich do scan)
# --------------------------------------------------------------------------- #

def test_parser_survives_malformed_html():
    out = _collect_emails("<html><div><p>contato@x.com.br</p></html")
    assert "contato@x.com.br" in out  # extrai o que dá, sem crash


def test_parser_survives_empty_and_garbage():
    assert _collect_emails("") == []
    assert _collect_emails("\x00\xff<<<>>>não é html") == []


def test_parser_survives_large_html():
    # 500 KB de lixo + 1 e-mail no fim → completa sem estourar (bounded).
    html = ("<div>x</div>" * 40000) + "<a href='mailto:achou@x.com.br'>c</a>"
    out = _collect_emails(html)
    assert "achou@x.com.br" in out


def test_ranked_emails_prefers_site_domain():
    emails = ["geral@gmail.com", "contato@site.com.br"]
    ranked = _ranked_emails(emails, "site.com.br")
    assert ranked[0] == "contato@site.com.br"  # mesmo domínio na frente


# --------------------------------------------------------------------------- #
# Um check ruim NÃO derruba o scan (contrato do runner)
# --------------------------------------------------------------------------- #

async def _gate_ok(target):
    return None


def test_one_raising_check_becomes_inconcluso(monkeypatch):
    async def _boom(url):
        raise RuntimeError("boom")

    async def _ok(url):
        return CheckResult(name="ok", status=Status.PASS, severity=Severity.BAIXA)

    monkeypatch.setattr(runner, "_accessibility_gate", _gate_ok)
    monkeypatch.setattr(runner, "ALL_CHECKS", [("check_01", _boom), ("check_02", _ok)])
    report = asyncio.run(runner.run_scan("https://x.com"))
    by = {r.check_id or r.name: r.status for r in report.results}
    assert Status.INCONCLUSO in by.values() and Status.PASS in by.values()
    assert report.status == "ok"  # o scan como um todo NÃO falha


# --------------------------------------------------------------------------- #
# SSL — erro de certificado vira FAIL (nunca crash) e o scan segue
# --------------------------------------------------------------------------- #

def test_ssl_bad_url_is_inconcluso():
    r = asyncio.run(check_ssl.check("not-a-url"))
    assert r.status == Status.INCONCLUSO  # host não extraível → neutro


def test_ssl_invalid_cert_is_fail(monkeypatch):
    fail = CheckResult(name="Certificado SSL válido", status=Status.FAIL,
                       severity=Severity.CRITICA, evidence="Certificado inválido: expirado.")
    monkeypatch.setattr(check_ssl, "_inspect_cert", lambda host, port: fail)
    r = asyncio.run(check_ssl.check("https://expired.example.com"))
    assert r.status == Status.FAIL  # erro de cert = FAIL, não crash
