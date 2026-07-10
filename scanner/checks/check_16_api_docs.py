"""Check 16 — Documentação de API exposta (Severidade: ALTA).

Passivo: GET em paths comuns de doc de API. Só é FAIL quando o corpo tem
marcadores fortes de documentação (Swagger/OpenAPI/GraphiQL/ReDoc) — assim o
catch-all de SPA (que responde 200 com o index.html em qualquer path) não vira
falso positivo.
"""

from __future__ import annotations

from urllib.parse import urljoin

import httpx

from .base import CheckResult, Status, Severity, fetch, base_url

ORDER = 16
CHECK_ID = "check_16_api_docs"
NAME = "Documentação de API exposta"

_PATHS = [
    "docs", "api/docs", "swagger", "swagger-ui", "swagger-ui.html",
    "openapi.json", "api-docs", "redoc", "graphql", "graphiql",
]

# Marcadores fortes que uma SPA comum NÃO tem (evita falso positivo do catch-all).
_MARKERS = (
    "swagger-ui", "swaggerui", '"openapi"', '"swagger"', "redoc",
    "graphiql", "swagger ui", "spec-url",
)


async def check(url: str) -> CheckResult:
    root = base_url(url) + "/"
    found: list[dict] = []
    probed: list[str] = []

    for path in _PATHS:
        target = urljoin(root, path)
        probed.append(path)
        try:
            resp = await fetch(target, method="GET", follow_redirects=True)
        except (httpx.HTTPError, OSError):
            continue
        if resp.status_code != 200:
            continue
        body = resp.text[:16000].lower()
        hit = next((mk for mk in _MARKERS if mk in body), None)
        if hit:
            found.append({"path": path, "marker": hit})

    if found:
        listing = ", ".join(f"/{f['path']}" for f in found)
        return CheckResult(
            name=NAME, status=Status.FAIL, severity=Severity.ALTA,
            evidence=f"Documentação de API acessível publicamente em: {listing}.",
            details={"found": found, "probed": probed})

    return CheckResult(
        name=NAME, status=Status.PASS, severity=Severity.ALTA,
        evidence=f"Nenhuma documentação de API exposta ({len(probed)} caminho(s) testado(s)).",
        details={"probed": probed})
