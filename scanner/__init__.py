"""Klarim scanner package.

Public surface:

    from scanner import run_scan, scan, ScanReport, compute_score

* :func:`run_scan`  — async, runs the 12 checks against a URL.
* :func:`scan`      — synchronous convenience wrapper.
* :class:`ScanReport` — the bundled results + score.
"""

from __future__ import annotations

from .runner import ScanReport, run_scan, scan, format_report
from .scoring import ScoreBreakdown, compute_score, summarize_fails
from .checks import ALL_CHECKS
from .checks.base import CheckResult, Status, Severity

__all__ = [
    "run_scan",
    "scan",
    "format_report",
    "ScanReport",
    "ScoreBreakdown",
    "compute_score",
    "summarize_fails",
    "ALL_CHECKS",
    "CheckResult",
    "Status",
    "Severity",
]

__version__ = "0.1.0"
