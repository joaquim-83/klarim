"""KL-154 — SPF/DKIM/DMARC importados do scanner para o Security Gate.

Cobre: o adaptador `CheckResult`→`Result`, o check `email_security` (com os checks do scanner
mockados — hermético, sem DNS real), o registro no engine, o gate por plano (Free não roda,
Pro roda) e o agrupamento Surface/Deep dos formatters."""
from __future__ import annotations

import asyncio
import json

from scanner.checks.base import CheckResult
from security_gate.checks.email_security import check_email_security
from security_gate.checks.scanner_adapter import adapt_check_result
from security_gate.engine import _DEFAULT_ORDER, run_all
from security_gate.formatters import format_json, format_terminal
from security_gate.models import GateReport, Result, Severity, Status


def _run(coro):
    return asyncio.run(coro)


def _cr(status, severity="ALTA", evidence="", name="Check"):
    return CheckResult(name=name, status=status, severity=severity, evidence=evidence)


# =========================================================================== #
# 1. Adaptador CheckResult (scanner) → Result (Gate)
# =========================================================================== #

def test_adapt_pass_alta():
    r = adapt_check_result(_cr("PASS", "ALTA", "SPF ok"), "spf")
    assert r.status == Status.PASS and r.severity == Severity.HIGH
    assert r.check == "spf" and r.category == "surface" and r.path == "/"
    assert r.detail == "SPF ok"


def test_adapt_fail_alta():
    r = adapt_check_result(_cr("FAIL", "ALTA", "SPF ausente"), "spf")
    assert r.status == Status.FAIL and r.severity == Severity.HIGH


def test_adapt_inconclusive_becomes_error():
    r = adapt_check_result(_cr("INCONCLUSO", "ALTA", "DNS falhou"), "spf")
    assert r.status == Status.ERROR


def test_adapt_severity_mapping():
    assert adapt_check_result(_cr("FAIL", "CRITICA"), "x").severity == Severity.CRITICAL
    assert adapt_check_result(_cr("FAIL", "ALTA"), "x").severity == Severity.HIGH
    assert adapt_check_result(_cr("FAIL", "MEDIA"), "x").severity == Severity.MEDIUM
    assert adapt_check_result(_cr("FAIL", "BAIXA"), "x").severity == Severity.LOW


def test_adapt_unknown_severity_defaults_medium():
    assert adapt_check_result(_cr("FAIL", "???"), "x").severity == Severity.MEDIUM


def test_adapt_unknown_status_defaults_error():
    assert adapt_check_result(_cr("WEIRD", "ALTA"), "x").status == Status.ERROR


def test_adapt_detail_falls_back_to_name():
    r = adapt_check_result(_cr("PASS", "ALTA", evidence="", name="DKIM"), "dkim")
    assert r.detail == "DKIM"


def test_adapt_custom_category():
    assert adapt_check_result(_cr("PASS"), "dmarc", "email").category == "email"


# =========================================================================== #
# 2. check_email_security (checks do scanner mockados)
# =========================================================================== #

def _patch(monkeypatch, spf=None, dkim=None, dmarc=None):
    """Mocka os 3 checks do scanner (SEMPRE os três — deixar algum real bateria em DNS de verdade
    e travaria o teste). Cada arg é um CheckResult a devolver ou uma exceção a levantar; ausente →
    um PASS inócuo (mantém a suíte hermética/offline)."""
    default = _cr("PASS", "BAIXA", "mock")
    for path, cr in (("scanner.checks.check_21_spf", spf if spf is not None else default),
                     ("scanner.checks.check_22_dkim", dkim if dkim is not None else default),
                     ("scanner.checks.check_23_dmarc", dmarc if dmarc is not None else default)):
        async def _fake(url, _cr=cr):
            if isinstance(_cr, Exception):
                raise _cr
            return _cr
        monkeypatch.setattr(f"{path}.check", _fake)


def _find(results, check):
    return next(r for r in results if r.check == check)


def test_email_security_returns_three(monkeypatch):
    _patch(monkeypatch, spf=_cr("PASS"), dkim=_cr("PASS", "MEDIA"), dmarc=_cr("PASS"))
    res = _run(check_email_security(None, "https://x.test"))
    assert [r.check for r in res] == ["spf", "dkim", "dmarc"]
    assert all(r.category == "surface" for r in res)


def test_spf_present_pass(monkeypatch):
    _patch(monkeypatch, spf=_cr("PASS", "ALTA", "SPF presente"))
    assert _find(_run(check_email_security(None, "https://x.test")), "spf").status == Status.PASS


def test_spf_absent_fail_high(monkeypatch):
    _patch(monkeypatch, spf=_cr("FAIL", "ALTA", "SPF ausente"))
    r = _find(_run(check_email_security(None, "https://x.test")), "spf")
    assert r.status == Status.FAIL and r.severity == Severity.HIGH


def test_dmarc_present_pass(monkeypatch):
    _patch(monkeypatch, dmarc=_cr("PASS", "ALTA", "DMARC p=reject"))
    assert _find(_run(check_email_security(None, "https://x.test")), "dmarc").status == Status.PASS


