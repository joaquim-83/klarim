"""KL-154 — checks de segurança de e-mail (SPF/DKIM/DMARC) para o Security Gate.

**Reuso do scanner** (regra do card: o Gate importa do scanner, não reimplementa): roda os MESMOS
checks 21/22/23 que o scan público usa e ADAPTA o resultado ao modelo do Gate (`scanner_adapter`).
Os imports do scanner são LAZY (dentro do `try`) — se o scanner mudar a interface ou a dependência
de DNS faltar, o Gate degrada para um `Result` ERROR (nunca derruba o gate inteiro). Passivo: os
checks só fazem consultas DNS TXT (SPF/DKIM/DMARC), nenhum request de ataque.

Por que é um check de SUPERFÍCIE: SPF/DKIM/DMARC são configuração de DNS pública — o dev precisa
saber, pós-deploy, se o domínio dele está protegido contra spoofing/phishing de e-mail."""
from __future__ import annotations

import importlib
from typing import List

from ..models import Result, Severity, Status
from .scanner_adapter import adapt_check_result

# (nome curto no Gate, módulo do scanner). Import por caminho (string) p/ manter o import lazy.
_EMAIL_CHECKS = (
    ("spf", "scanner.checks.check_21_spf"),
    ("dkim", "scanner.checks.check_22_dkim"),
    ("dmarc", "scanner.checks.check_23_dmarc"),
)


async def check_email_security(client, base_url: str, config=None) -> List[Result]:
    """Verifica SPF, DKIM e DMARC via os checks do scanner.

    `client`/`config` entram só por uniformidade da assinatura do engine — os checks do scanner
    fazem o próprio DNS (não usam o client HTTP do Gate)."""
    results: List[Result] = []

    for check_name, module_path in _EMAIL_CHECKS:
        try:
            module = importlib.import_module(module_path)      # lazy: só quando o check roda
            raw = await module.check(base_url)
            results.append(adapt_check_result(raw, check_name, "surface"))
        except Exception as exc:  # noqa: BLE001 - scanner indisponível/mudou → degrada, não quebra
            results.append(Result(
                check=check_name, category="surface", path="/",
                status=Status.ERROR, severity=Severity.INFO,
                detail=f"Erro ao verificar {check_name.upper()}: {exc!r}"))

    return results
