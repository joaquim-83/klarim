"""Runner paraleliza os checks preservando ordem (KL-51 f3 hotfix 504) — offline.

Os checks passaram a rodar com `asyncio.gather` + `Semaphore` (antes era um `for` +
`await` sequencial, que num site grande/frio estourava o timeout do proxy → 504). Estes
testes usam checks mockados (sem rede) para provar concorrência + ordem + tolerância a
falha, sem depender de um scan real.
"""

from __future__ import annotations

import asyncio
import time

import pytest

import scanner.runner as runner
import scanner.privacy_checks as privacy_checks
from scanner.checks.base import CheckResult, Status, Severity


@pytest.fixture(autouse=True)
def _no_privacy_fetch(monkeypatch):
    """KL-44 P5: os indicadores de privacidade fazem 1 GET próprio. Estes testes medem a
    concorrência dos CHECKS — sem rede — então neutralizamos o fetch de privacidade."""
    async def _noop(url):
        return None
    monkeypatch.setattr(privacy_checks, "scan_privacy", _noop)


def _mk(cid: str, delay: float):
    async def _c(url):
        await asyncio.sleep(delay)
        return CheckResult(name=cid, status=Status.PASS, severity=Severity.BAIXA, evidence="ok")
    return (cid, _c)


def test_run_scan_is_concurrent_and_ordered(monkeypatch):
    # 3 checks de 0,3s: sequencial daria ~0,9s; em paralelo ~0,3s.
    checks = [_mk("check_01_https", 0.3), _mk("check_02_hsts", 0.3), _mk("check_03_ssl", 0.3)]
    monkeypatch.setattr(runner, "ALL_CHECKS", checks)

    t0 = time.monotonic()
    report = asyncio.run(runner.run_scan("https://example.com", full=True))
    dt = time.monotonic() - t0

    ids = [r.check_id for r in report.results]
    assert ids == ["check_01_https", "check_02_hsts", "check_03_ssl"]  # ordem = ordem dos checks
    assert len(report.results) == 3
    assert dt < 0.7, f"esperado concorrente (~0,3s), levou {dt:.2f}s"


def test_run_scan_respects_concurrency_cap(monkeypatch):
    # Com teto de concorrência 2, 4 checks de 0,3s rodam em 2 ondas ≈ 0,6s (não 0,3s nem 1,2s).
    monkeypatch.setattr(runner, "SCAN_MAX_CONCURRENCY", 2)
    checks = [_mk(f"check_0{i}_x", 0.3) for i in (1, 2, 3, 4)]
    monkeypatch.setattr(runner, "ALL_CHECKS", checks)

    t0 = time.monotonic()
    report = asyncio.run(runner.run_scan("https://example.com", full=True))
    dt = time.monotonic() - t0

    assert len(report.results) == 4
    assert 0.5 < dt < 0.9, f"esperado ~0,6s (2 ondas), levou {dt:.2f}s"


def test_run_scan_one_bad_check_does_not_kill(monkeypatch):
    async def _boom(url):
        raise RuntimeError("boom")

    async def _ok(url):
        return CheckResult(name="check_02_hsts", status=Status.PASS, severity=Severity.BAIXA, evidence="ok")

    monkeypatch.setattr(runner, "ALL_CHECKS", [("check_01_https", _boom), ("check_02_hsts", _ok)])
    report = asyncio.run(runner.run_scan("https://example.com", full=True))

    assert len(report.results) == 2
    st = {r.check_id: r.status for r in report.results}
    assert st["check_01_https"] == Status.INCONCLUSO  # a falha vira INCONCLUSO, não derruba o scan
    assert st["check_02_hsts"] == Status.PASS