def test_dmarc_absent_fail_high(monkeypatch):
    _patch(monkeypatch, dmarc=_cr("FAIL", "ALTA", "DMARC ausente"))
    r = _find(_run(check_email_security(None, "https://x.test")), "dmarc")
    assert r.status == Status.FAIL and r.severity == Severity.HIGH


def test_dkim_present_pass(monkeypatch):
    _patch(monkeypatch, dkim=_cr("PASS", "MEDIA", "DKIM presente"))
    assert _find(_run(check_email_security(None, "https://x.test")), "dkim").status == Status.PASS


def test_scanner_check_raises_is_graceful(monkeypatch):
    # Um check que estoura vira ERROR (INFO) isolado — nunca derruba os demais.
    _patch(monkeypatch, spf=RuntimeError("boom"), dkim=_cr("PASS", "MEDIA"), dmarc=_cr("PASS"))
    res = _run(check_email_security(None, "https://x.test"))
    spf = _find(res, "spf")
    assert spf.status == Status.ERROR and spf.severity == Severity.INFO
    assert _find(res, "dkim").status == Status.PASS  # os outros seguem normalmente


def test_scanner_import_failure_is_graceful(monkeypatch):
    # Se o scanner sumir/mudar de interface, o import lazy falha → 3 ERROR, sem crash.
    def _boom(_path):
        raise ImportError("scanner indisponível")
    monkeypatch.setattr("security_gate.checks.email_security.importlib.import_module", _boom)
    res = _run(check_email_security(None, "https://x.test"))
    assert len(res) == 3
    assert all(r.status == Status.ERROR and r.severity == Severity.INFO for r in res)


# =========================================================================== #
# 3. Engine + gate por plano
# =========================================================================== #

def test_email_security_in_default_order():
    assert "email_security" in _DEFAULT_ORDER


def test_run_all_includes_email_security(monkeypatch):
    _patch(monkeypatch, spf=_cr("FAIL", "ALTA", "SPF ausente"),
           dkim=_cr("PASS", "MEDIA"), dmarc=_cr("PASS"))
    report = _run(run_all("https://x.test", checks=["email_security"]))
    checks = {r.check for r in report.results}
    assert {"spf", "dkim", "dmarc"} <= checks
    assert report.error is None


def test_plan_gating_free_vs_pro():
    from api.gate import get_allowed_checks
    free = {"checks_allowed": ["headers", "ssl", "exposure", "https_redirect"]}
    pro = {"checks_allowed": ["headers", "ssl", "exposure", "https_redirect", "credentials",
                              "cors", "cookies", "api", "infrastructure", "email_security"]}
    assert "email_security" not in get_allowed_checks(free)
    assert "email_security" in get_allowed_checks(pro)
    # ["all"] (Team/Enterprise) inclui o novo check automaticamente.
    assert "email_security" in get_allowed_checks({"checks_allowed": ["all"]})


# =========================================================================== #
# 4. Formatters — agrupamento Surface vs Deep
# =========================================================================== #

def _sample_report():
    return GateReport(url="https://x.test", results=[
        Result("header_csp", "headers", "/", Status.FAIL, Severity.HIGH, "CSP ausente"),
        Result("ssl_valid", "ssl", "/", Status.PASS, Severity.CRITICAL, "válido"),
        Result("spf", "surface", "/", Status.FAIL, Severity.HIGH, "SPF ausente"),
        Result("dmarc", "surface", "/", Status.PASS, Severity.HIGH, "DMARC p=reject"),
        Result("env_exposed", "exposure", "/", Status.PASS, Severity.CRITICAL, "nada exposto"),
        Result("cors_reflect", "cors", "/", Status.FAIL, Severity.CRITICAL, "reflete origin"),
    ])


def test_terminal_groups_surface_and_deep():
    out = format_terminal(_sample_report())
    assert "Surface (servidor + DNS)" in out
    assert "Deep (exposição + código)" in out
    # Surface aparece antes de Deep; o SPF (surface) antes do CORS (deep).
    assert out.index("Surface") < out.index("Deep")
    assert out.index("Spf") < out.index("Deep")
    assert out.index("Cors Reflect") > out.index("Deep")


def test_json_includes_surface_deep_summary():
    data = json.loads(format_json(_sample_report()))
    assert "summary" in data
    # Surface: header_csp + ssl_valid + spf + dmarc = 4 (headers/ssl/surface são surface).
    assert data["summary"]["surface"]["checks"] == 4
    assert data["summary"]["surface"]["fail"] == 2   # header_csp + spf
    assert data["summary"]["surface"]["pass"] == 2   # ssl_valid + dmarc
    # Deep: env_exposed + cors_reflect = 2.
    assert data["summary"]["deep"]["checks"] == 2
    assert data["summary"]["deep"]["fail"] == 1


def test_json_backward_compatible_fields():
    data = json.loads(format_json(_sample_report()))
    assert set(data) >= {"url", "score", "passed", "critical", "high", "medium",
                         "duration_ms", "results"}
