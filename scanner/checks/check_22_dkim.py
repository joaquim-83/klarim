"""Check 22 — DKIM ausente (Severidade: MÉDIA).

Passivo: tenta os seletores DKIM mais comuns (`{selector}._domainkey.{domain}`).
Algum com `v=DKIM1`/`p=` → PASS. Todos ausentes → FAIL (cobre 90%+ dos casos;
seletores exóticos podem dar falso positivo). Só INCONCLUSO se o DNS falhar de vez.
"""

from __future__ import annotations

import asyncio

from .base import CheckResult, Status, Severity, domain_of, registrable_domain
from . import dns_util

ORDER = 22
CHECK_ID = "check_22_dkim"
NAME = "DKIM (assinatura de e-mail)"

# Seletores DKIM comuns + provedores populares (inclui os usados no Brasil).
# Cobre 90%+ dos casos; seletores exóticos ainda podem dar falso positivo.
DKIM_SELECTORS = [
    "default", "google", "selector1", "selector2", "k1", "mail", "dkim", "s1", "s2",
    "resend",                                          # Resend (usado pelo klarim.net)
    "mandrill", "mailgun", "amazonses", "sendgrid",    # provedores transacionais
    "zoho", "locaweb", "titan",                        # populares no Brasil
]


async def check(url: str) -> CheckResult:
    domain = registrable_domain(domain_of(url))
    any_dns_ok = False
    for selector in DKIM_SELECTORS:
        records = await asyncio.to_thread(
            dns_util.resolve_txt, f"{selector}._domainkey.{domain}", 4.0)
        if records is None:
            continue  # erro nesse seletor; tenta o próximo
        any_dns_ok = True
        for r in records:
            low = r.lower()
            if "v=dkim1" in low or "p=" in low:
                return CheckResult(name=NAME, status=Status.PASS, severity=Severity.MEDIA,
                                   evidence=f"DKIM encontrado em {domain} (seletor '{selector}').",
                                   details={"selector": selector, "domain": domain})

    if not any_dns_ok:
        return CheckResult(name=NAME, status=Status.INCONCLUSO, severity=Severity.MEDIA,
                           evidence=f"Não foi possível consultar o DNS de {domain}.")

    return CheckResult(
        name=NAME, status=Status.FAIL, severity=Severity.MEDIA,
        evidence=f"Nenhum registro DKIM nos seletores comuns de {domain} — e-mails do "
                 f"domínio não são assinados digitalmente.",
        details={"domain": domain, "selectors_tried": DKIM_SELECTORS})
