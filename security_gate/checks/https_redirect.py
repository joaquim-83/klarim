"""KL-149 — check de HTTP→HTTPS redirect. Passivo: 1 GET na versão HTTP do alvo (sem seguir o
redirect) e confere se o servidor devolve 3xx com `Location: https://…`. Reusa o client do engine
com `follow_redirects=False` por request (o client base segue redirects)."""
from __future__ import annotations

from typing import List

import httpx

from ..models import Result, Severity, Status


async def check_https_redirect(client: httpx.AsyncClient, base_url: str, config=None) -> List[Result]:
    if not base_url.startswith("https://"):
        return [Result("https_redirect_skip", "https", "/", Status.SKIP, Severity.INFO,
                       "Site não usa HTTPS — redirect não se aplica")]

    http_url = "http://" + base_url[len("https://"):]
    try:
        r = await client.get(http_url, follow_redirects=False)
    except httpx.HTTPError as exc:
        return [Result("https_redirect_error", "https", "/", Status.ERROR, Severity.INFO,
                       f"Não foi possível verificar o redirect HTTP→HTTPS: {exc!r}")]

    location = (r.headers.get("location", "") or "").strip()
    if r.status_code in (301, 302, 303, 307, 308) and location.lower().startswith("https://"):
        return [Result("https_redirect_ok", "https", "/", Status.PASS, Severity.CRITICAL,
                       f"HTTP redireciona para HTTPS ({r.status_code})")]
    return [Result("https_redirect_missing", "https", "/", Status.FAIL, Severity.CRITICAL,
                   f"HTTP NÃO redireciona para HTTPS (status {r.status_code}, Location='{location[:60]}')")]
