"""Klarim reporter — geração de relatórios PDF a partir de um ScanReport.

Uso:

    from reporter import generate_executive_pdf, generate_technical_pdf

    pdf_bytes = await generate_executive_pdf(scan_report, "https://exemplo.com")

As funções de PDF são carregadas **sob demanda** (PEP 562 `__getattr__`) para que
importar submódulos leves — como `reporter.risk_messages` (KL-20) — não puxe o
WeasyPrint (pesado, com libs nativas) para containers que não geram PDF.
"""

from __future__ import annotations

_LAZY = ("generate_executive_pdf", "generate_technical_pdf", "pdf_filename",
         "report_id", "site_name")

__all__ = list(_LAZY)


def __getattr__(name):
    if name in _LAZY:
        from . import generator
        return getattr(generator, name)
    raise AttributeError(f"module 'reporter' has no attribute {name!r}")
