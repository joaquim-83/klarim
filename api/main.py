"""Klarim API (FastAPI) — MVP placeholder.

Exposes a minimal surface for the self-service semaphore described in the spec.
It runs a scan on demand and returns either the full technical report or the
free executive summary (semáforo). Persistence (Postgres), queueing (Redis) and
payments are intentionally out of scope for this placeholder.

Run locally:

    uvicorn api.main:app --reload --port 8000

Then:

    curl "http://localhost:8000/scan?url=https://example.com"
    curl "http://localhost:8000/scan/summary?url=https://example.com"
"""

from __future__ import annotations

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse, Response

from scanner import run_scan, summarize_fails
from scanner import __version__ as scanner_version
from reporter import (
    generate_executive_pdf,
    generate_technical_pdf,
    pdf_filename,
)

app = FastAPI(
    title="Klarim API",
    version="0.1.0",
    description="O alarme que toca antes do ataque — scanner passivo de segurança web.",
)


@app.get("/")
async def root() -> dict:
    return {
        "name": "Klarim API",
        "scanner_version": scanner_version,
        "endpoints": [
            "/health",
            "/scan?url=",
            "/scan/summary?url=",
            "/report/executive?url=",
            "/report/technical?url=",
        ],
        "disclaimer": (
            "Varredura passiva (GET/HEAD a URLs públicas). Não realiza ataques, "
            "brute-force ou acesso autenticado."
        ),
    }


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@app.get("/scan")
async def scan_full(url: str = Query(..., description="URL alvo (http/https).")) -> JSONResponse:
    """Full technical report — the paid tier in the business model."""
    report = await _safe_scan(url)
    return JSONResponse(report.to_dict())


@app.get("/scan/summary")
async def scan_summary(url: str = Query(..., description="URL alvo.")) -> dict:
    """Free executive semaphore — score + counts, no per-check detail."""
    report = await _safe_scan(url)
    score = report.score
    return {
        "url": report.url,
        "score": score.score if score else None,
        "semaphore": score.semaphore if score else None,
        "grade_icon": score.grade_icon if score else None,
        "summary": summarize_fails(report.results),
        "message": (
            "Encaminhe este resumo ao responsável pelo seu site. "
            "Relatório técnico completo disponível na versão paga."
        ),
    }


@app.get("/report/executive")
async def report_executive(url: str = Query(..., description="URL alvo.")) -> Response:
    """Relatório executivo em PDF (semáforo + linguagem de negócio)."""
    report = await _safe_scan(url)
    pdf = await _safe_pdf(generate_executive_pdf, report, url)
    return _pdf_response(pdf, pdf_filename("executive", url, report.started_at))


@app.get("/report/technical")
async def report_technical(url: str = Query(..., description="URL alvo.")) -> Response:
    """Relatório técnico em PDF (checks detalhados + correções + inventário)."""
    report = await _safe_scan(url)
    pdf = await _safe_pdf(generate_technical_pdf, report, url)
    return _pdf_response(pdf, pdf_filename("technical", url, report.started_at))


def _pdf_response(pdf: bytes, filename: str) -> Response:
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{filename}"'},
    )


async def _safe_scan(url: str):
    try:
        return await run_scan(url)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"URL inválida: {exc}") from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Falha na varredura: {exc!r}") from exc


async def _safe_pdf(fn, report, url: str) -> bytes:
    try:
        return await fn(report, url)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"Falha ao gerar PDF: {exc!r}") from exc
