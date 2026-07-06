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

import asyncio
import os

import httpx
import pytest

from scanner.checks.base import (
    CheckResult,
    Status,
    Severity,
    normalize_url,
    domain_of,
    with_scheme,
    base_url,
    registrable_domain,
    extract_script_refs,
)
from scanner.checks import check_sri, check_risky_sources, check_external_domains
from scanner.scoring import compute_score
from scanner import ALL_CHECKS, run_scan


# --------------------------------------------------------------------------- #
# Offline unit tests
# --------------------------------------------------------------------------- #

def test_checks_registered_dynamically():
    # The suite grows over time; assert on the contract, not a fixed count.
    ids = [cid for cid, _ in ALL_CHECKS]
    assert len(ids) >= 15                    # at least the current supply-chain set
    assert len(set(ids)) == len(ids)         # all unique
    assert ids[0] == "check_01_https"        # ordered by ORDER
    # supply-chain checks (KL-2) are present and in order
    for expected in ("check_13_sri", "check_14_risky_sources", "check_15_external_domains"):
        assert expected in ids
    assert ids == sorted(ids)                # ORDER + id keep them monotonic
    # every registered check is an async callable
    for _, fn in ALL_CHECKS:
        assert asyncio.iscoroutinefunction(fn)


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
# Supply-chain checks 13-15 (offline, mocked HTTP)
# --------------------------------------------------------------------------- #

TARGET_URL = "https://www.example.com"


def _fake_fetch(html: str):
    """Return an async stand-in for base.fetch that serves fixed HTML."""

    async def _fetch(url, method="GET", **kwargs):
        req = httpx.Request("GET", url)
        return httpx.Response(200, text=html, request=req)

    return _fetch


def _run_check(module, html, monkeypatch, url=TARGET_URL):
    monkeypatch.setattr(module, "fetch", _fake_fetch(html))
    return asyncio.run(module.check(url))


def _scripts(*srcs_with_optional_integrity) -> str:
    tags = []
    for item in srcs_with_optional_integrity:
        if isinstance(item, tuple):
            src, integrity = item
            tags.append(f'<script src="{src}" integrity="{integrity}"></script>')
        else:
            tags.append(f'<script src="{item}"></script>')
    return "<html><head>" + "".join(tags) + "</head><body></body></html>"


# --- shared helpers -------------------------------------------------------- #

def test_registrable_domain():
    assert registrable_domain("www.verdegreen.com.br") == "verdegreen.com.br"
    assert registrable_domain("cdn.verdegreen.com.br") == "verdegreen.com.br"
    assert registrable_domain("a.b.example.com") == "example.com"
    assert registrable_domain("example.com") == "example.com"
    # github.io is intentionally NOT a treated suffix -> each account is its own site
    assert registrable_domain("bigspotteddog.github.io") == "bigspotteddog.github.io"


def test_extract_script_refs_flags_external_and_sri():
    html = _scripts(
        "/local/app.js",                                   # same-site, relative
        "https://www.example.com/other.js",                # same-site, absolute
        ("https://cdn.thirdparty.com/lib.js", "sha384-x"),  # external, has SRI
        "https://analytics.foo.com/track.js",              # external, no SRI
    )
    refs = extract_script_refs(html, TARGET_URL)
    assert len(refs) == 4
    externals = [r for r in refs if r.is_external]
    assert {r.host for r in externals} == {"cdn.thirdparty.com", "analytics.foo.com"}
    sri_by_host = {r.host: r.has_sri for r in externals}
    assert sri_by_host["cdn.thirdparty.com"] is True
    assert sri_by_host["analytics.foo.com"] is False


# --- check 13: SRI --------------------------------------------------------- #

def test_check_sri_fails_when_majority_missing(monkeypatch):
    html = _scripts(
        "https://a.com/1.js",
        "https://b.com/2.js",
        "https://c.com/3.js",   # 3/3 external, none with SRI -> 100% missing
    )
    result = _run_check(check_sri, html, monkeypatch)
    assert result.status == Status.FAIL
    assert result.severity == Severity.ALTA


def test_check_sri_passes_when_all_protected(monkeypatch):
    html = _scripts(
        ("https://a.com/1.js", "sha384-aaa"),
        ("https://b.com/2.js", "sha384-bbb"),
    )
    result = _run_check(check_sri, html, monkeypatch)
    assert result.status == Status.PASS


def test_check_sri_passes_when_no_external_scripts(monkeypatch):
    html = _scripts("https://www.example.com/app.js", "/local.js")
    result = _run_check(check_sri, html, monkeypatch)
    assert result.status == Status.PASS
    assert result.details["external_scripts"] == 0


# --- check 14: risky sources ---------------------------------------------- #

def test_check_risky_sources_flags_github_pages(monkeypatch):
    html = _scripts("https://bigspotteddog.github.io/ScrollToFixed/jquery.js")
    result = _run_check(check_risky_sources, html, monkeypatch)
    assert result.status == Status.FAIL
    assert result.severity == Severity.ALTA
    assert "github.io" in result.evidence


def test_check_risky_sources_flags_s3_variants(monkeypatch):
    for host in (
        "s3.amazonaws.com/mybucket",
        "mybucket.s3.amazonaws.com",
        "mybucket.s3.us-east-1.amazonaws.com",
        "s3-eu-west-1.amazonaws.com",
    ):
        html = _scripts(f"https://{host}/app.js")
        result = _run_check(check_risky_sources, html, monkeypatch)
        assert result.status == Status.FAIL, host


def test_check_risky_sources_ignores_managed_cdn(monkeypatch):
    html = _scripts(
        "https://d123.cloudfront.net/app.js",     # managed CDN -> NOT risky
        "https://cdnjs.cloudflare.com/lib.js",
    )
    result = _run_check(check_risky_sources, html, monkeypatch)
    assert result.status == Status.PASS


# --- check 15: external domains ------------------------------------------- #

def test_check_external_domains_pass_when_few(monkeypatch):
    html = _scripts(
        "https://a.com/1.js",
        "https://b.com/2.js",
        "https://www.example.com/self.js",  # not counted (same site)
    )
    result = _run_check(check_external_domains, html, monkeypatch)
    assert result.status == Status.PASS
    assert result.details["count"] == 2


def test_check_external_domains_fail_medium(monkeypatch):
    html = _scripts(*[f"https://d{i}.com/s.js" for i in range(12)])  # 12 domains
    result = _run_check(check_external_domains, html, monkeypatch)
    assert result.status == Status.FAIL
    assert result.severity == Severity.MEDIA


def test_check_external_domains_fail_high(monkeypatch):
    html = _scripts(*[f"https://d{i}.com/s.js" for i in range(16)])  # 16 domains
    result = _run_check(check_external_domains, html, monkeypatch)
    assert result.status == Status.FAIL
    assert result.severity == Severity.ALTA


# --------------------------------------------------------------------------- #
# Online integration test (opt-in)
# --------------------------------------------------------------------------- #

ONLINE = os.environ.get("KLARIM_ONLINE") == "1"
TARGET = os.environ.get("KLARIM_TARGET", "https://www.verdegreen.com.br")


@pytest.mark.skipif(not ONLINE, reason="set KLARIM_ONLINE=1 to run the network test")
@pytest.mark.asyncio
async def test_scan_real_domain():
    report = await run_scan(TARGET)
    # The scan always produces exactly one result per registered check.
    assert len(report.results) == len(ALL_CHECKS)
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
