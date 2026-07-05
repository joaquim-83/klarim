"""Klarim security checks.

Each ``check_*`` module in this package exposes a single coroutine::

    async def check(url: str) -> CheckResult

Import :data:`ALL_CHECKS` to iterate over every check in canonical order
(1..12). ``runner.py`` uses this registry to execute the full suite.
"""

from __future__ import annotations

from typing import Awaitable, Callable, List, Tuple

from .base import CheckResult, Status, Severity  # re-export for convenience

from . import (
    check_https,
    check_hsts,
    check_ssl,
    check_tls,
    check_csp,
    check_xfo,
    check_xcto,
    check_server,
    check_sourcemaps,
    check_sensitive,
    check_dirlist,
    check_metatags,
)

# (check_id, coroutine) in the canonical 1..12 order defined by the spec.
CheckFn = Callable[[str], Awaitable[CheckResult]]

ALL_CHECKS: List[Tuple[str, CheckFn]] = [
    ("check_01_https", check_https.check),
    ("check_02_hsts", check_hsts.check),
    ("check_03_ssl", check_ssl.check),
    ("check_04_tls", check_tls.check),
    ("check_05_csp", check_csp.check),
    ("check_06_xfo", check_xfo.check),
    ("check_07_xcto", check_xcto.check),
    ("check_08_server", check_server.check),
    ("check_09_sourcemaps", check_sourcemaps.check),
    ("check_10_sensitive", check_sensitive.check),
    ("check_11_dirlist", check_dirlist.check),
    ("check_12_metatags", check_metatags.check),
]

__all__ = ["ALL_CHECKS", "CheckResult", "Status", "Severity", "CheckFn"]
