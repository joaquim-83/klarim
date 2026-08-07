"""KL-151 Prompt 2/4 — API REST de scan + CLI + MCP tools. Offline.

O scan roda no SERVIDOR: `run_all` (a engine) é mockado nos testes de endpoint para não fazer
rede; o foco é a autorização (API key + domínio verificado), o enforcement de plano (checks
bloqueados, limite de scans/dia) e a persistência do run. O CLI é testado com um client fake."""
from __future__ import annotations

import asyncio
import importlib.util
import os

import pytest
from fastapi.testclient import TestClient

import api.main as m
from api import gate as g
from security_gate.models import GateReport, Result, Severity, Status

SECRET = "k" * 64


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


_FREE = {"id": 1, "name": "Free", "slug": "free", "scans_per_day": 5, "max_domains": 1,
         "checks_allowed": ["headers", "ssl", "exposure", "https_redirect"],
         "scan_third_party": False}
_PRO = {"id": 2, "name": "Pro", "slug": "pro", "scans_per_day": 50, "max_domains": 10,
        "checks_allowed": ["headers", "ssl", "exposure", "https_redirect", "credentials", "cors",
                           "cookies", "api", "infrastructure"], "scan_third_party": False}


class FakeStore:
    def __init__(self, plan=_FREE, verified=True):
        self.plan = plan
        self.keys = [{"id": 1, "account_id": 10, "key_prefix": "KLM_aaaa", "name": "d",
                      "is_active": True, "last_used_at": None}]
        self.hash_to_key = {}   # set by test helper
        self.projects = [{"id": 5, "account_id": 10, "name": "Acme", "url": "https://acme.com.br",
                          "domain": "acme.com.br", "verified": verified,
                          "verification_method": "dns_txt" if verified else None, "config": {},
                          "invited_by": None}]
        self.runs = []
        self.rid = 100

    async def get_gate_api_key_by_hash(self, key_hash):
        return self.hash_to_key.get(key_hash)

    async def touch_gate_api_key(self, key_id):
        for k in self.keys:
            if k["id"] == key_id:
                k["last_used_at"] = "now"

    async def get_account_gate_fields(self, account_id):
        return {"id": account_id, "email": "d@acme.com", "account_type": "developer",
                "gate_plan_id": self.plan["id"], "gate_trial_started_at": None,
                "gate_trial_ends_at": None}

    async def get_gate_plan(self, plan_id):
        return dict(self.plan) if plan_id == self.plan["id"] else None

    async def get_gate_plan_by_slug(self, slug):
        return dict(self.plan) if slug == self.plan["slug"] else (dict(_PRO) if slug == "pro" else None)

    async def get_gate_project_by_domain(self, account_id, domain):
        return next((dict(p) for p in self.projects
                     if p["account_id"] == account_id and p["domain"] == domain), None)

    async def count_gate_runs_today(self, account_id):
        return sum(1 for r in self.runs if r["account_id"] == account_id)

    async def insert_gate_audit(self, **kw):
        self.audits = getattr(self, "audits", [])
        self.audits.append(kw)

    async def create_gate_run(self, project_id, account_id, url, score, passed, fail_on,
                              duration_ms, results, checks_run, checks_blocked, metadata=None):
        r = {"id": self.rid, "project_id": project_id, "account_id": account_id, "url": url,
             "score": score, "passed": passed, "fail_on": fail_on, "duration_ms": duration_ms,
             "results": results, "checks_run": checks_run, "checks_blocked": checks_blocked,
             "metadata": metadata or {}, "created_at": "2026-08-08T00:00:00Z"}
        self.runs.append(r)
        self.rid += 1
        return r["id"]

    async def list_gate_runs(self, account_id=None, project_id=None, limit=20):
        rows = [r for r in self.runs
                if (account_id is None or r["account_id"] == account_id)
                and (project_id is None or r["project_id"] == project_id)]
        return [{k: v for k, v in r.items() if k != "results"} for r in rows[-limit:][::-1]]

    async def get_gate_run(self, run_id, account_id=None):
        return next((dict(r) for r in self.runs if r["id"] == run_id
                     and (account_id is None or r["account_id"] == account_id)), None)


