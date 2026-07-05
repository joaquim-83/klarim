"""
Base primitives shared by all Klarim checks.

Every check module exposes a single coroutine:

    async def check(url: str) -> CheckResult

and returns a :class:`CheckResult` describing what was observed. Checks must
only ever perform passive, non-invasive HTTP GET/HEAD requests (or read a TLS
certificate) against public URLs. No attack payloads, no authentication, no
brute-force. See ``README.md`` for the legal framing.

This module also provides the shared HTTP plumbing used by every check:

* a 10s timeout per request (spec requirement),
* a per-domain rate limiter of 1 request/second (spec requirement),
* a common User-Agent identifying the scanner.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, Optional
from urllib.parse import urlparse, urlunparse

import httpx


# --------------------------------------------------------------------------- #
# Constants
# --------------------------------------------------------------------------- #

# Status of a single check.
class Status:
    PASS = "PASS"
    FAIL = "FAIL"
    INCONCLUSO = "INCONCLUSO"


# Severity of a finding, ordered from most to least serious. The weights are
# consumed by ``scanner.scoring`` to compute the 0-100 score.
class Severity:
    CRITICA = "CRITICA"
    ALTA = "ALTA"
    MEDIA = "MEDIA"
    BAIXA = "BAIXA"


# Per-request timeout in seconds (spec: "Timeout de 10s por request").
REQUEST_TIMEOUT = 10.0

# Minimum interval between two requests to the *same* domain, in seconds
# (spec: "rate limit 1 req/s por domínio").
RATE_LIMIT_INTERVAL = 1.0

# A polite, honest User-Agent. Klarim identifies itself; it does not pretend to
# be a browser and it does not hide.
USER_AGENT = (
    "KlarimScanner/0.1 (+https://klarim.io; passive security scan; "
    "GET/HEAD only)"
)


# --------------------------------------------------------------------------- #
# CheckResult
# --------------------------------------------------------------------------- #

@dataclass
class CheckResult:
    """The outcome of a single security check.

    Attributes:
        name:     Human-readable name of the check (e.g. ``"HTTPS ativo"``).
        status:   One of :class:`Status` (PASS / FAIL / INCONCLUSO).
        severity: One of :class:`Severity` (CRITICA / ALTA / MEDIA / BAIXA).
                  This is the *weight* of the check when it FAILs; it does not
                  change with the outcome.
        evidence: A short human-readable string with the concrete detail that
                  justifies the status (headers seen, paths probed, cert dates).
    """

    name: str
    status: str
    severity: str
    evidence: str = ""
    # ``id`` lets the runner / API address a specific check (e.g. "check_01").
    check_id: str = ""
    # Optional structured extras (e.g. probed paths) for the technical report.
    details: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def __str__(self) -> str:  # pragma: no cover - cosmetic
        icon = {
            Status.PASS: "PASS",
            Status.FAIL: "FAIL",
            Status.INCONCLUSO: "INCONCLUSO",
        }.get(self.status, self.status)
        return f"[{icon}] ({self.severity}) {self.name}: {self.evidence}"


# --------------------------------------------------------------------------- #
# URL helpers
# --------------------------------------------------------------------------- #

def normalize_url(url: str) -> str:
    """Return ``url`` guaranteed to have a scheme (defaults to https)."""
    url = url.strip()
    if not url:
        raise ValueError("empty URL")
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    return url


def domain_of(url: str) -> str:
    """Return the hostname of ``url`` (without port), lowercased."""
    return (urlparse(normalize_url(url)).hostname or "").lower()


def with_scheme(url: str, scheme: str) -> str:
    """Return ``url`` rewritten to use ``scheme`` (http/https)."""
    parts = urlparse(normalize_url(url))
    return urlunparse(parts._replace(scheme=scheme))


def base_url(url: str) -> str:
    """Return ``scheme://host[:port]`` with no path/query, for path probing."""
    parts = urlparse(normalize_url(url))
    return urlunparse((parts.scheme, parts.netloc, "", "", "", ""))


def host_port(url: str, default: int = 443) -> tuple[str, int]:
    """Return ``(hostname, port)`` for a URL, using ``default`` when absent."""
    parts = urlparse(normalize_url(url))
    return (parts.hostname or "", parts.port or default)


# --------------------------------------------------------------------------- #
# Per-domain rate limiter
# --------------------------------------------------------------------------- #

class _DomainRateLimiter:
    """Enforces a minimum interval between requests to the same domain.

    A single lock + last-timestamp is kept per domain. Concurrent checks that
    hit the same domain are serialised through the lock and spaced out by
    ``RATE_LIMIT_INTERVAL`` seconds. Different domains are independent.
    """

    def __init__(self, interval: float = RATE_LIMIT_INTERVAL) -> None:
        self._interval = interval
        self._locks: Dict[str, asyncio.Lock] = {}
        self._last: Dict[str, float] = {}
        self._guard = asyncio.Lock()

    async def _lock_for(self, domain: str) -> asyncio.Lock:
        async with self._guard:
            if domain not in self._locks:
                self._locks[domain] = asyncio.Lock()
            return self._locks[domain]

    async def acquire(self, domain: str) -> None:
        lock = await self._lock_for(domain)
        await lock.acquire()
        now = time.monotonic()
        last = self._last.get(domain)
        if last is not None:
            wait = self._interval - (now - last)
            if wait > 0:
                await asyncio.sleep(wait)
        self._last[domain] = time.monotonic()

    def release(self, domain: str) -> None:
        lock = self._locks.get(domain)
        if lock is not None and lock.locked():
            lock.release()


# Module-level singleton shared by every check.
_rate_limiter = _DomainRateLimiter()


# --------------------------------------------------------------------------- #
# HTTP request helper
# --------------------------------------------------------------------------- #

async def fetch(
    url: str,
    method: str = "GET",
    *,
    follow_redirects: bool = True,
    verify: bool = False,
    headers: Optional[Dict[str, str]] = None,
    timeout: float = REQUEST_TIMEOUT,
) -> httpx.Response:
    """Perform a single rate-limited, timed-out HTTP request.

    Only ``GET`` and ``HEAD`` are permitted — this is a passive scanner.

    ``verify`` defaults to ``False`` so that header/content checks still return
    useful data on hosts with a broken/expired/mismatched certificate. The
    certificate itself is validated separately and rigorously by
    ``check_ssl`` using a raw TLS handshake, so disabling verification here does
    not weaken the scan.
    """
    method = method.upper()
    if method not in ("GET", "HEAD"):
        raise ValueError(f"passive scanner refuses method {method!r}")

    req_headers = {"User-Agent": USER_AGENT}
    if headers:
        req_headers.update(headers)

    domain = domain_of(url)
    await _rate_limiter.acquire(domain)
    try:
        async with httpx.AsyncClient(
            verify=verify,
            follow_redirects=follow_redirects,
            timeout=timeout,
            headers=req_headers,
        ) as client:
            return await client.request(method, url)
    finally:
        _rate_limiter.release(domain)


def looks_like_html(response: httpx.Response) -> bool:
    """Heuristic: is this response an HTML page (likely an SPA fallback)?

    Many single-page-app hosts answer *every* path with ``index.html`` and a
    200. Checks that probe for sensitive files must not treat that fallback as
    a real hit, so they use this to distinguish a genuine file from the SPA
    catch-all.
    """
    ctype = response.headers.get("content-type", "").lower()
    if "text/html" in ctype:
        return True
    body_head = response.text[:512].lstrip().lower()
    return body_head.startswith(("<!doctype html", "<html"))
