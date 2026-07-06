"""Testes do módulo reporter (geração de PDF).

Constroem um ScanReport sintético (sem rede) e verificam que os dois PDFs são
gerados com um cabeçalho PDF válido. Se as bibliotecas nativas do WeasyPrint não
estiverem disponíveis no ambiente, os testes são pulados (mantém o CI robusto).
"""

from __future__ import annotations

import asyncio

import pytest

from scanner.runner import ScanReport
from scanner.scoring import compute_score
from scanner.checks.base import CheckResult, Status, Severity

# Guard: WeasyPrint precisa de libs nativas (pango/cairo). Pula se ausentes.
try:
    from weasyprint import HTML

    HTML(string="<p>x</p>").write_pdf()
    from reporter import generate_executive_pdf, generate_technical_pdf

    _RENDER_OK = True
except Exception:  # noqa: BLE001
    _RENDER_OK = False

pytestmark = pytest.mark.skipif(
    not _RENDER_OK, reason="bibliotecas nativas do WeasyPrint indisponíveis"
)


def _sample_report() -> ScanReport:
    results = [
        CheckResult("HTTPS ativo", Status.PASS, Severity.CRITICA, "ok",
                    check_id="check_01_https"),
        CheckResult("SRI ausente em scripts externos", Status.FAIL, Severity.ALTA,
                    "3 de 3 scripts externos sem SRI (100%).",
                    check_id="check_13_sri",
                    details={"without_sri_urls": ["https://cdn.x.com/a.js"]}),
        CheckResult("Scripts de fontes arriscadas", Status.FAIL, Severity.ALTA,
                    "1 script arriscado.", check_id="check_14_risky_sources",
                    details={"risky_scripts": [
                        {"url": "https://u.github.io/a.js", "reason": "GitHub Pages"}]}),
        CheckResult("TLS 1.2+ only", Status.INCONCLUSO, Severity.ALTA, "n/d",
                    check_id="check_04_tls"),
        CheckResult("Domínios externos carregando scripts", Status.PASS,
                    Severity.MEDIA, "2 domínios.",
                    check_id="check_15_external_domains",
                    details={"external_domains": ["a.com", "b.com"]}),
    ]
    return ScanReport(
        url="https://example.com",
        started_at="2026-07-06T00:00:00+00:00",
        finished_at="2026-07-06T00:00:30+00:00",
        duration_s=30.0,
        results=results,
        score=compute_score(results),
    )


def test_executive_pdf_renders():
    pdf = asyncio.run(generate_executive_pdf(_sample_report(), "https://example.com"))
    assert pdf[:5] == b"%PDF-"
    assert len(pdf) > 2000


def test_technical_pdf_renders():
    pdf = asyncio.run(generate_technical_pdf(_sample_report(), "https://example.com"))
    assert pdf[:5] == b"%PDF-"
    assert len(pdf) > 2000
