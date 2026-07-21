"""KL-94 — gate de acessibilidade no runner + guard universal dos checks Tipo B. Um site
inexistente/offline NÃO recebe score; um check que não conseguiu verificar o conteúdo (5xx/
corpo vazio) retorna INCONCLUSO, não PASS falso. Offline (mocka DNS/HTTP)."""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

import scanner.runner as runner
from scanner.checks import dns_util
import scanner.checks.base as base
from scanner.checks.base import content_guard, Status, Severity


class _Resp:
    def __init__(self, code=200, text="x" * 300):
        self.status_code = code
        self.text = text
        self.headers = {}
        self.url = "https://x.com.br/"


# =========================================================================== #
# 1. content_guard (puro)
# =========================================================================== #

def test_content_guard_5xx_inconcluso():
    g = content_guard(_Resp(503, "x" * 300), "n", Severity.MEDIA)
    assert g is not None and g.status == Status.INCONCLUSO


def test_content_guard_empty_inconcluso():
    g = content_guard(_Resp(200, "   "), "n", Severity.MEDIA)
    assert g is not None and g.status == Status.INCONCLUSO


def test_content_guard_ok_passes_through():
    assert content_guard(_Resp(200, "x" * 300), "n", Severity.MEDIA) is None
    # 404 com conteúdo real NÃO bloqueia (o check decide caso a caso)
    assert content_guard(_Resp(404, "x" * 300), "n", Severity.MEDIA) is None


# =========================================================================== #
# 2. Check Tipo B — 5xx e corpo vazio → INCONCLUSO (não PASS falso)
# =========================================================================== #

@pytest.mark.asyncio
async def test_check_tipo_b_5xx_is_inconcluso(monkeypatch):
    from scanner.checks import check_45_html_comments as c

    async def fake_fetch(*a, **k):
        return _Resp(503, "x" * 300)
    monkeypatch.setattr(c, "fetch", fake_fetch)
    res = await c.check("https://x.com.br")
    assert res.status == Status.INCONCLUSO


@pytest.mark.asyncio
async def test_check_tipo_b_empty_is_inconcluso(monkeypatch):
    from scanner.checks import check_47_open_redirect as c

    async def fake_fetch(*a, **k):
        return _Resp(200, "")            # corpo vazio → nada a analisar
    monkeypatch.setattr(c, "fetch", fake_fetch)
    res = await c.check("https://x.com.br")
    assert res.status == Status.INCONCLUSO


# =========================================================================== #
# 3. Gate de acessibilidade no run_scan
# =========================================================================== #

def _bypass_checks(monkeypatch):
    """Neutraliza os checks/privacy: aqui só testamos o GATE, não os checks."""
    import scanner.privacy_checks as pc

    async def _noop(url):
        return None
    monkeypatch.setattr(pc, "scan_privacy", _noop)
    monkeypatch.setattr(runner, "ALL_CHECKS", [])
    monkeypatch.setattr(runner, "FREE_CHECKS", [])


def test_gate_nxdomain(monkeypatch):
    _bypass_checks(monkeypatch)
    monkeypatch.setattr(dns_util, "resolve_host_status", lambda name, timeout=5.0: "nxdomain")
    report = asyncio.run(runner.run_scan("https://naoexiste.com.br"))
    assert report.status == "domain_not_found"
    assert report.score is None and report.results == []
    assert "DNS" in report.error_detail


def test_gate_dns_error(monkeypatch):
    _bypass_checks(monkeypatch)
    monkeypatch.setattr(dns_util, "resolve_host_status", lambda name, timeout=5.0: "error")
    report = asyncio.run(runner.run_scan("https://x.com.br"))
    assert report.status == "dns_error" and report.score is None


def test_gate_unreachable(monkeypatch):
    _bypass_checks(monkeypatch)
    monkeypatch.setattr(dns_util, "resolve_host_status", lambda name, timeout=5.0: "found")

    async def _boom(*a, **k):
        raise ConnectionError("refused")
    monkeypatch.setattr(base, "fetch", _boom)
    report = asyncio.run(runner.run_scan("https://x.com.br"))
    assert report.status == "unreachable" and report.score is None


def test_gate_ok_runs_checks(monkeypatch):
    # DNS resolve + HTTP responde → o gate passa e os checks (aqui 1 mock) rodam.
    import scanner.privacy_checks as pc

    async def _noop(url):
        return None
    monkeypatch.setattr(pc, "scan_privacy", _noop)
    monkeypatch.setattr(dns_util, "resolve_host_status", lambda name, timeout=5.0: "found")

    async def _ok_fetch(*a, **k):
        return _Resp(200, "x" * 300)
    monkeypatch.setattr(base, "fetch", _ok_fetch)

    async def _one(url):
        return base.CheckResult(name="c", status=Status.PASS, severity=Severity.BAIXA, evidence="ok")
    monkeypatch.setattr(runner, "ALL_CHECKS", [("check_01_x", _one)])
    report = asyncio.run(runner.run_scan("https://x.com.br", full=True))
    assert report.status == "ok" and report.score is not None and len(report.results) == 1


def test_report_status_roundtrip():
    r = runner.ScanReport(url="https://x", started_at="a", finished_at="b", duration_s=1.0,
                          status="unreachable", error_detail="fora do ar")
    d = r.to_dict()
    assert d["status"] == "unreachable" and d["error_detail"] == "fora do ar"
    r2 = runner.ScanReport.from_dict(d)
    assert r2.status == "unreachable" and r2.error_detail == "fora do ar"


# =========================================================================== #
# 4. API — /scan/result e /scan/summary devolvem status != ok (200)
# =========================================================================== #

@pytest.fixture
def client(monkeypatch):
    import api.main as m

    async def _none(*a, **k):
        return None
    monkeypatch.setattr(m, "get_recent_only", _none)

    gate_report = runner.ScanReport(
        url="https://offline.com.br", started_at="a", finished_at="b", duration_s=0.1,
        results=[], score=None, status="unreachable", error_detail="O site não respondeu.")

    async def _safe(*a, **k):
        return gate_report
    monkeypatch.setattr(m, "_safe_scan", _safe)

    async def _lvl(request):
        return ("anonymous", None)
    monkeypatch.setattr(m, "_access_level", _lvl)
    return TestClient(m.app, raise_server_exceptions=False)


def test_scan_result_returns_gate_status(client):
    r = client.get("/scan/result", params={"url": "offline.com.br"})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "unreachable"
    assert body["score"] is None and body["checks"] == []
    assert body["error_detail"]
