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
from html.parser import HTMLParser
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin, urlparse, urlunparse

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
        owasp/cwe/lgpd: Classificação em frameworks reconhecidos (KL-34/35). São
                  metadata opcional (``None`` por default, retrocompatível); o
                  ``runner`` as carimba pelo ``check_id`` a partir de
                  ``scanner.checks.classifications``. Aparecem só no relatório
                  técnico e na API — nunca no executivo.
    """

    name: str
    status: str
    severity: str
    evidence: str = ""
    # ``id`` lets the runner / API address a specific check (e.g. "check_01").
    check_id: str = ""
    # Optional structured extras (e.g. probed paths) for the technical report.
    details: Dict[str, Any] = field(default_factory=dict)
    # Classificação de compliance (KL-34/35) — opcional, carimbada pelo runner.
    owasp: Optional[str] = None    # ex.: "A05:2025 Security Misconfiguration"
    cwe: Optional[str] = None      # ex.: "CWE-693"
    lgpd: Optional[str] = None     # ex.: "Art. 46" (ou "Art. 46, Art. 48")

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "CheckResult":
        return cls(
            name=d["name"],
            status=d["status"],
            severity=d["severity"],
            evidence=d.get("evidence", ""),
            check_id=d.get("check_id", ""),
            details=d.get("details") or {},
            owasp=d.get("owasp"),
            cwe=d.get("cwe"),
            lgpd=d.get("lgpd"),
        )

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


# --------------------------------------------------------------------------- #
# Registrable-domain helper (lightweight public-suffix approximation)
# --------------------------------------------------------------------------- #

# A small set of multi-label public suffixes so that e.g. "www.foo.com.br" and
# "cdn.foo.com.br" resolve to the same registrable domain "foo.com.br". This is
# a pragmatic, dependency-free approximation of the Public Suffix List — good
# enough to tell "same site" from "third party" for the supply-chain checks.
_TWO_LABEL_SUFFIXES = {
    # ccTLD second levels
    "com.br", "net.br", "org.br", "gov.br", "edu.br", "art.br", "blog.br",
    "co.uk", "org.uk", "gov.uk", "ac.uk",
    "com.au", "net.au", "org.au",
    "co.jp", "com.mx", "com.ar", "com.co", "co.in", "com.pt", "co.za",
    # private suffixes where each subdomain is a distinct owner (PSL "private")
    "github.io",
}


def registrable_domain(host: str) -> str:
    """Return the registrable domain (eTLD+1) of ``host``.

    ``www.verdegreen.com.br`` -> ``verdegreen.com.br``;
    ``bigspotteddog.github.io`` -> ``bigspotteddog.github.io`` (``github.io`` is a
    public suffix, so each GitHub Pages account is its own registrable domain —
    exactly what the supply-chain checks want).
    """
    host = (host or "").lower().strip(".")
    parts = host.split(".")
    if len(parts) <= 2:
        return host
    last2 = ".".join(parts[-2:])
    if last2 in _TWO_LABEL_SUFFIXES:
        return ".".join(parts[-3:])
    return last2


# --------------------------------------------------------------------------- #
# HTML <script> extraction (stdlib parser; no BeautifulSoup dependency)
# --------------------------------------------------------------------------- #

@dataclass
class ScriptRef:
    """A ``<script src=...>`` reference found in a page."""

    src: str                     # absolute URL of the script
    host: str                    # hostname of the script URL
    registrable: str             # registrable domain of the script host
    integrity: Optional[str]     # value of the integrity attribute, if any
    is_external: bool            # True if a different registrable domain than the page

    @property
    def has_sri(self) -> bool:
        return bool(self.integrity)


class _ScriptSrcExtractor(HTMLParser):
    """Collect (src, integrity) for every <script> tag carrying a ``src``."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.found: List[tuple[str, Optional[str]]] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag.lower() != "script":
            return
        adict = {k.lower(): v for k, v in attrs}
        src = adict.get("src")
        if src:
            integrity = adict.get("integrity") or None
            self.found.append((src.strip(), integrity))


def extract_script_refs(html: str, page_url: str) -> List[ScriptRef]:
    """Parse ``html`` and return every external/internal script reference.

    ``page_url`` is used to resolve relative ``src`` values to absolute URLs and
    to decide whether each script is same-site or third-party.
    """
    parser = _ScriptSrcExtractor()
    try:
        parser.feed(html)
    except Exception:  # noqa: BLE001 - malformed HTML must not crash a check
        pass

    page_reg = registrable_domain(domain_of(page_url))
    refs: List[ScriptRef] = []
    for src, integrity in parser.found:
        abs_src = urljoin(page_url, src)
        host = (urlparse(abs_src).hostname or "").lower()
        if not host:
            continue  # e.g. data: URIs
        reg = registrable_domain(host)
        refs.append(
            ScriptRef(
                src=abs_src,
                host=host,
                registrable=reg,
                integrity=integrity,
                is_external=(reg != page_reg),
            )
        )
    return refs