def _register_key(store, full_key, account_id=10):
    store.hash_to_key[g._hash_key(full_key)] = {
        "id": 1, "account_id": account_id, "key_prefix": full_key[:8], "name": "d",
        "is_active": True, "last_used_at": None}


def _report(results):
    return GateReport(url="https://acme.com.br",
                      results=[Result(c, cat, "/", st, sev, "detalhe") for (c, cat, sev, st) in results],
                      duration_ms=1234)


@pytest.fixture
def store(monkeypatch):
    s = FakeStore()
    monkeypatch.setenv("JWT_SECRET", SECRET)
    monkeypatch.setattr(m, "get_target_store", lambda: s)
    monkeypatch.setattr(g, "get_target_store", lambda: s)
    monkeypatch.setattr(m, "_cache", None)   # sem Redis → enforce cai na contagem do banco
    return s


@pytest.fixture
def client(store):
    return TestClient(m.app, raise_server_exceptions=False)


def _mock_engine(monkeypatch, report):
    async def _fake_run_all(url, timeout=60, checks=None, config=None, deploy_ts=None):
        return report
    monkeypatch.setattr(g, "run_all", _fake_run_all)


# =========================================================================== #
# POST /gate/scan
# =========================================================================== #

def test_scan_ok_returns_score_and_results(client, store, monkeypatch):
    key = "KLM_" + "a" * 32
    _register_key(store, key)
    _mock_engine(monkeypatch, _report([
        ("header_hsts", "headers", Severity.HIGH, Status.FAIL),
        ("ssl_valid", "ssl", Severity.CRITICAL, Status.PASS)]))
    r = client.post("/gate/scan", json={"url": "https://acme.com.br", "fail_on": "critical",
                                        "metadata": {"commit": "abc123", "ci": "gh"}},
                    headers={"X-API-Key": key})
    assert r.status_code == 200
    body = r.json()
    assert body["score"] == 90 and body["passed"] is True    # 1 HIGH fail (-10); sem crítico → passa
    assert body["high"] == 1 and body["critical"] == 0
    assert len(body["results"]) == 2
    assert body["plan"] == "Free"
    # checks do plano Free (4) + bloqueados (14)
    assert body["checks_run"] == _FREE["checks_allowed"] and len(body["checks_blocked"]) == 14
    assert body["dashboard_url"].endswith(f"/gate/runs/{body['run_id']}")
    # run persistido com metadata
    assert store.runs[-1]["metadata"] == {"commit": "abc123", "ci": "gh"}
    assert store.runs[-1]["checks_blocked"] and store.runs[-1]["results"]


def test_scan_fail_on_high_flips_passed(client, store, monkeypatch):
    key = "KLM_" + "b" * 32
    _register_key(store, key)
    _mock_engine(monkeypatch, _report([("header_hsts", "headers", Severity.HIGH, Status.FAIL)]))
    # fail_on=critical → passa (só HIGH); fail_on=high → reprova
    assert client.post("/gate/scan", json={"url": "https://acme.com.br", "fail_on": "critical"},
                       headers={"X-API-Key": key}).json()["passed"] is True
    assert client.post("/gate/scan", json={"url": "https://acme.com.br", "fail_on": "high"},
                       headers={"X-API-Key": key}).json()["passed"] is False


def test_scan_no_key_401(client, store):
    assert client.post("/gate/scan", json={"url": "https://acme.com.br"}).status_code == 401


def test_scan_bad_key_401(client, store):
    assert client.post("/gate/scan", json={"url": "https://acme.com.br"},
                       headers={"X-API-Key": "nope"}).status_code == 401


def test_scan_unverified_domain_403(monkeypatch):
    s = FakeStore(verified=False)
    monkeypatch.setenv("JWT_SECRET", SECRET)
    monkeypatch.setattr(m, "get_target_store", lambda: s)
    monkeypatch.setattr(g, "get_target_store", lambda: s)
    monkeypatch.setattr(m, "_cache", None)
    key = "KLM_" + "c" * 32
    _register_key(s, key)
    c = TestClient(m.app, raise_server_exceptions=False)
    r = c.post("/gate/scan", json={"url": "https://acme.com.br"}, headers={"X-API-Key": key})
    assert r.status_code == 403 and "verific" in r.json()["detail"].lower()


