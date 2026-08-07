"""KL-149 — check de SUBDOMAIN TAKEOVER. Passivo: resolve o CNAME de subdomínios comuns e, se
apontar para um serviço PaaS conhecido, faz um GET e procura a "impressão digital" de projeto
inexistente (ex.: 'no such app' no Heroku). Só consultas de leitura — nunca reivindica nada.

As resoluções DNS bloqueantes rodam em thread; `_resolve_cname` é o ponto de mock nos testes."""
from __future__ import annotations

import asyncio
from typing import List

import httpx

from ..models import Result, Severity, Status
from ..utils import _host

TAKEOVER_FINGERPRINTS = {
    "herokuapp.com": "no such app",
    "github.io": "there isn't a github pages site here",
    "s3.amazonaws.com": "nosuchbucket",
    "azurewebsites.net": "error 404 - web app not found",
    "pantheonsite.io": "404 error unknown site",
    "readme.io": "project doesnt exist",
    "surge.sh": "project not found",
    "bitbucket.io": "repository not found",
    "ghost.io": "the thing you were looking for is no longer here",
    "myshopify.com": "sorry, this shop is currently unavailable",
    "fastly.net": "fastly error: unknown domain",
    "wpengine.com": "the site you were looking for couldn't be found",
}

_COMMON_SUBDOMAINS = ["www", "app", "api", "admin", "staging", "dev", "blog",
                      "mail", "cdn", "docs", "static", "assets"]


def _resolve_cname(fqdn: str) -> List[str]:
    """Targets de CNAME de `fqdn` (lista, sem o ponto final). NoAnswer/NXDOMAIN → sem CNAME.
    Bloqueante."""
    import dns.resolver
    return [str(r.target).rstrip(".") for r in dns.resolver.resolve(fqdn, "CNAME")]


async def check_subdomain_takeover(client: httpx.AsyncClient, base_url: str, config=None) -> List[Result]:
    host = _host(base_url).split(":")[0]
    domain = ".".join(host.split(".")[-2:]) if host.count(".") >= 1 else host
    results: List[Result] = []

    for sub in _COMMON_SUBDOMAINS:
        fqdn = f"{sub}.{domain}"
        try:
            targets = await asyncio.to_thread(_resolve_cname, fqdn)
        except Exception:  # noqa: BLE001 - sem CNAME / erro DNS → nada a fazer
            continue
        for target in targets:
            for service, fingerprint in TAKEOVER_FINGERPRINTS.items():
                if service not in target:
                    continue
                try:
                    resp = await client.get(f"https://{fqdn}", follow_redirects=True)
                except httpx.HTTPError:
                    continue
                if fingerprint.lower() in resp.text.lower():
                    results.append(Result("subdomain_takeover", "subdomain", fqdn,
                                          Status.FAIL, Severity.CRITICAL,
                                          f"Possível takeover: {fqdn} → CNAME {target} ({service}, "
                                          f"serviço respondendo 'projeto inexistente')"))

    if not results:
        results.append(Result("subdomain_ok", "subdomain", domain, Status.PASS, Severity.HIGH,
                              "Sem risco de subdomain takeover detectado nos subdomínios comuns"))
    return results
