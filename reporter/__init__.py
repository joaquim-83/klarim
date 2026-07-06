"""Klarim reporter — geração de relatórios PDF a partir de um ScanReport.

Uso:

    from reporter import generate_executive_pdf, generate_technical_pdf

    pdf_bytes = await generate_executive_pdf(scan_report, "https://exemplo.com")
"""

from __future__ import annotations

from .generator import (
    generate_executive_pdf,
    generate_technical_pdf,
    pdf_filename,
    report_id,
    site_name,
)

__all__ = [
    "generate_executive_pdf",
    "generate_technical_pdf",
    "pdf_filename",
    "report_id",
    "site_name",
]
