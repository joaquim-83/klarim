"""Klarim security checks.

Each ``check_*`` module in this package exposes a single coroutine::

    async def check(url: str) -> CheckResult

plus two module-level constants used for registration:

    ORDER: int        # position in the suite (lower runs first)
    CHECK_ID: str     # stable identifier, e.g. "check_13_sri"

Checks are **discovered dynamically** at import time — there is no hardcoded
list. Dropping a new ``check_*.py`` file that follows the contract above is
enough for it to join the suite; the number of checks grows over time.

Import :data:`ALL_CHECKS` to iterate over every registered check in ``ORDER``.
``runner.py`` uses this registry to execute the full suite.
"""

from __future__ import annotations

import importlib
import pkgutil
from typing import Awaitable, Callable, List, Tuple

from .base import CheckResult, Status, Severity  # re-exported for convenience

CheckFn = Callable[[str], Awaitable[CheckResult]]


def discover_checks() -> List[Tuple[str, CheckFn]]:
    """Import every ``check_*`` module and return ``(check_id, check)`` in order.

    A module joins the suite when it defines a callable ``check``. Ordering is by
    the module's ``ORDER`` constant (falling back to ``CHECK_ID``/name), so the
    running order stays deterministic regardless of filesystem iteration order.
    """
    discovered = []
    for mod_info in pkgutil.iter_modules(__path__):
        name = mod_info.name
        if not name.startswith("check_"):
            continue
        module = importlib.import_module(f"{__name__}.{name}")
        check_fn = getattr(module, "check", None)
        if not callable(check_fn):
            continue
        order = getattr(module, "ORDER", 10_000)
        check_id = getattr(module, "CHECK_ID", name)
        discovered.append((order, check_id, check_fn))

    discovered.sort(key=lambda t: (t[0], t[1]))
    return [(check_id, fn) for _, check_id, fn in discovered]


# Built once at import time. Recompute with ``discover_checks()`` if needed.
ALL_CHECKS: List[Tuple[str, CheckFn]] = discover_checks()

__all__ = [
    "ALL_CHECKS",
    "discover_checks",
    "CheckResult",
    "Status",
    "Severity",
    "CheckFn",
]
