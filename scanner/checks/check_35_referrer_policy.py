"""Check 35 — Referrer-Policy: qualidade (Severidade: BAIXA/MÉDIA, KL-32).

Avalia o *valor* da policy, não só a presença. ``unsafe-url`` vaza a URL completa
(inclusive query strings com dados sensíveis) para sites de terceiros — FAIL MÉDIA.
Ausência é uma lacuna (os navegadores usam ``strict-origin-when-cross-origin`` por
default desde 2021) — FAIL BAIXA. Passivo: só lê o header.
"""

from __future__ import annotations

import httpx

from .base import CheckResult, Status, Severity, fetch, with_scheme

ORDER = 35
CHECK_ID = "check_35_referrer_policy"
NAME = "Referrer-Policy (qualidade)"

# Ranking de valores (o navegador usa o último token que reconhece).
REFERRER_POLICY_RANKING = {
    "no-referrer": "seguro",
    "same-origin": "seguro",
    "strict-origin": "bom",
    "strict-origin-when-cross-origin": "recomendado",
    "origin": "aceitável",
    "origin-when-cross-origin": "aceitável",
    "no-referrer-when-downgrade": "fraco",
    "unsafe-url": "perigoso",
}
_OK = {"seguro", "bom", "recomendado", "aceitável"}


async def check(url: str) -> CheckResult:
    https_url = with_scheme(url, "https")
    try:
        resp = await fetch(https_url, method="GET", follow_redirects=True)
    except (httpx.HTTPError, OSError) as exc:
        return CheckResult(name=NAME, status=Status.INCONCLUSO, severity=Severity.BAIXA,
                           evidence=f"Falha ao obter resposta HTTPS: {exc!r}")

    raw = (resp.headers.get("referrer-policy") or "").strip().lower()
    if not raw:
        return CheckResult(name=NAME, status=Status.FAIL, severity=Severity.BAIXA,
                           evidence="Referrer-Policy ausente — recomendado declarar "
                                    "'strict-origin-when-cross-origin' explicitamente.")

    # Usa o último token reconhecido (semântica do navegador com múltiplos valores).
    tokens = [t.strip() for t in raw.split(",") if t.strip()]
    chosen = next((t for t in reversed(tokens) if t in REFERRER_POLICY_RANKING), tokens[-1])
    rating = REFERRER_POLICY_RANKING.get(chosen)
    details = {"header": raw, "value": chosen, "rating": rating}

    if chosen == "unsafe-url":
        return CheckResult(
            name=NAME, status=Status.FAIL, severity=Severity.MEDIA,
            evidence="Referrer-Policy 'unsafe-url' — expõe a URL completa (incluindo query "
                     "strings com dados sensíveis) a sites de terceiros.",
            details=details)
    if rating in _OK:
        return CheckResult(name=NAME, status=Status.PASS, severity=Severity.BAIXA,
                           evidence=f"Referrer-Policy '{chosen}' ({rating}).", details=details)
    # 'no-referrer-when-downgrade' (fraco) ou valor desconhecido -> PASS com nota.
    return CheckResult(
        name=NAME, status=Status.PASS, severity=Severity.BAIXA,
        evidence=f"Referrer-Policy '{chosen}' é fraco — recomendado "
                 f"'strict-origin-when-cross-origin'.",
        details=details)
