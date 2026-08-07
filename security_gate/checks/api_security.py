"""KL-141 (Prompt 3) — check de API SECURITY: (1) a raiz da API não lista os endpoints e (2) os
endpoints protegidos exigem autenticação (401/403), nunca respondem 200 sem auth.

NÃO é DAST: só um GET sem credenciais em cada endpoint, observando o status — nenhum payload."""
from __future__ import annotations

from typing import List, Optional

import httpx

from ..config import GateConfig
from ..models import Result, Severity, Status
from ..utils import matches_spa_fingerprint

# Marcadores que denunciam uma raiz de API "vazando" o mapa de endpoints.
_LEAK_MARKERS = ("endpoints", '"endpoints"', "routes", "/api/admin", "/webhooks",
                 "scanner_version", "payments_enabled")


async def check_api_security(client: httpx.AsyncClient, base_url: str,
                             config: GateConfig = None,
                             spa_fingerprint: Optional[dict] = None) -> List[Result]:
    """Raiz da API limpa + endpoints protegidos exigindo auth. `spa_fingerprint` (KL-147): um 200
    que casa o fallback de SPA NÃO é um endpoint real → PASS (não FAIL de "sem auth")."""
    config = config or GateConfig(target=base_url)
    base = base_url.rstrip("/")
    results: List[Result] = []

    # 1. A raiz da API não pode listar endpoints.
    api_url = f"{base}{config.api_root_path}"
    try:
        r = await client.get(api_url)
        if r.status_code == 200:
            low = r.text.lower()
            if any(marker in low for marker in _LEAK_MARKERS):
                results.append(Result(
                    check="api_root_leaks", category="api", path=config.api_root_path,
                    status=Status.FAIL, severity=Severity.HIGH,
                    detail="Raiz da API expõe a listagem de endpoints", http_status=200))
            else:
                results.append(Result(
                    check="api_root_clean", category="api", path=config.api_root_path,
                    status=Status.PASS, severity=Severity.HIGH,
                    detail="Raiz da API não expõe endpoints", http_status=200))
        else:
            results.append(Result(
                check="api_root_clean", category="api", path=config.api_root_path,
                status=Status.PASS, severity=Severity.HIGH,
                detail=f"Raiz da API retorna {r.status_code}", http_status=r.status_code))
    except httpx.HTTPError:
        pass   # raiz inacessível não é vazamento

    # 2. Cada endpoint protegido deve exigir auth (401/403), nunca 200 sem credencial.
    for endpoint in config.protected_endpoints:
        url = f"{base}{endpoint}"
        try:
            r = await client.get(url)
        except httpx.HTTPError:
            continue
        if r.status_code == 200:
            # KL-147: 200 que casa o fallback de SPA não é endpoint real → PASS.
            if matches_spa_fingerprint(r, spa_fingerprint):
                results.append(Result(
                    check="api_protected", category="api", path=endpoint,
                    status=Status.PASS, severity=Severity.CRITICAL,
                    detail=f"Endpoint {endpoint}: fallback de SPA (não é endpoint real)",
                    http_status=200))
                continue
            results.append(Result(
                check="api_unprotected", category="api", path=endpoint,
                status=Status.FAIL, severity=Severity.CRITICAL,
                detail=f"Endpoint protegido {endpoint} acessível SEM auth (HTTP 200)",
                http_status=200))
        elif r.status_code in (401, 403):
            results.append(Result(
                check="api_protected", category="api", path=endpoint,
                status=Status.PASS, severity=Severity.CRITICAL,
                detail=f"Endpoint {endpoint} exige auth ({r.status_code})", http_status=r.status_code))
        else:
            results.append(Result(
                check="api_protected", category="api", path=endpoint,
                status=Status.PASS, severity=Severity.HIGH,
                detail=f"Endpoint {endpoint} retorna {r.status_code}", http_status=r.status_code))

    return results
