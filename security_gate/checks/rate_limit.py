"""KL-149 — check de RATE LIMITING. Passivo (mini-burst): envia até 10 GETs rápidos por endpoint
e verifica se algum retorna 429 (Too Many Requests). Teto de 10 requests — nunca é um DoS."""
from __future__ import annotations

from typing import List

import httpx

from ..models import Result, Severity, Status

_MAX_REQUESTS = 10


async def check_rate_limit(client: httpx.AsyncClient, base_url: str, config=None) -> List[Result]:
    """Se algum endpoint devolve 429 dentro de 10 requests → há rate limit (PASS). Nenhum 429 em
    nenhum endpoint → sem rate limit detectado (FAIL). Endpoints configuráveis via YAML."""
    base = base_url.rstrip("/")
    endpoints = list(getattr(config, "rate_limit_endpoints", None) or ["/", "/api/"])

    for endpoint in endpoints:
        url = f"{base}{endpoint}"
        for i in range(_MAX_REQUESTS):
            try:
                r = await client.get(url)
            except httpx.HTTPError:
                break
            if r.status_code == 429:
                return [Result("rate_limit_ok", "rate_limit", endpoint, Status.PASS, Severity.HIGH,
                               f"Rate limit ativo em {endpoint} (429 após {i + 1} requests)")]

    return [Result("rate_limit_missing", "rate_limit", "/", Status.FAIL, Severity.HIGH,
                   f"Sem rate limiting detectado ({_MAX_REQUESTS} requests sem 429) em {endpoints}")]
