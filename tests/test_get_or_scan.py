"""Testes do get_or_scan: reusar cache/banco antes de reescanear (fix rápido)."""

from __future__ import annotations

import asyncio

import api.main as m
from scanner.runner import ScanReport
from scanner.scoring import compute_score
from scanner.checks.base import CheckResult, Status, Severity


def _report_dict():
    results = [CheckResult(name="HTTPS", status=Status.FAIL, severity=Severity.CRITICA,
                           evidence="x", check_id="check_01_https")]
    rep = ScanReport(url="https://x.com.br", started_at="2026-07-08T00:00:00",
                     finished_at="2026-07-08T00:00:30", duration_s=30.0,
                     results=results, score=compute_score(results))
    return rep.to_dict()


class FakeStore:
    def __init__(self, checks):
        self._checks = checks

    async def get_recent_scan_checks(self, url, max_age_minutes=60):
        return self._checks


def test_get_or_scan_uses_recent_db_scan(monkeypatch):
    """Scan recente no banco → reconstrói, NÃO reescaneia."""
    monkeypatch.setattr(m, "_cache", None)
    monkeypatch.setattr(m, "get_target_store", lambda: FakeStore(_report_dict()))

    async def _boom(url):
        raise AssertionError("run_scan não deveria ser chamado (havia scan no banco)")

    monkeypatch.setattr(m, "run_scan", _boom)

    report = asyncio.run(m.get_or_scan("https://x.com.br"))
    assert isinstance(report, ScanReport)
    assert report.url == "https://x.com.br" and len(report.results) == 1


def test_get_or_scan_rescans_when_no_recent(monkeypatch):
    """Sem cache e sem scan recente → escaneia."""
    monkeypatch.setattr(m, "_cache", None)
    monkeypatch.setattr(m, "get_target_store", lambda: FakeStore(None))

    called = {"n": 0}

    async def _fake_scan(url):
        called["n"] += 1
        results = [CheckResult(name="HTTPS", status=Status.PASS, severity=Severity.CRITICA,
                               evidence="ok", check_id="check_01_https")]
        return ScanReport(url=url, started_at="t", finished_at="t", duration_s=1.0,
                          results=results, score=compute_score(results))

    monkeypatch.setattr(m, "run_scan", _fake_scan)

    report = asyncio.run(m.get_or_scan("https://y.com.br"))
    assert called["n"] == 1 and report.url == "https://y.com.br"
