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
from fastapi.responses import JSONResponse

from scanner import run_scan, summarize_fails
from scanner import __version__ as scanner_version

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
        "endpoints": ["/health", "/scan?url=", "/scan/summary?url="],
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


async def _safe_scan(url: str):
    try:
        return await run_scan(url)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"URL inválida: {exc}") from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Falha na varredura: {exc!r}") from exc