def test_scan_unregistered_domain_403(client, store, monkeypatch):
    key = "KLM_" + "d" * 32
    _register_key(store, key)
    r = client.post("/gate/scan", json={"url": "https://outro.com"}, headers={"X-API-Key": key})
    assert r.status_code == 403


def test_scan_limit_exceeded_429(client, store, monkeypatch):
    key = "KLM_" + "e" * 32
    _register_key(store, key)
    _mock_engine(monkeypatch, _report([("ssl_valid", "ssl", Severity.CRITICAL, Status.PASS)]))
    # Free = 5/dia. 5 scans OK; o 6º estoura (sem Redis → contagem no banco).
    for i in range(5):
        assert client.post("/gate/scan", json={"url": "https://acme.com.br"},
                           headers={"X-API-Key": key}).status_code == 200
    r6 = client.post("/gate/scan", json={"url": "https://acme.com.br"}, headers={"X-API-Key": key})
    assert r6.status_code == 429


def test_scan_third_party_plan_scans_unverified(monkeypatch):
    ent = {"id": 4, "name": "Enterprise", "slug": "enterprise", "scans_per_day": -1,
           "max_domains": -1, "checks_allowed": ["all"], "scan_third_party": True}
    s = FakeStore(plan=ent, verified=False)
    monkeypatch.setenv("JWT_SECRET", SECRET)
    monkeypatch.setattr(m, "get_target_store", lambda: s)
    monkeypatch.setattr(g, "get_target_store", lambda: s)
    monkeypatch.setattr(m, "_cache", None)
    _mock_engine(monkeypatch, _report([("ssl_valid", "ssl", Severity.CRITICAL, Status.PASS)]))
    key = "KLM_" + "f" * 32
    _register_key(s, key)
    c = TestClient(m.app, raise_server_exceptions=False)
    r = c.post("/gate/scan", json={"url": "https://acme.com.br"}, headers={"X-API-Key": key})
    assert r.status_code == 200 and len(r.json()["checks_blocked"]) == 0   # Enterprise = todos


# =========================================================================== #
# GET /gate/runs + /gate/runs/{id}
# =========================================================================== #

def test_list_and_get_run(client, store, monkeypatch):
    key = "KLM_" + "g" * 32
    _register_key(store, key)
    _mock_engine(monkeypatch, _report([("ssl_valid", "ssl", Severity.CRITICAL, Status.PASS)]))
    rid = client.post("/gate/scan", json={"url": "https://acme.com.br"},
                      headers={"X-API-Key": key}).json()["run_id"]
    runs = client.get("/gate/runs", headers={"X-API-Key": key}).json()["runs"]
    assert len(runs) == 1 and "results" not in runs[0]      # sumário sem results
    detail = client.get(f"/gate/runs/{rid}", headers={"X-API-Key": key}).json()["run"]
    assert detail["id"] == rid and "results" in detail       # detalhe com results


def test_get_other_account_run_404(client, store, monkeypatch):
    key = "KLM_" + "h" * 32
    _register_key(store, key, account_id=10)
    store.runs.append({"id": 999, "account_id": 77, "project_id": 5, "url": "x", "results": []})
    assert client.get("/gate/runs/999", headers={"X-API-Key": key}).status_code == 404


# =========================================================================== #
# enforce_scan_limit — contador (fallback no banco sem Redis)
# =========================================================================== #

def test_enforce_scan_limit_unlimited(monkeypatch):
    class _S:
        async def count_gate_runs_today(self, a):
            return 10_000
    monkeypatch.setattr(g, "get_target_store", lambda: _S())
    monkeypatch.setattr(m, "_cache", None)
    _run(g.enforce_scan_limit(1, {"scans_per_day": -1}))   # -1 → nunca levanta


# =========================================================================== #
# CLI (client fake)
# =========================================================================== #

