"""KL-160 Parte 4 — varredura de segurança da plataforma (self-scan pelo painel admin).
Testa o runner (`_run_platform_security_scan`), o alerta e o gating do endpoint (cooldown/running).
Offline: FakeStore + `run_all` mockado (sem rede)."""
from __future__ import annotations

import asyncio

import pytest

import api.gate as gate
from security_gate.models import GateReport, Result, Severity, Status


def _run(coro):
    return asyncio.run(coro)


class _Store:
    def __init__(self):
        self.scans = []
        self._seq = 0

    async def create_platform_security_scan(self, *, url, score, passed, critical, high, medium,
                                            low, duration_ms, results, error=None,
                                            triggered_by="admin"):
        self._seq += 1
        row = {"id": self._seq, "url": url, "score": score, "passed": passed, "critical": critical,
               "high": high, "medium": medium, "low": low, "duration_ms": duration_ms,
               "results": results, "error": error, "triggered_by": triggered_by}
        self.scans.append(row)
        return self._seq

    async def list_platform_security_scans(self, limit=20):
        return [{k: v for k, v in s.items() if k != "results"} | {"created_at": None}
                for s in reversed(self.scans)][:limit]

    async def get_platform_security_scan(self, scan_id):
        for s in self.scans:
            if s["id"] == scan_id:
                return {**s, "created_at": None}
        return None


def _report(results):
    return GateReport(url="https://klarim.net", results=results, duration_ms=1234)


def _res(status, sev, check="c", detail="d", category="headers"):
    return Result(check=check, category=category, path="/", status=status, severity=sev, detail=detail)


@pytest.fixture
def store():
    return _Store()


# --- runner persiste o resultado --- #
def test_runner_persists_scan(store, monkeypatch):
    rep = _report([_res(Status.PASS, Severity.INFO), _res(Status.FAIL, Severity.HIGH, "rate_limit")])

    async def fake_run_all(**kw):
        return rep
    monkeypatch.setattr(gate, "run_all", fake_run_all)
    _run(gate._run_platform_security_scan(store, None))

    assert len(store.scans) == 1
    s = store.scans[0]
    assert s["url"] == "https://klarim.net"
    assert s["score"] == 90                 # 1 FAIL high = -10
    assert s["passed"] is True              # nenhum crítico
    assert s["high"] == 1 and s["critical"] == 0 and s["medium"] == 0
    assert s["duration_ms"] == 1234
    # results serializados (dicts com status/severity string)
    assert any(r["check"] == "rate_limit" and r["status"] == "fail" for r in s["results"])


def test_runner_records_error_on_exception(store, monkeypatch):
    async def boom(**kw):
        raise RuntimeError("engine down")
    monkeypatch.setattr(gate, "run_all", boom)
    _run(gate._run_platform_security_scan(store, None))
    assert len(store.scans) == 1
    assert store.scans[0]["error"] == "engine down"
    assert store.scans[0]["score"] is None


# --- alerta: score<80 OU crítico dispara; senão não --- #
def test_alert_fires_on_low_score(store, monkeypatch):
    rep = _report([_res(Status.FAIL, Severity.HIGH), _res(Status.FAIL, Severity.HIGH),
                   _res(Status.FAIL, Severity.MEDIUM)])   # -10-10-5 = 75 < 80
    async def fake_run_all(**kw):
        return rep
    monkeypatch.setattr(gate, "run_all", fake_run_all)
    calls = []
    async def fake_alert(url, report):
        calls.append(report.score)
    monkeypatch.setattr(gate, "_alert_platform_scan", fake_alert)
    _run(gate._run_platform_security_scan(store, None))
    assert calls == [75]


def test_alert_fires_on_critical(store, monkeypatch):
    rep = _report([_res(Status.FAIL, Severity.CRITICAL)])   # score 80, mas crítico
    async def fake_run_all(**kw):
        return rep
    monkeypatch.setattr(gate, "run_all", fake_run_all)
    calls = []
    async def fake_alert(url, report):
        calls.append(report.critical_count)
    monkeypatch.setattr(gate, "_alert_platform_scan", fake_alert)
    _run(gate._run_platform_security_scan(store, None))
    assert calls == [1]


def test_alert_silent_when_healthy(store, monkeypatch):
    rep = _report([_res(Status.FAIL, Severity.HIGH)])   # 90, sem crítico → sem alerta
    async def fake_run_all(**kw):
        return rep
    monkeypatch.setattr(gate, "run_all", fake_run_all)
    calls = []
    async def fake_alert(url, report):
        calls.append(1)
    monkeypatch.setattr(gate, "_alert_platform_scan", fake_alert)
    _run(gate._run_platform_security_scan(store, None))
    assert calls == []


# --- store round-trip (list sem results, get com results) --- #
def test_store_roundtrip(store):
    async def go():
        sid = await store.create_platform_security_scan(
            url="https://klarim.net", score=95, passed=True, critical=0, high=1, medium=0, low=0,
            duration_ms=100, results=[{"check": "x", "status": "fail"}])
        lst = await store.list_platform_security_scans()
        assert lst[0]["id"] == sid and "results" not in lst[0]
        full = await store.get_platform_security_scan(sid)
        assert full["results"][0]["check"] == "x"
        assert await store.get_platform_security_scan(9999) is None
    _run(go())
