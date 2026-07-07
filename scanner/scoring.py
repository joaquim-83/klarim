"""Score calculation for a Klarim scan (0-100).

The score is a weighted proportion of checks passed. Each check carries a weight
derived from its severity. A ``PASS`` earns the full weight, a ``FAIL`` earns
zero, and an ``INCONCLUSO`` is *excluded from the denominator* so that a check we
could not evaluate neither rewards nor punishes the target.

    score = round(100 * sum(weight of PASSes) / sum(weight of PASS+FAIL))

A traffic-light ("semáforo") grade is derived from the score for the executive
report described in the spec.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Iterable, List

from .checks.base import CheckResult, Status, Severity


# Weight of a check when evaluated, keyed by severity.
SEVERITY_WEIGHT: Dict[str, int] = {
    Severity.CRITICA: 5,
    Severity.ALTA: 3,
    Severity.MEDIA: 2,
    Severity.BAIXA: 1,
}

# Traffic-light thresholds (inclusive lower bounds).
GREEN_THRESHOLD = 80
YELLOW_THRESHOLD = 50


@dataclass
class ScoreBreakdown:
    score: int                       # 0-100
    semaphore: str                   # "verde" | "amarelo" | "vermelho"
    grade_icon: str                  # 🟢 | 🟡 | 🔴
    earned_weight: int
    considered_weight: int           # denominator (excludes INCONCLUSO)
    total_weight: int                # weight of all registered checks
    passed: int
    failed: int
    inconclusive: int
    fails_by_severity: Dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "score": self.score,
            "semaphore": self.semaphore,
            "grade_icon": self.grade_icon,
            "earned_weight": self.earned_weight,
            "considered_weight": self.considered_weight,
            "total_weight": self.total_weight,
            "passed": self.passed,
            "failed": self.failed,
            "inconclusive": self.inconclusive,
            "fails_by_severity": self.fails_by_severity,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ScoreBreakdown":
        return cls(
            score=d["score"],
            semaphore=d["semaphore"],
            grade_icon=d["grade_icon"],
            earned_weight=d["earned_weight"],
            considered_weight=d["considered_weight"],
            total_weight=d["total_weight"],
            passed=d["passed"],
            failed=d["failed"],
            inconclusive=d["inconclusive"],
            fails_by_severity=d.get("fails_by_severity") or {},
        )


def _semaphore(score: int) -> tuple[str, str]:
    if score >= GREEN_THRESHOLD:
        return "verde", "🟢"
    if score >= YELLOW_THRESHOLD:
        return "amarelo", "🟡"
    return "vermelho", "🔴"


def compute_score(results: Iterable[CheckResult]) -> ScoreBreakdown:
    results = list(results)

    earned = 0
    considered = 0
    total = 0
    passed = failed = inconclusive = 0
    fails_by_severity: Dict[str, int] = {
        Severity.CRITICA: 0,
        Severity.ALTA: 0,
        Severity.MEDIA: 0,
        Severity.BAIXA: 0,
    }

    for r in results:
        weight = SEVERITY_WEIGHT.get(r.severity, 1)
        total += weight
        if r.status == Status.PASS:
            passed += 1
            considered += weight
            earned += weight
        elif r.status == Status.FAIL:
            failed += 1
            considered += weight
            fails_by_severity[r.severity] = fails_by_severity.get(r.severity, 0) + 1
        else:  # INCONCLUSO -> neutral (excluded from denominator)
            inconclusive += 1

    if considered == 0:
        score = 0
    else:
        score = round(100 * earned / considered)

    semaphore, icon = _semaphore(score)

    return ScoreBreakdown(
        score=score,
        semaphore=semaphore,
        grade_icon=icon,
        earned_weight=earned,
        considered_weight=considered,
        total_weight=total,
        passed=passed,
        failed=failed,
        inconclusive=inconclusive,
        fails_by_severity=fails_by_severity,
    )


def summarize_fails(results: Iterable[CheckResult]) -> str:
    """A one-line executive summary like '2 críticos, 1 alto, 0 médios'."""
    breakdown = compute_score(results)
    f = breakdown.fails_by_severity
    return (
        f"{f.get(Severity.CRITICA, 0)} crítico(s), "
        f"{f.get(Severity.ALTA, 0)} alto(s), "
        f"{f.get(Severity.MEDIA, 0)} médio(s), "
        f"{f.get(Severity.BAIXA, 0)} baixo(s)"
    )
