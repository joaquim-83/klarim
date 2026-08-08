"""KL-141 (Prompt 3) — formatação do relatório do Gate para terminal (humano) e JSON (CI).

KL-154 — os resultados são agrupados em duas camadas: **Surface** (servidor + DNS: headers, SSL,
DNS, HTTPS e o e-mail SPF/DKIM/DMARC importado do scanner) e **Deep** (exposição + código:
exposure, credentials, CORS, etc.). O agrupamento é derivado do `result.category`."""
from __future__ import annotations

import json

from ..models import GateReport, Status

_CATEGORY_LABELS = [
    ("headers", "Headers"),
    ("ssl", "SSL/TLS"),
    ("dns", "DNS (DNSSEC/CAA)"),
    ("https", "HTTPS Redirect"),
    ("surface", "E-mail (SPF/DKIM/DMARC)"),   # KL-154 — checks de e-mail importados do scanner
    ("exposure", "Exposure"),
    ("credentials", "Credentials"),
    ("api", "API Security"),
    # KL-149
    ("cors", "CORS"),
    ("cookies", "Cookies"),
    ("redirect", "Open Redirect"),
    ("error_disclosure", "Error Disclosure"),
    ("jwt", "JWT"),
    ("forms", "Forms"),
    ("dependencies", "Dependencies (CVE)"),
    ("tls", "TLS Ciphers"),
    ("subdomain", "Subdomain Takeover"),
    ("infrastructure", "Infrastructure URLs"),
    ("rate_limit", "Rate Limiting"),
]
_CAT_ORDER = {cat: i for i, (cat, _label) in enumerate(_CATEGORY_LABELS)}

# KL-154 — categorias de SUPERFÍCIE (config de servidor + DNS). Tudo o mais é DEEP (exposição/código).
_SURFACE_CATEGORIES = frozenset({"surface", "headers", "ssl", "dns", "https"})

_ICON = {Status.PASS: "✅", Status.FAIL: "❌", Status.ERROR: "⚠️", Status.SKIP: "⏭️"}

# Macro-grupos, na ordem de exibição.
_MACRO_GROUPS = (
    ("surface", "Surface (servidor + DNS)"),
    ("deep", "Deep (exposição + código)"),
)


def _macro(category: str) -> str:
    """Macro-grupo (`surface`/`deep`) de uma categoria de check (KL-154)."""
    return "surface" if category in _SURFACE_CATEGORIES else "deep"


def _summary(report: GateReport) -> dict:
    """Contagem pass/fail/checks por macro-grupo (surface/deep) — usada no JSON (KL-154)."""
    out = {}
    for macro, _label in _MACRO_GROUPS:
        rows = [r for r in report.results if _macro(r.category) == macro]
        out[macro] = {
            "pass": sum(1 for r in rows if r.status == Status.PASS),
            "fail": sum(1 for r in rows if r.status == Status.FAIL),
            "checks": len(rows),
        }
    return out


def format_terminal(report: GateReport, quiet: bool = False) -> str:
    """Relatório legível agrupado em Surface/Deep, com ícone/score/veredito. `quiet` omite os PASS."""
    lines = [f"\n🔍 Klarim Security Gate — {report.url}", "━" * 50]
    if report.error:
        lines.append(f"⚠️  Erro de execução: {report.error}")

    by_cat: dict = {}
    for r in report.results:
        by_cat.setdefault(r.category, []).append(r)
    # Categorias presentes, ordenadas pela ordem de `_CATEGORY_LABELS` (desconhecidas → fim → deep).
    ordered_cats = sorted(by_cat, key=lambda c: _CAT_ORDER.get(c, len(_CATEGORY_LABELS)))

    for macro, macro_label in _MACRO_GROUPS:
        rows = []
        for cat in ordered_cats:
            if _macro(cat) != macro:
                continue
            for r in by_cat[cat]:
                if quiet and r.status == Status.PASS:
                    continue
                rows.append(r)
        if not rows:
            continue
        lines.append(f"\n{macro_label}")
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
        "summary": _summary(report),   # KL-154 — contagens por camada (surface/deep)
        "results": [
            {"check": r.check, "category": r.category, "path": r.path,
             "status": r.status.value, "severity": r.severity.value, "detail": r.detail}
            for r in report.results
        ],
    }, indent=2, ensure_ascii=False)
