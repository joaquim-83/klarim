"""KL-141 (Prompt 3) — formatação do relatório do Gate para terminal (humano) e JSON (CI)."""
from __future__ import annotations

import json

from ..models import GateReport, Status

_CATEGORY_LABELS = [
    ("headers", "Headers"),
    ("ssl", "SSL/TLS"),
    ("exposure", "Exposure"),
    ("credentials", "Credentials"),
    ("api", "API Security"),
]
_ICON = {Status.PASS: "✅", Status.FAIL: "❌", Status.ERROR: "⚠️", Status.SKIP: "⏭️"}


def format_terminal(report: GateReport, quiet: bool = False) -> str:
    """Relatório legível: por categoria, com ícone/score/veredito. `quiet` omite os PASS."""
    lines = [f"\n🔍 Klarim Security Gate — {report.url}", "━" * 50]
    if report.error:
        lines.append(f"⚠️  Erro de execução: {report.error}")

    by_cat: dict = {}
    for r in report.results:
        by_cat.setdefault(r.category, []).append(r)

    for cat, label in _CATEGORY_LABELS:
        if cat not in by_cat:
            continue
        rows = [r for r in by_cat[cat] if not (quiet and r.status == Status.PASS)]
        if not rows:
            continue
        lines.append(f"\n{label}")
        for r in rows:
            icon = _ICON.get(r.status, "•")
            name = r.check.replace("header_", "").replace("_", " ").title()
            lines.append(f"  {icon} {name:.<30s} {r.detail}")

    lines.append("\n" + "━" * 50)
    semaphore = "🟢" if report.score >= 90 else "🟡" if report.score >= 50 else "🔴"
    lines.append(f"Score: {report.score}/100 {semaphore}")
    lines.append(f"Critical: {report.critical_count} | High: {report.high_count} "
                 f"| Medium: {report.medium_count}")
    lines.append(f"Duração: {report.duration_ms}ms")
    lines.append("✅ PASSED — pode subir" if report.passed else "❌ FAILED — problemas encontrados")
    return "\n".join(lines)


def format_json(report: GateReport) -> str:
    """Relatório em JSON (integração/CI). `ensure_ascii=False` preserva os acentos PT-BR."""
    return json.dumps({
        "url": report.url,
        "score": report.score,
        "passed": report.passed,
        "critical": report.critical_count,
        "high": report.high_count,
        "medium": report.medium_count,
        "duration_ms": report.duration_ms,
        "error": report.error,
        "results": [
            {"check": r.check, "category": r.category, "path": r.path,
             "status": r.status.value, "severity": r.severity.value, "detail": r.detail}
            for r in report.results
        ],
    }, indent=2, ensure_ascii=False)
