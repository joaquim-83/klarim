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
from typing import Awaitable, Callable, Dict, List, Tuple

from .base import CheckResult, Status, Severity  # re-exported for convenience

CheckFn = Callable[[str], Awaitable[CheckResult]]

# Tier gratuito (KL-27): os checks com ``ORDER <= FREE_CHECK_MAX_ORDER`` compõem o
# scan gratuito (15 checks). O scan pago (``full=True``) roda todos os checks.
# Não é a identidade do produto — é só onde cortamos o funil grátis/pago.
FREE_CHECK_MAX_ORDER = 15


def _discover_all() -> List[Tuple[int, str, str, CheckFn]]:
    """Importa cada ``check_*`` e devolve ``(order, check_id, name, check)`` ordenado.

    Um módulo entra na suíte quando define uma coroutine ``check``. A ordem é pelo
    ``ORDER`` (fallback ``CHECK_ID``/nome), determinística independente da ordem do
    filesystem.
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
        display = getattr(module, "NAME", check_id)
        discovered.append((order, check_id, display, check_fn))

    discovered.sort(key=lambda t: (t[0], t[1]))
    return discovered


def discover_checks(full: bool = True) -> List[Tuple[str, CheckFn]]:
    """Retorna ``(check_id, check)`` em ordem. ``full=False`` limita ao tier gratuito
    (``ORDER <= FREE_CHECK_MAX_ORDER``) — usado no scan público (KL-27)."""
    return [
        (cid, fn)
        for order, cid, _name, fn in _discover_all()
        if full or order <= FREE_CHECK_MAX_ORDER
    ]


# Construído uma vez no import. Recompute com ``discover_checks()`` se preciso.
_META: List[Tuple[int, str, str, CheckFn]] = _discover_all()

ALL_CHECKS: List[Tuple[str, CheckFn]] = [(cid, fn) for _o, cid, _n, fn in _META]
FREE_CHECKS: List[Tuple[str, CheckFn]] = [
    (cid, fn) for o, cid, _n, fn in _META if o <= FREE_CHECK_MAX_ORDER
]

# Metadados leves (sem chamar os checks) para o frontend/summary listar nomes e
# separar tiers — inclusive os pagos que o scan gratuito nunca executa (KL-27).
CHECK_META: List[Dict[str, object]] = [
    {"check_id": cid, "name": name, "order": o, "paid": o > FREE_CHECK_MAX_ORDER}
    for o, cid, name, _fn in _META
]

__all__ = [
    "ALL_CHECKS",
    "FREE_CHECKS",
    "CHECK_META",
    "FREE_CHECK_MAX_ORDER",
    "discover_checks",
    "CheckResult",
    "Status",
    "Severity",
    "CheckFn",
]
