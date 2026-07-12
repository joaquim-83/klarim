"""Testes KL-38 — content analysis passivo (comentários, debug, redirect, senha). Offline."""

from __future__ import annotations

import asyncio

import httpx

from scanner.checks import (
    check_45_html_comments as com, check_46_debug_mode as dbg,
    check_47_open_redirect as red, check_48_password_fields as pw,
)
from scanner.checks.base import Status, Severity
from scanner.checks.classifications import classify

URL = "https://x.com.br"


def _run(coro):
    return asyncio.run(coro)


def _resp(text="", headers=None):
    return httpx.Response(200, headers=headers or {}, text=text,
                          request=httpx.Request("GET", URL + "/"))


def _patch(monkeypatch, mod, text="", headers=None):
    resp = _resp(text, headers)

    async def _f(url, method="GET", **kw):
        return resp
    monkeypatch.setattr(mod, "fetch", _f)


# --- 45: comentários HTML -------------------------------------------------- #

def test_comment_credential_fail_alta(monkeypatch):
    _patch(monkeypatch, com, "<html><!-- password: admin123 --></html>")
    r = _run(com.check(URL))
    assert r.status == Status.FAIL and r.severity == Severity.ALTA


def test_comment_todo_security_fail_media(monkeypatch):
    _patch(monkeypatch, com, "<html><!-- TODO: fix XSS in search --></html>")
    r = _run(com.check(URL))
    assert r.status == Status.FAIL and r.severity == Severity.MEDIA


def test_comment_internal_ip_fail_media(monkeypatch):
    _patch(monkeypatch, com, "<html><!-- backend 10.0.1.42 --></html>")
    r = _run(com.check(URL))
    assert r.status == Status.FAIL and r.severity == Severity.MEDIA


def test_comment_copyright_is_safe(monkeypatch):
    _patch(monkeypatch, com, "<html><!-- © 2026 Company Ltda --></html>")
    assert _run(com.check(URL)).status == Status.PASS


def test_comment_none_pass(monkeypatch):
    _patch(monkeypatch, com, "<html><body>sem comentários</body></html>")
    assert _run(com.check(URL)).status == Status.PASS


# --- 46: debug mode -------------------------------------------------------- #

def test_debug_stack_trace_fail_alta(monkeypatch):
    _patch(monkeypatch, dbg, "<pre>Traceback (most recent call last): ...</pre>")
    r = _run(dbg.check(URL))
    assert r.status == Status.FAIL and r.severity == Severity.ALTA


def test_debug_php_error_fail_alta(monkeypatch):
    _patch(monkeypatch, dbg, "Fatal error: undefined in /var/www/html/x.php on line 10")
    assert _run(dbg.check(URL)).status == Status.FAIL


def test_debug_laravel_whoops_fail(monkeypatch):
    _patch(monkeypatch, dbg, "<h1>Whoops, looks like something went wrong.</h1>")
    assert _run(dbg.check(URL)).status == Status.FAIL


def test_debug_clean_pass(monkeypatch):
    _patch(monkeypatch, dbg, "<html><body>tudo certo</body></html>")
    assert _run(dbg.check(URL)).status == Status.PASS


def test_debug_headers_fail_media(monkeypatch):
    _patch(monkeypatch, dbg, "<html>ok</html>", headers={"X-Debug-Token": "abc123"})
    r = _run(dbg.check(URL))
    assert r.status == Status.FAIL and r.severity == Severity.MEDIA


def test_debug_ignores_script_content(monkeypatch):
    # "Traceback" dentro de <script> não deve disparar (strip de script/style).
    _patch(monkeypatch, dbg, "<script>var s='Traceback (most recent call last)';</script>")
    assert _run(dbg.check(URL)).status == Status.PASS


# --- 47: open redirect ----------------------------------------------------- #

def test_redirect_pattern_fail_baixa(monkeypatch):
    _patch(monkeypatch, red, '<a href="/login?redirect_to=/painel">entrar</a>')
    r = _run(red.check(URL))
    assert r.status == Status.FAIL and r.severity == Severity.BAIXA


def test_redirect_many_fail_media(monkeypatch):
    links = "".join(f'<a href="/p?next=/{i}">x</a>' for i in range(6))
    _patch(monkeypatch, red, links)
    r = _run(red.check(URL))
    assert r.status == Status.FAIL and r.severity == Severity.MEDIA


def test_redirect_none_pass(monkeypatch):
    _patch(monkeypatch, red, '<a href="/sobre">sobre</a>')
    assert _run(red.check(URL)).status == Status.PASS


# --- 48: password fields --------------------------------------------------- #

def test_password_without_autocomplete_fail(monkeypatch):
    _patch(monkeypatch, pw, '<form><input type="password" name="pass"></form>')
    r = _run(pw.check(URL))
    assert r.status == Status.FAIL and r.severity == Severity.BAIXA


def test_password_with_autocomplete_pass(monkeypatch):
    _patch(monkeypatch, pw, '<input type="password" name="p" autocomplete="new-password">')
    assert _run(pw.check(URL)).status == Status.PASS


def test_password_absent_pass(monkeypatch):
    _patch(monkeypatch, pw, '<input type="text" name="q"><input type="email">')
    assert _run(pw.check(URL)).status == Status.PASS


# --- 16: classificações ---------------------------------------------------- #

def test_content_checks_classified():
    assert classify("check_45_html_comments") == ("A01:2025 Broken Access Control", "CWE-615", "Art. 46")
    assert classify("check_46_debug_mode") == ("A05:2025 Security Misconfiguration", "CWE-489", "Art. 46")
    assert classify("check_47_open_redirect") == ("A01:2025 Broken Access Control", "CWE-601", "Art. 46")
    assert classify("check_48_password_fields") == ("A04:2025 Insecure Design", "CWE-522", "Art. 46, Art. 11")
