"""KL-141 Prompt 3 — CLI + config YAML + check de API security + allowlist + formatters.
Offline: `run_all` mockado na CLI; MockTransport nos checks; YAML em tmp_path."""
from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
from pathlib import Path

import httpx
import pytest

from security_gate.checks.api_security import check_api_security
from security_gate.checks.exposure import check_exposure
from security_gate.config import GateConfig, load_config
from security_gate.formatters import format_json, format_terminal
from security_gate.models import GateReport, Result, Severity, Status


def _rep(*results, url="https://x.test", error=None):
    r = GateReport(url=url, results=list(results))
    r.error = error
    return r


def _R(check, cat, status, sev, detail="d"):
    return Result(check, cat, "/", status, sev, detail)


# =========================================================================== #
# CLI (scripts/security_gate.py carregado via importlib)
# =========================================================================== #
_CLI_PATH = Path(__file__).resolve().parent.parent / "scripts" / "security_gate.py"


def _load_cli():
    spec = importlib.util.spec_from_file_location("gate_cli", _CLI_PATH)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


cli = _load_cli()
_NOCFG = "/tmp/kl141_nonexistent_config.yml"   # força os defaults + args da CLI


def _mock_run(monkeypatch, report, captured=None):
    async def _r(**kw):
        if captured is not None:
            captured.update(kw)
        return report
    monkeypatch.setattr(cli, "run_all", _r)


def test_cli_exit_0_when_pass(monkeypatch):
    _mock_run(monkeypatch, _rep(_R("h", "headers", Status.PASS, Severity.HIGH)))
    assert cli.main(["https://x.test", "--config", _NOCFG]) == 0


def test_cli_fail_on_high_with_high_finding_exit_1(monkeypatch):
    _mock_run(monkeypatch, _rep(_R("x", "headers", Status.FAIL, Severity.HIGH)))
    assert cli.main(["https://x.test", "--fail-on", "high", "--config", _NOCFG]) == 1


def test_cli_fail_on_critical_with_high_finding_exit_0(monkeypatch):
    _mock_run(monkeypatch, _rep(_R("x", "headers", Status.FAIL, Severity.HIGH)))
    assert cli.main(["https://x.test", "--fail-on", "critical", "--config", _NOCFG]) == 0


def test_cli_offline_exit_2(monkeypatch):
    _mock_run(monkeypatch, _rep(error="ConnectError('offline')"))
    assert cli.main(["https://x.test", "--config", _NOCFG]) == 2


def test_cli_checks_filter_passed_through(monkeypatch):
    captured = {}
    _mock_run(monkeypatch, _rep(_R("h", "headers", Status.PASS, Severity.HIGH)), captured)
    cli.main(["https://x.test", "--checks", "headers,ssl", "--config", _NOCFG])
    assert captured["checks"] == ["headers", "ssl"]


def test_cli_json_output(monkeypatch, capsys):
    _mock_run(monkeypatch, _rep(_R("h", "headers", Status.FAIL, Severity.HIGH, "sem CSP")))
    cli.main(["https://x.test", "--json", "--fail-on", "critical", "--config", _NOCFG])
    data = json.loads(capsys.readouterr().out)
    assert data["url"] == "https://x.test" and data["results"][0]["check"] == "h"


def test_cli_quiet_omits_pass(monkeypatch, capsys):
    _mock_run(monkeypatch, _rep(
        _R("h", "headers", Status.PASS, Severity.HIGH, "hsts ok"),
        _R("c", "headers", Status.FAIL, Severity.HIGH, "csp ausente")))
    cli.main(["https://x.test", "--quiet", "--fail-on", "critical", "--config", _NOCFG])
    out = capsys.readouterr().out
    assert "csp ausente" in out and "hsts ok" not in out


def test_cli_has_failure_helper():
    rep = _rep(_R("x", "ssl", Status.FAIL, Severity.MEDIUM))
    assert cli._has_failure(rep, "medium") is True
    assert cli._has_failure(rep, "high") is False   # MEDIUM < HIGH


