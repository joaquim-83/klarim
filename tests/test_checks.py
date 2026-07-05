"""Basic tests for the Klarim scanner.

Two flavours:

* Offline unit tests (always run): exercise the pure logic — CheckResult,
  URL helpers, scoring — with no network access.

* Online integration test (opt-in): runs the full 12-check suite against a real
  domain. It is skipped by default so CI stays hermetic; enable it with:

      KLARIM_ONLINE=1 pytest tests/test_checks.py

The default online target is https://www.verdegreen.com.br (the Duda hotel case
from the spec). Override with KLARIM_TARGET=<url>.
"""

from __future__ import annotations

import os

import pytest

from scanner.checks.base import (
    CheckResult,
    Status,
    Severity,
    normalize_url,
    domain_of,
    with_scheme,
    base_url,
)
from scanner.scoring import compute_score
from scanner import ALL_CHECKS, run_scan


# --------------------------------------------------------------------------- #
# Offline unit tests
# --------------------------------------------------------------------------- #

def test_twelve_checks_registered():
    assert len(ALL_CHECKS) == 12
    ids = [cid for cid, _ in ALL_CHECKS]
    assert ids[0] == "check_01_https"
    assert ids[-1] == "check_12_metatags"
    # all unique
    assert len(set(ids)) == 12


def test_url_helpers():
    assert normalize_url("example.com") == "https://example.com"
    assert normalize_url("http://example.com") == "http://example.com"
    assert domain_of("https://www.example.com/path") == "www.example.com"
    assert with_scheme("https://x.com/a", "http") == "http://x.com/a"
    assert base_url("https://x.com:8443/a?b=1") == "https://x.com:8443"


def test_scoring_all_pass_is_100():
    results = [
        CheckResult(name=f"c{i}", status=Status.PASS, severity=Severity.CRITICA)
        for i in range(5)
    ]
    assert compute_score(results).score == 100


def test_scoring_all_fail_is_0():
    results = [
        CheckResult(name=f"c{i}", status=Status.FAIL, severity=Severity.ALTA)
        for i in range(5)
    ]
    breakdown = compute_score(results)
    assert breakdown.score == 0
    assert breakdown.failed == 5


def test_scoring_inconclusive_is_neutral():
    results = [
        CheckResult(name="a", status=Status.PASS, severity=Severity.CRITICA),
        CheckResult(name="b", status=Status.INCONCLUSO, severity=Severity.CRITICA),
    ]
    # The INCONCLUSO check is excluded from the denominator -> 100.
    breakdown = compute_score(results)
    assert breakdown.score == 100
    assert breakdown.inconclusive == 1


def test_semaphore_thresholds():
    green = [CheckResult("a", Status.PASS, Severity.CRITICA)]
    assert compute_score(green).semaphore == "verde"

    mixed = [
        CheckResult("a", Status.PASS, Severity.CRITICA),   # weight 5
        CheckResult("b", Status.FAIL, Severity.CRITICA),   # weight 5
    ]
    # 5 / 10 = 50 -> amarelo
    assert compute_score(mixed).semaphore == "amarelo"

    red = [
        CheckResult("a", Status.PASS, Severity.BAIXA),     # weight 1
        CheckResult("b", Status.FAIL, Severity.CRITICA),   # weight 5
    ]
    # 1 / 6 = 17 -> vermelho
    assert compute_score(red).semaphore == "vermelho"


# --------------------------------------------------------------------------- #
# Online integration test (opt-in)
# --------------------------------------------------------------------------- #

ONLINE = os.environ.get("KLARIM_ONLINE") == "1"
TARGET = os.environ.get("KLARIM_TARGET", "https://www.verdegreen.com.br")


@pytest.mark.skipif(not ONLINE, reason="set KLARIM_ONLINE=1 to run the network test")
@pytest.mark.asyncio
async def test_scan_real_domain():
    report = await run_scan(TARGET)
    # 12 checks always produce 12 results.
    assert len(report.results) == 12
    # Every result has a valid status and severity.
    for r in report.results:
        assert r.status in (Status.PASS, Status.FAIL, Status.INCONCLUSO)
        assert r.severity in (
            Severity.CRITICA,
            Severity.ALTA,
            Severity.MEDIA,
            Severity.BAIXA,
        )
        assert r.evidence  # non-empty evidence string
    # A score in range must be produced.
    assert report.score is not None
    assert 0 <= report.score.score <= 100
