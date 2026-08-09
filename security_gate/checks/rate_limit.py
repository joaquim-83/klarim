"""KL-149 — check de RATE LIMITING. Passivo (mini-burst): envia até 10 GETs por endpoint e
verifica se algum retorna 429 (Too Many Requests). Teto de 10 requests — nunca é um DoS.

KL-160 — a rajada é CONCORRENTE (não sequencial): o leaky bucket do nginx "refilla" entre um
request e o próximo (o RTT costuma ser ≈ o intervalo de refill), então 10 requests SEQUENCIAIS
quase nunca disparam o 429 mesmo com rate limit ativo (falso negativo). 10 requests concorrentes
esgotam o burst e revelam o limite — continua ≤10 requests, passivo."""
from __future__ import annotations

import asyncio
from typing import List

import httpx

from ..models import Result, Severity, Status

_MAX_REQUESTS = 10


async def check_rate_limit(client: httpx.AsyncClient, base_url: str, config=None) -> List[Result]:
    """Se algum endpoint devolve 429 numa rajada CONCORRENTE de ≤10 requests → há rate limit (PASS).
    Nenhum 429 em nenhum endpoint → sem rate limit detectado (FAIL). Endpoints configuráveis via YAML."""
    base = base_url.rstrip("/")
    endpoints = list(getattr(config, "rate_limit_endpoints", None) or ["/", "/api/"])

    for endpoint in endpoints:
        url = f"{base}{endpoint}"
        try:
            resps = await asyncio.gather(*[client.get(url) for _ in range(_MAX_REQUESTS)],
                                         return_exceptions=True)
        except Exception:  # noqa: BLE001 - rajada best-effort; erro num endpoint → tenta o próximo
            continue
        hits = sum(1 for r in resps
                   if not isinstance(r, Exception) and getattr(r, "status_code", 0) == 429)
        if hits:
            return [Result("rate_limit_ok", "rate_limit", endpoint, Status.PASS, Severity.HIGH,
                           f"Rate limit ativo em {endpoint} ({hits}/{_MAX_REQUESTS} → 429)")]

    return [Result("rate_limit_missing", "rate_limit", "/", Status.FAIL, Severity.HIGH,
                   f"Sem rate limiting detectado ({_MAX_REQUESTS} requests sem 429) em {endpoints}")]