def _load_cli():
    path = os.path.join(os.path.dirname(__file__), "..", "scripts", "klarim_gate_cli.py")
    spec = importlib.util.spec_from_file_location("klarim_gate_cli", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _FakeClient:
    def __init__(self, result=None, exc=None):
        self._result = result
        self._exc = exc

    def __call__(self, api_key, base_url=None):
        return self

    def scan(self, url, fail_on="critical", timeout=60, metadata=None):
        if self._exc:
            raise self._exc
        return self._result

    def list_projects(self):
        return [{"domain": "acme.com.br", "verified": True, "verification_method": "dns_txt"}]

    def list_runs(self, project_id=None, limit=20):
        return [{"id": 100, "score": 90, "passed": True, "created_at": "2026-08-08T00:00:00",
                 "url": "https://acme.com.br"}]


_SCAN_RESULT = {"url": "https://acme.com.br", "score": 90, "passed": True, "duration_ms": 100,
                "critical": 0, "high": 1, "medium": 0, "plan": "Free",
                "results": [{"check": "header_hsts", "category": "headers", "severity": "high",
                             "status": "fail", "detail": "HSTS ausente"},
                            {"check": "ssl_valid", "category": "ssl", "severity": "critical",
                             "status": "pass", "detail": "ok"}],
                "checks_run": ["headers", "ssl"], "checks_blocked": ["credentials", "cors"],
                "dashboard_url": "https://klarim.net/dashboard/gate/runs/100"}


def test_cli_scan_passed_exit_0(monkeypatch, capsys):
    cli = _load_cli()
    monkeypatch.setattr(cli, "GateClient", _FakeClient(result=dict(_SCAN_RESULT)))
    rc = cli.main(["scan", "https://acme.com.br", "--api-key", "KLM_x"])
    out = capsys.readouterr().out
    assert rc == 0 and "Score: 90/100" in out and "PASSED" in out


def test_cli_scan_json(monkeypatch, capsys):
    cli = _load_cli()
    monkeypatch.setattr(cli, "GateClient", _FakeClient(result=dict(_SCAN_RESULT)))
    rc = cli.main(["scan", "https://acme.com.br", "--api-key", "KLM_x", "--json"])
    out = capsys.readouterr().out
    import json
    parsed = json.loads(out)
    assert rc == 0 and parsed["score"] == 90


def test_cli_scan_quiet_omits_pass(monkeypatch, capsys):
    cli = _load_cli()
    monkeypatch.setattr(cli, "GateClient", _FakeClient(result=dict(_SCAN_RESULT)))
    cli.main(["scan", "https://acme.com.br", "--api-key", "KLM_x", "--quiet"])
    out = capsys.readouterr().out
    assert "header_hsts" in out and "ssl_valid" not in out   # só o FAIL aparece


def test_cli_scan_failed_exit_1(monkeypatch, capsys):
    cli = _load_cli()
    failed = {**_SCAN_RESULT, "passed": False}
    monkeypatch.setattr(cli, "GateClient", _FakeClient(result=failed))
    rc = cli.main(["scan", "https://acme.com.br", "--api-key", "KLM_x"])
    assert rc == 1


def test_cli_no_api_key_exit_2(capsys, monkeypatch):
    cli = _load_cli()
    monkeypatch.delenv("KLARIM_API_KEY", raising=False)
    rc = cli.main(["scan", "https://acme.com.br"])
    assert rc == 2 and "API key" in capsys.readouterr().err


def test_cli_api_error_exit_2(monkeypatch):
    cli = _load_cli()
    monkeypatch.setattr(cli, "GateClient", _FakeClient(exc=RuntimeError("boom")))
    rc = cli.main(["scan", "https://acme.com.br", "--api-key", "KLM_x"])
    assert rc == 2


def test_cli_projects_and_runs(monkeypatch, capsys):
    cli = _load_cli()
    monkeypatch.setattr(cli, "GateClient", _FakeClient(result=dict(_SCAN_RESULT)))
    assert cli.main(["projects", "--api-key", "KLM_x"]) == 0
    assert "acme.com.br" in capsys.readouterr().out
    assert cli.main(["runs", "--api-key", "KLM_x"]) == 0


# =========================================================================== #
# MCP tools registradas
# =========================================================================== #

def test_mcp_gate_tools_registered():
    import mcp_server.tools  # noqa: F401 - registra as tools
    from mcp_server._base import mcp
    names = {t.name for t in _run(mcp.list_tools())}
    for t in ("list_gate_projects", "get_gate_project", "create_gate_project",
              "list_gate_runs", "get_gate_run"):
        assert t in names
