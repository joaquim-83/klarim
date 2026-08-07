"""KL-149 — check de OPEN REDIRECT. Passivo: para cada parâmetro de redirect comum, faz um GET
(sem seguir o redirect) com um domínio-sonda externo e vê se o servidor devolve um `Location` que
aponta para ele. NUNCA navega para o destino; só observa o header. 1 finding basta."""
from __future__ import annotations

from typing import List

import httpx

from ..models import Result, Severity, Status

REDIRECT_PARAMS = ["redirect", "next", "url", "return_to", "callback", "return",
                   "returnUrl", "redirect_uri", "dest", "destination", "continue", "goto"]

# Domínio-sonda que NÃO existe (nunca é acessado — só procurado no Location).
_PROBE = "https://klarim-gate-openredirect-probe.example"


async def check_open_redirect(client: httpx.AsyncClient, base_url: str, config=None) -> List[Result]:
    base = base_url.rstrip("/")
    for param in REDIRECT_PARAMS:
        url = f"{base}/?{param}={_PROBE}"
        try:
            r = await client.get(url, follow_redirects=False)
        except httpx.HTTPError:
            continue
        location = r.headers.get("location", "")
        if r.status_code in (301, 302, 303, 307, 308) and _PROBE in location:
            return [Result("open_redirect", "redirect", f"?{param}=", Status.FAIL, Severity.HIGH,
                           f"Open redirect via ?{param}= (Location aponta p/ domínio externo)")]

    return [Result("redirect_ok", "redirect", "/", Status.PASS, Severity.HIGH,
                   "Sem open redirect detectado nos parâmetros comuns")]
