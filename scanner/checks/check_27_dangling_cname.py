"""Check 27 — Dangling CNAME / subdomain takeover (Severidade: CRÍTICA).

Passivo: para subdomínios comuns, resolve o CNAME. Se aponta para um serviço
propenso a takeover (heroku/azure/s3/github.io/ghost…) **e** o alvo não existe
mais (NXDOMAIN), qualquer pessoa pode registrar o serviço e assumir o subdomínio.
Limitado a 10 subdomínios, 3s por lookup. DNS de vez indisponível → INCONCLUSO.
"""

from __future__ import annotations

import asyncio

from .base import CheckResult, Status, Severity, domain_of, registrable_domain
from . import dns_util

ORDER = 27
CHECK_ID = "check_27_dangling_cname"
NAME = "Dangling CNAME (subdomain takeover)"

_COMMON = ["www", "mail", "app", "api", "blog", "shop", "admin", "dev", "staging", "cdn"]

# Serviços onde um CNAME órfão permite takeover (o alvo pode ser registrado por qualquer um).
_TAKEOVER_SUFFIXES = (
    "herokuapp.com", "herokudns.com", "azurewebsites.net", "cloudapp.net",
    "trafficmanager.net", "s3.amazonaws.com", "s3-website", "ghost.io",
    "github.io", "wpengine.com", "pantheonsite.io", "fastly.net",
    "surge.sh", "bitbucket.io", "helpscoutdocs.com", "readme.io",
)


async def check(url: str) -> CheckResult:
    domain = registrable_domain(domain_of(url))
    dangling: list[dict] = []
    any_dns_ok = False

    for sub in _COMMON:
        fqdn = f"{sub}.{domain}"
        target = await asyncio.to_thread(dns_util.resolve_cname, fqdn, 3.0)
        if target is None:
            continue  # sem CNAME (ou erro) — não é dangling
        any_dns_ok = True
        if not any(target.endswith(suf) or suf in target for suf in _TAKEOVER_SUFFIXES):
            continue
        exists = await asyncio.to_thread(dns_util.host_exists, target, 3.0)
        if exists is False:  # o alvo do CNAME não existe mais → takeover possível
            dangling.append({"subdomain": fqdn, "cname": target})

    if dangling:
        d = dangling[0]
        return CheckResult(
            name=NAME, status=Status.FAIL, severity=Severity.CRITICA,
            evidence=f"{d['subdomain']} aponta (CNAME) para {d['cname']}, que não existe mais "
                     f"— risco de subdomain takeover.",
            details={"dangling": dangling})

    if not any_dns_ok:
        return CheckResult(name=NAME, status=Status.INCONCLUSO, severity=Severity.CRITICA,
                           evidence=f"Não foi possível verificar CNAMEs de {domain}.")

    return CheckResult(name=NAME, status=Status.PASS, severity=Severity.CRITICA,
                       evidence="Nenhum CNAME órfão nos subdomínios comuns (sem risco de takeover).")