# =========================================================================== #
# Config YAML
# =========================================================================== #

def _write(tmp_path, text):
    p = tmp_path / "security-gate.yml"
    p.write_text(text, encoding="utf-8")
    return str(p)


def test_config_allowlist(tmp_path):
    cfg = load_config(_write(tmp_path,
                             "checks:\n  exposure:\n    allowlist:\n      - /docs\n      - /adminer\n"))
    assert cfg.exposure_allowlist == ["/docs", "/adminer"]


def test_config_disable_check(tmp_path):
    cfg = load_config(_write(tmp_path, "checks:\n  ssl:\n    enabled: false\n"))
    assert "ssl" not in cfg.checks and "headers" in cfg.checks


def test_config_protected_endpoints(tmp_path):
    cfg = load_config(_write(tmp_path,
                             "checks:\n  api:\n    root_path: /api/\n    protected_endpoints:\n"
                             "      - /api/admin/\n      - /mcp/\n"))
    assert cfg.protected_endpoints == ["/api/admin/", "/mcp/"] and cfg.api_root_path == "/api/"


def test_config_missing_yaml_defaults():
    cfg = load_config("/tmp/kl141_definitely_missing.yml")
    assert cfg.fail_on == "critical" and "credentials" in cfg.checks


def test_config_cli_overrides_yaml(tmp_path):
    path = _write(tmp_path, "fail_on: critical\ntimeout: 60\n")
    args = argparse.Namespace(url="https://o.test", fail_on="high", timeout=15, checks="headers")
    cfg = load_config(path, args)
    assert cfg.fail_on == "high" and cfg.timeout == 15 and cfg.target == "https://o.test"
    assert cfg.checks == ["headers"]


def test_config_ssl_min_days_and_hsts(tmp_path):
    cfg = load_config(_write(tmp_path,
                             "checks:\n  ssl:\n    min_days: 30\n  headers:\n    hsts_min_age: 15768000\n"))
    assert cfg.ssl_min_days == 30 and cfg.hsts_min_age == 15768000


# =========================================================================== #
# API security
# =========================================================================== #

class _Api:
    def __init__(self, routes):
        self.routes = routes   # path -> (status, body)

    def __call__(self, request):
        st, body = self.routes.get(request.url.path, (404, ""))
        return httpx.Response(st, text=body)


async def _api(routes, config):
    async with httpx.AsyncClient(transport=httpx.MockTransport(_Api(routes))) as c:
        return await check_api_security(c, "https://x.test", config)


def _find(res, check):
    return next(r for r in res if r.check == check)


def test_api_root_leaks_fail_high():
    cfg = GateConfig(api_root_path="/api/", protected_endpoints=[])
    res = asyncio.run(_api({"/api/": (200, '{"endpoints": ["/api/admin"]}')}, cfg))
    r = _find(res, "api_root_leaks")
    assert r.status == Status.FAIL and r.severity == Severity.HIGH


def test_api_root_clean_pass():
    cfg = GateConfig(api_root_path="/api/", protected_endpoints=[])
    res = asyncio.run(_api({"/api/": (200, '{"name": "Klarim API", "status": "ok"}')}, cfg))
    assert _find(res, "api_root_clean").status == Status.PASS


def test_api_protected_endpoint_401_pass():
    cfg = GateConfig(api_root_path="/api/", protected_endpoints=["/api/admin/"])
    res = asyncio.run(_api({"/api/": (200, '{"name":"x"}'), "/api/admin/": (401, "")}, cfg))
    r = _find(res, "api_protected")
    assert r.status == Status.PASS and r.severity == Severity.CRITICAL


def test_api_protected_endpoint_200_fail_critical():
    cfg = GateConfig(api_root_path="/api/", protected_endpoints=["/api/admin/"])
    res = asyncio.run(_api({"/api/": (200, '{"name":"x"}'), "/api/admin/": (200, "leaked")}, cfg))
    r = _find(res, "api_unprotected")
    assert r.status == Status.FAIL and r.severity == Severity.CRITICAL


