"""Testes do get_or_scan: reusar cache/banco antes de reescanear, respeitando o
tier (free=15 / full=29, KL-27)."""

from __future__ import annotations

import asyncio

import api.main as m
from scanner.runner import ScanReport
from scanner.scoring import compute_score
from scanner.checks.base import CheckResult, Status, Severity


def _report(n: int, url: str = "https://x.com.br") -> ScanReport:
    results = [
        CheckResult(name=f"c{i}", status=Status.PASS, severity=Severity.MEDIA,
                    evidence="ok", check_id=f"check_{i:02d}")
        for i in range(1, n + 1)
    ]
    return ScanReport(url=url, started_at="2026-07-08T00:00:00",
                      finished_at="2026-07-08T00:00:30", duration_s=30.0,
                      results=results, score=compute_score(results))


class FakeStore:
    def __init__(self, checks):
        self._checks = checks

    async def get_recent_scan_checks(self, url, max_age_minutes=60):
        return self._checks


def test_get_or_scan_free_uses_recent_db_scan(monkeypatch):
    """Scan gratuito (15) recente no banco → reconstrói, NÃO reescaneia."""
    monkeypatch.setattr(m, "_cache", None)
    monkeypatch.setattr(m, "get_target_store", lambda: FakeStore(_report(15).to_dict()))

    async def _boom(url, full=True):
        raise AssertionError("run_scan não deveria ser chamado (havia scan no banco)")

    monkeypatch.setattr(m, "run_scan", _boom)

    report = asyncio.run(m.get_or_scan("https://x.com.br", full=False))
    assert isinstance(report, ScanReport)
    assert report.url == "https://x.com.br" and len(report.results) == 15


def test_get_or_scan_full_rescans_when_db_is_free_tier(monkeypatch):
    """Pedido COMPLETO (29) mas o banco só tem um scan gratuito (15) → reescaneia (KL-27)."""
    monkeypatch.setattr(m, "_cache", None)
    monkeypatch.setattr(m, "get_target_store", lambda: FakeStore(_report(15).to_dict()))

    called = {"n": 0, "full": None}

    async def _fake_scan(url, full=True):
        called["n"] += 1
        called["full"] = full
        return _report(29, url)

    monkeypatch.setattr(m, "run_scan", _fake_scan)

    report = asyncio.run(m.get_or_scan("https://x.com.br", full=True))
    assert called["n"] == 1 and called["full"] is True
    assert len(report.results) == 29


def test_get_or_scan_rescans_when_no_recent(monkeypatch):
    """Sem cache e sem scan recente → escaneia."""
    monkeypatch.setattr(m, "_cache", None)
    monkeypatch.setattr(m, "get_target_store", lambda: FakeStore(None))

    called = {"n": 0}

    async def _fake_scan(url, full=True):
        called["n"] += 1
        return _report(15, url)

    monkeypatch.setattr(m, "run_scan", _fake_scan)

    report = asyncio.run(m.get_or_scan("https://y.com.br", full=False))
    assert called["n"] == 1 and report.url == "https://y.com.br"
