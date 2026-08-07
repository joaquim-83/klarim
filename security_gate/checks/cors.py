"""KL-149 — check de CORS misconfiguration. Passivo: 1 GET com um header `Origin` de teste,
observando o `Access-Control-Allow-Origin`/`-Credentials` da resposta. NUNCA ataca."""
from __future__ import annotations

from typing import List, Optional

import httpx

from ..models import Result, Severity, Status

_PROBE_ORIGIN = "https://klarim-gate-probe.test"


async def check_cors(client: httpx.AsyncClient, base_url: str, config=None) -> List[Result]:
    """Envia um Origin arbitrário e checa se o servidor o reflete (qualquer site poderia ler os
    dados). Pior caso = wildcard + credentials. Ausência de CORS ou origin ignorado = PASS."""
    try:
        r = await client.get(base_url, headers={"Origin": _PROBE_ORIGIN})
    except httpx.HTTPError as exc:
        return [Result("cors_error", "cors", "/", Status.ERROR, Severity.INFO,
                       f"Não foi possível verificar CORS: {exc!r}")]

    acao = r.headers.get("Access-Control-Allow-Origin", "")
    acac = (r.headers.get("Access-Control-Allow-Credentials", "") or "").strip().lower()

    if acao == _PROBE_ORIGIN:   # origin arbitrário refletido → qualquer site lê os dados
        return [Result("cors_reflect", "cors", "/", Status.FAIL, Severity.CRITICAL,
                       "CORS reflete origin arbitrário (Access-Control-Allow-Origin espelhado)")]
    if acao == "*" and acac == "true":
        return [Result("cors_wildcard_creds", "cors", "/", Status.FAIL, Severity.CRITICAL,
                       "CORS wildcard (*) COM Access-Control-Allow-Credentials: true")]
    if acao == "*":
        return [Result("cors_wildcard", "cors", "/", Status.FAIL, Severity.HIGH,
                       "CORS wildcard (Access-Control-Allow-Origin: *)")]
    return [Result("cors_ok", "cors", "/", Status.PASS, Severity.CRITICAL,
                   "CORS não aceita origin arbitrário")]