# =========================================================================== #
# Allowlist de exposição (resolve os falsos positivos de SPA do Prompt 1)
# =========================================================================== #

class _Exp:
    def __init__(self, status_map):
        self.status_map = status_map

    def __call__(self, request):
        return httpx.Response(self.status_map.get(request.url.path, 404))


async def _exposure(status_map, config=None):
    async with httpx.AsyncClient(transport=httpx.MockTransport(_Exp(status_map)),
                                 follow_redirects=True) as c:
        return await check_exposure(c, "https://x.test", config)


def test_allowlisted_path_200_is_pass():
    cfg = GateConfig(exposure_allowlist=["/adminer"])
    res = asyncio.run(_exposure({"/adminer": 200}, cfg))
    # /adminer é allowlisted → pulado; nenhum outro path do grupo está 200 → grupo PASS.
    assert _find(res, "admin_panel_exposed").status == Status.PASS


def test_non_allowlisted_path_200_is_fail():
    res = asyncio.run(_exposure({"/adminer": 200}, GateConfig(exposure_allowlist=[])))
    assert _find(res, "admin_panel_exposed").status == Status.FAIL


# --- Content-Type guard: recurso não-HTML devolvendo text/html = fallback de SPA ------------- #
class _ExpCT:
    def __init__(self, routes):   # path -> (status, content_type)
        self.routes = routes

    def __call__(self, request):
        st, ct = self.routes.get(request.url.path, (404, "text/plain"))
        return httpx.Response(st, headers={"content-type": ct})


async def _exposure_ct(routes):
    async with httpx.AsyncClient(transport=httpx.MockTransport(_ExpCT(routes)),
                                 follow_redirects=True) as c:
        return await check_exposure(c, "https://x.test", GateConfig(exposure_allowlist=[]))


def test_source_map_html_is_spa_fallback_pass():
    # /main.js.map devolvendo text/html = fallback de SPA → NÃO é source map real → PASS.
    res = asyncio.run(_exposure_ct({"/main.js.map": (200, "text/html; charset=utf-8")}))
    assert _find(res, "source_maps_exposed").status == Status.PASS


def test_source_map_json_is_real_exposure_fail():
    # /main.js.map devolvendo application/json = source map REAL exposto → FAIL (guard não suprime).
    res = asyncio.run(_exposure_ct({"/main.js.map": (200, "application/json")}))
    assert _find(res, "source_maps_exposed").status == Status.FAIL


def test_config_yaml_html_is_spa_fallback_pass():
    res = asyncio.run(_exposure_ct({"/config/database.yml": (200, "text/html")}))
    assert _find(res, "cms_config_exposed").status == Status.PASS


def test_env_html_is_spa_fallback_pass():
    res = asyncio.run(_exposure_ct({"/.env": (200, "text/html")}))
    assert _find(res, "env_exposed").status == Status.PASS


def test_env_plaintext_is_real_exposure_fail():
    # .env de verdade vaza como text/plain → o guard (só text/html) não suprime → FAIL.
    res = asyncio.run(_exposure_ct({"/.env": (200, "text/plain")}))
    assert _find(res, "env_exposed").status == Status.FAIL


# =========================================================================== #
# Formatters
# =========================================================================== #

def _sample():
    return _rep(
        _R("header_csp", "headers", Status.FAIL, Severity.HIGH, "CSP ausente"),
        _R("ssl_valid", "ssl", Status.PASS, Severity.CRITICAL, "válido"))


def test_format_terminal_has_icons_score_verdict():
    out = format_terminal(_sample())
    assert "❌" in out and "✅" in out
    assert "Score:" in out and ("PASSED" in out or "FAILED" in out)
    assert "https://x.test" in out


def test_format_terminal_quiet_omits_pass():
    out = format_terminal(_sample(), quiet=True)
    assert "CSP ausente" in out and "válido" not in out


def test_format_json_parseable_all_fields():
    data = json.loads(format_json(_sample()))
    assert set(data) >= {"url", "score", "passed", "critical", "high", "medium",
                         "duration_ms", "results"}
    assert data["results"][0]["severity"] == "high"
