"""KL-141 — orquestrador do Security Gate. NÃO contém lógica de check: só monta o client,
roda os checks habilitados e agrega em `GateReport`.

Cache busting (comentário 2 do card): TODO request leva headers anti-cache — um scan pós-deploy
não pode ler uma resposta cacheada (do CDN/browser) de ANTES do deploy. Se `deploy_ts` for
fornecido, o engine avisa (log) quando o `Last-Modified` da raiz é mais antigo que o deploy.
User-Agent honesto: `Klarim Security Gate/1.0`."""
from __future__ import annotations

import email.utils
import logging
import time
from typing import List, Optional

import httpx

from .checks.api_security import check_api_security
from .checks.credentials import check_credentials
from .checks.exposure import check_exposure
from .checks.headers import check_headers
from .checks.ssl import check_ssl
from .config import GateConfig
from .models import GateReport, Result, Severity, Status

logger = logging.getLogger("security_gate")

# Headers anti-cache em TODOS os requests (comentário 2 do card).
ANTI_CACHE_HEADERS = {
    "Cache-Control": "no-cache, no-store, must-revalidate",
    "Pragma": "no-cache",
    "If-None-Match": "",
    "User-Agent": "Klarim Security Gate/1.0",
}

# Mapa nome→função. O engine só orquestra; a lógica vive em cada módulo de check.
_CHECKS = {
    "headers": check_headers,
    "ssl": check_ssl,
    "exposure": check_exposure,
    "credentials": check_credentials,
    "api": check_api_security,
}
_DEFAULT_ORDER = ["headers", "ssl", "exposure", "credentials", "api"]


async def _warn_if_stale(client: httpx.AsyncClient, url: str, deploy_ts: float) -> None:
    """Avisa (log) se a raiz responde com `Last-Modified` mais antigo que o deploy — sinal
    de resposta cacheada (o gate estaria a validar o build ANTERIOR). Best-effort."""
    try:
        r = await client.head(url)
        lm = r.headers.get("last-modified")
        if lm:
            ts = email.utils.parsedate_to_datetime(lm).timestamp()
            if ts < deploy_ts:
                logger.warning("[gate] Last-Modified (%s) é anterior ao deploy — possível "
                               "resposta cacheada; verifique o cache-busting do CDN.", lm)
    except Exception:  # noqa: BLE001 - aviso best-effort, nunca derruba o gate
        pass


async def run_all(url: str, timeout: int = 60, checks: Optional[List[str]] = None,
                  config: Optional[GateConfig] = None,
                  deploy_ts: Optional[float] = None) -> GateReport:
    """Roda os checks habilitados contra `url` e devolve o `GateReport` agregado.

    `checks` filtra quais rodam (default: headers+ssl+exposure+credentials+api). `config`
    (GateConfig) alimenta allowlist de exposição, endpoints protegidos, thresholds — cada check
    recebe `config`. Um check que estoure inesperadamente vira um `Result` ERROR (nunca derruba o
    gate inteiro)."""
    start = time.monotonic()
    report = GateReport(url=url)
    if config is None:
        config = GateConfig(target=url)
    enabled = checks if checks is not None else (config.checks or _DEFAULT_ORDER)

    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True,
                                     verify=True, headers=ANTI_CACHE_HEADERS) as client:
            if deploy_ts is not None:
                await _warn_if_stale(client, url, deploy_ts)
            for name in _DEFAULT_ORDER:
                if name not in enabled:
                    continue
                fn = _CHECKS[name]
                try:
                    report.results.extend(await fn(client, url, config))
                except Exception as exc:  # noqa: BLE001 - um check ruim não derruba o gate
                    logger.exception("[gate] check %s falhou", name)
                    report.results.append(Result(
                        check=f"{name}_error", category=name, path="/",
                        status=Status.ERROR, severity=Severity.INFO,
                        detail=f"Erro no check {name}: {exc!r}"))
    except Exception as exc:  # noqa: BLE001 - falha de infra (client/DNS) → report com erro
        report.error = f"{exc!r}"

    report.duration_ms = int((time.monotonic() - start) * 1000)
    return report
