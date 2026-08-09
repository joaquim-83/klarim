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
import uuid
from typing import List, Optional

import httpx

from .checks.api_security import check_api_security
from .checks.cookies import check_cookies
from .checks.cors import check_cors
from .checks.credentials import check_credentials
from .checks.dependencies import check_dependencies
from .checks.dns_security import check_dns_security
from .checks.email_security import check_email_security
from .checks.error_disclosure import check_error_disclosure
from .checks.exposure import check_exposure
from .checks.form_security import check_form_security
from .checks.headers import check_headers
from .checks.https_redirect import check_https_redirect
from .checks.infrastructure_urls import check_infrastructure_urls
from .checks.jwt_analysis import check_jwt
from .checks.rate_limit import check_rate_limit
from .checks.redirect import check_open_redirect
from .checks.ssl import check_ssl
from .checks.subdomain import check_subdomain_takeover
from .checks.tls_ciphers import check_tls_ciphers
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
    # KL-149 — 13 checks novos.
    "cors": check_cors,
    "cookies": check_cookies,
    "https_redirect": check_https_redirect,
    "open_redirect": check_open_redirect,
    "error_disclosure": check_error_disclosure,
    "jwt": check_jwt,
    "form_security": check_form_security,
    "dns": check_dns_security,
    "dependencies": check_dependencies,
    "tls_ciphers": check_tls_ciphers,
    "subdomain": check_subdomain_takeover,
    "infrastructure": check_infrastructure_urls,
    # KL-154 — SPF/DKIM/DMARC importados do scanner (via adaptador). Check de superfície (DNS-only).
    "email_security": check_email_security,
    # rate_limit é o ÚLTIMO: dispara um mini-burst de 10 requests; rodando por último, seu efeito
    # (possível 429 no IP do gate) não contamina os demais checks.
    "rate_limit": check_rate_limit,
}
_DEFAULT_ORDER = ["headers", "ssl", "exposure", "credentials", "api",
                  "cors", "cookies", "https_redirect", "open_redirect", "error_disclosure",
                  "jwt", "form_security", "dns", "email_security", "dependencies", "tls_ciphers",
                  "subdomain", "infrastructure", "rate_limit"]
# Checks que comparam paths contra o fingerprint de SPA fallback (KL-147). headers/ssl/credentials
# NÃO têm paths para comparar → não recebem o fingerprint.
_SPA_AWARE = frozenset({"exposure", "api"})


async def _detect_spa_fallback(client: httpx.AsyncClient, base_url: str) -> Optional[dict]:
    """Probe de controle (KL-147): HEAD num path que CERTAMENTE não existe. Se responde 200, o
    alvo é um SPA/app com **fallback** (devolve o `index.html` para qualquer path) — captura o
    fingerprint (ETag + Content-Type + Content-Length) p/ os checks de exposição/API distinguirem
    o fallback de uma exposição real. **1 request extra por scan**; best-effort (qualquer erro →
    None e os checks rodam normalmente)."""
    probe = f"{base_url.rstrip('/')}/_klarim_gate_probe_{uuid.uuid4().hex[:8]}"
    try:
        r = await client.head(probe, follow_redirects=True)
        if r.status_code == 200:
            # KL-160: `last_modified` é o comparador de fallback quando o ETag é removido pelo
            # proxy (o Cloudflare tira o ETag → o guard do KL-147 ficava sem comparador primário).
            fp = {"etag": r.headers.get("etag"),
                  "content_type": (r.headers.get("content-type") or "").split(";")[0].strip(),
                  "content_length": r.headers.get("content-length"),
                  "last_modified": r.headers.get("last-modified")}
            logger.info("[gate] SPA fallback detectado (etag=%s, content_type=%s, cl=%s, lm=%s) — "
                        "paths com este fingerprint serão tratados como fallback, não exposição.",
                        fp["etag"], fp["content_type"], fp["content_length"], fp["last_modified"])
            return fp
    except Exception:  # noqa: BLE001 - probe best-effort; erro nunca derruba o gate
        pass
    return None


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
            # KL-147: só faz o probe se algum check spa-aware vai rodar (evita o request extra à toa).
            spa_fingerprint = None
            if _SPA_AWARE.intersection(enabled):
                spa_fingerprint = await _detect_spa_fallback(client, url)
            for name in _DEFAULT_ORDER:
                if name not in enabled:
                    continue
                fn = _CHECKS[name]
                try:
                    if name in _SPA_AWARE:
                        report.results.extend(await fn(client, url, config, spa_fingerprint))
                    else:
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
