"""Testes do cache de scan (Redis) — offline, com Redis fake."""

from __future__ import annotations

import asyncio

from scanner.cache import ScanCache
from scanner.runner import ScanReport
from scanner.scoring import compute_score
from scanner.checks.base import CheckResult, Status, Severity


class FakeRedis:
    def __init__(self):
        self.d = {}

    async def get(self, k):
        return self.d.get(k)

    async def set(self, k, v, ex=None):
        self.d[k] = v


class BrokenRedis:
    async def get(self, k):
        raise RuntimeError("redis down")

    async def set(self, k, v, ex=None):
        raise RuntimeError("redis down")


def _sample_report(url="https://x.com"):
    results = [
        CheckResult("HTTPS ativo", Status.PASS, Severity.CRITICA, "ok", check_id="check_01_https"),
        CheckResult("SRI", Status.FAIL, Severity.ALTA, "3/3 sem SRI", check_id="check_13_sri",
                    details={"without_sri_urls": ["https://cdn.x/a.js"]}),
    ]
    return ScanReport(url, "2026-07-07T00:00:00+00:00", "2026-07-07T00:00:30+00:00",
                      30.0, results, compute_score(results))


def test_key_normalization():
    c = ScanCache(FakeRedis())
    assert c._key("https://X.com/") == c._key("https://x.com") == c._key("  HTTPS://x.com/ ")
    assert c._key("https://a.com") != c._key("https://b.com")


def test_cache_roundtrip():
    c = ScanCache(FakeRedis())
    assert asyncio.run(c.get("https://x.com")) is None  # miss
    rep = _sample_report()
    asyncio.run(c.set("https://X.com/", rep))
    got = asyncio.run(c.get("https://x.COM"))  # normalização -> mesma chave
    assert got is not None
    assert got.url == rep.url
    assert got.score.score == rep.score.score
    assert len(got.results) == 2
    assert got.results[1].details["without_sri_urls"] == ["https://cdn.x/a.js"]


def test_cache_degrades_on_redis_error():
    c = ScanCache(BrokenRedis())
    assert asyncio.run(c.get("https://x.com")) is None
    asyncio.run(c.set("https://x.com", _sample_report()))  # não levanta
