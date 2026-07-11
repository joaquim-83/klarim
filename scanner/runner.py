"""Scan orchestration.

``run_scan(url)`` executes every registered check (discovered dynamically and
ordered by ``scanner.checks.ALL_CHECKS``) against a single target and returns a
:class:`ScanReport` bundling every :class:`CheckResult` plus the computed score.
The number of checks is dynamic and grows as new ``check_*`` modules are added.

The checks run **in sequence** (spec: "executar os checks em sequência"),
which also keeps the per-domain rate limit trivially satisfied.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import List, Optional

from .checks import ALL_CHECKS, FREE_CHECKS
from .checks.base import CheckResult, Status, Severity, normalize_url
from .checks.classifications import classify
from .scoring import ScoreBreakdown, compute_score


@dataclass
class ScanReport:
    url: str
    started_at: str
    finished_at: str
    duration_s: float
    results: List[CheckResult] = field(default_factory=list)
    score: Optional[ScoreBreakdown] = None

    def to_dict(self) -> dict:
        return {
            "url": self.url,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "duration_s": round(self.duration_s, 2),
            "score": self.score.to_dict() if self.score else None,
            "results": [r.to_dict() for r in self.results],
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ScanReport":
        from .scoring import ScoreBreakdown

        return cls(
            url=d["url"],
            started_at=d["started_at"],
            finished_at=d["finished_at"],
            duration_s=d.get("duration_s", 0.0),
            results=[CheckResult.from_dict(r) for r in d.get("results", [])],
            score=ScoreBreakdown.from_dict(d["score"]) if d.get("score") else None,
        )


async def run_scan(url: str, full: bool = True) -> ScanReport:
    """Run the registered checks against ``url`` sequentially and score them.

    ``full=True`` (padrão) roda todos os checks (tier pago, 29). ``full=False`` roda
    só o tier gratuito (15 primeiros, KL-27) — economiza tempo de scan e requests
    DNS/API do funil público.
    """
    target = normalize_url(url)
    loop = asyncio.get_event_loop()
    started_at = datetime.now(timezone.utc)
    t0 = loop.time()

    checks = ALL_CHECKS if full else FREE_CHECKS
    results: List[CheckResult] = []
    for check_id, check_fn in checks:
        try:
            result = await check_fn(target)
        except Exception as exc:  # noqa: BLE001 - one bad check must not kill the scan
            result = CheckResult(
                name=check_id,
                status=Status.INCONCLUSO,
                severity=Severity.MEDIA,
                evidence=f"Erro inesperado ao executar o check: {exc!r}",
            )
        result.check_id = check_id
        # Carimba OWASP/CWE/LGPD (KL-34/35) — metadata, não afeta o score. Centralizado
        # aqui (onde o check_id é setado) para não editar as ~100 return sites dos checks.
        cls = classify(check_id)
        result.owasp, result.cwe, result.lgpd = cls.owasp, cls.cwe, cls.lgpd
        results.append(result)

    duration = loop.time() - t0
    finished_at = datetime.now(timezone.utc)
    score = compute_score(results)

    return ScanReport(
        url=target,
        started_at=started_at.isoformat(),
        finished_at=finished_at.isoformat(),
        duration_s=duration,
        results=results,
        score=score,
    )


def scan(url: str, full: bool = True) -> ScanReport:
    """Synchronous convenience wrapper around :func:`run_scan`."""
    return asyncio.run(run_scan(url, full=full))


# --------------------------------------------------------------------------- #
# Pretty printing (used by the CLI / quick test)
# --------------------------------------------------------------------------- #

_STATUS_ICON = {
    Status.PASS: "✅ PASS",
    Status.FAIL: "❌ FAIL",
    Status.INCONCLUSO: "⚠️  INCONCLUSO",
}


def format_report(report: ScanReport) -> str:
    lines: List[str] = []
    lines.append("=" * 72)
    lines.append(f"  KLARIM — Relatório de varredura")
    lines.append(f"  Alvo:  {report.url}")
    lines.append(f"  Data:  {report.started_at}  ({report.duration_s:.1f}s)")
    lines.append("=" * 72)

    for i, r in enumerate(report.results, start=1):
        status = _STATUS_ICON.get(r.status, r.status)
        lines.append(f"{i:>2}. [{status}] ({r.severity})  {r.name}")
        lines.append(f"     ↳ {r.evidence}")

    if report.score is not None:
        s = report.score
        lines.append("-" * 72)
        lines.append(
            f"  SCORE: {s.score}/100  {s.grade_icon} ({s.semaphore.upper()})"
        )
        lines.append(
            f"  PASS: {s.passed}   FAIL: {s.failed}   "
            f"INCONCLUSO: {s.inconclusive}"
        )
        f = s.fails_by_severity
        lines.append(
            "  Falhas por severidade — "
            f"Crítica: {f.get(Severity.CRITICA, 0)}, "
            f"Alta: {f.get(Severity.ALTA, 0)}, "
            f"Média: {f.get(Severity.MEDIA, 0)}, "
            f"Baixa: {f.get(Severity.BAIXA, 0)}"
        )
    lines.append("=" * 72)
    return "\n".join(lines)
