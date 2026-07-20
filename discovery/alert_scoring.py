"""KL-85 Parte 1 — lead scoring de qualidade de alerta. Filtra alertas proativos de baixa
qualidade ANTES do envio (economiza cota Resend + protege reputação + sobe a taxa de clique).

`calculate_alert_score` é uma função **PURA** (sem SQL): recebe o target, o e-mail de contato e
um booleano `domain_bounced` (o worker faz a consulta de bounce com cache Redis à parte).
Retorna `{"score": int, "signals": [...]}` — `score` vai ao banco/threshold; `signals` alimenta
o breakdown no detalhe admin. Lead scoring **nunca** impede scan; só a decisão de alertar.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

# Provedores de e-mail gratuitos (um e-mail nesses domínios ≠ dono verificável do site).
FREE_EMAIL_DOMAINS = {
    "gmail.com", "hotmail.com", "outlook.com", "yahoo.com", "yahoo.com.br",
    "live.com", "bol.com.br", "uol.com.br", "terra.com.br", "ig.com.br",
    "globo.com", "msn.com", "hotmail.com.br", "outlook.com.br", "icloud.com",
    "protonmail.com", "zoho.com",
}

# Prefixos genéricos (caixas de função, não uma pessoa) — taxa de resposta pior.
ROLE_BASED_PREFIXES = {
    "sac", "suporte", "atendimento", "vendas", "comercial", "financeiro",
    "rh", "marketing", "administrativo", "secretaria", "recepcao", "info",
    "noreply", "no-reply", "naoresponda", "nao-responda", "contato",
    "faleconosco", "fale-conosco", "ouvidoria", "compras", "diretoria",
    "gerencia", "ti", "webmaster", "postmaster", "abuse", "admin",
    "newsletter", "comunicacao",
}

# Setores com click rate alto — começa VAZIO (não inventar dados). Popular quando o
# KL-83 funnel-by-sector tiver >100 alertas/setor e click_rate > 15%.
HIGH_CLICK_SECTORS: set = set()


def _norm_domain(d: Optional[str]) -> str:
    d = (d or "").strip().lower()
    return d[4:] if d.startswith("www.") else d


def _email_parts(email: str) -> tuple:
    """(local, domain) minúsculos, ou ('','') se o e-mail é inválido (sem @/domínio)."""
    email = (email or "").strip().lower()
    if "@" not in email:
        return "", ""
    local, _, domain = email.partition("@")
    return local, domain


def _domain_match(email_domain: str, site_domain: str) -> bool:
    """E-mail no domínio do site (igual ou relação de subdomínio nos dois sentidos)."""
    if not email_domain or not site_domain:
        return False
    if email_domain == site_domain:
        return True
    return site_domain.endswith("." + email_domain) or email_domain.endswith("." + site_domain)


def calculate_alert_score(target: Dict[str, Any], contact_email: Optional[str],
                          domain_bounced: bool = False) -> Dict[str, Any]:
    """Score (pode ser negativo) + breakdown de sinais. Função pura, testável sem DB."""
    signals = []
    score = 0

    def add(pts: int, name: str) -> None:
        nonlocal score
        score += pts
        signals.append({"signal": name, "points": pts})

    local, edomain = _email_parts(contact_email or "")
    valid_email = bool(edomain)
    sdomain = _norm_domain(target.get("domain"))
    matches = valid_email and _domain_match(edomain, sdomain)

    # --- positivos ---
    if matches:
        add(30, "email_matches_domain")
    if valid_email and edomain not in FREE_EMAIL_DOMAINS:
        add(10, "corporate_email")

    sc = target.get("last_scan_score")
    if sc is None:
        sc = target.get("scan_score")
    if sc is not None:
        if sc > 85:
            add(5, "score_low_urgency")           # pouca urgência, conversão baixa
        elif sc >= 50:
            add(20, "score_action_zone")          # urgência + viabilidade (50–85)
        elif sc >= 40:
            add(10, "score_high_urgency")         # urgente, mas pode estar abandonado

    if (target.get("sector") or "").strip().lower() in HIGH_CLICK_SECTORS:
        add(15, "high_click_sector")

    # --- negativos ---
    if valid_email and not matches and edomain in FREE_EMAIL_DOMAINS:
        add(-20, "email_mismatch_free")           # e-mail genérico de terceiro
    if valid_email and local in ROLE_BASED_PREFIXES:
        add(-15, "role_based_prefix")
    if target.get("status") == "descartado" or (sc is not None and sc < 40):
        add(-10, "abandoned_or_low_score")
    # Bounce por DOMÍNIO só penaliza domínio próprio/corporativo. Num provedor genérico
    # (gmail/outlook/…), um bounce em joao@gmail.com NÃO diz nada sobre maria@gmail.com — são
    # endereços independentes. Penalizar o domínio inteiro filtrava ~38% do pool (fix 2026-07-20).
    if domain_bounced and edomain not in FREE_EMAIL_DOMAINS:
        add(-40, "bounce_domain")                 # histórico ruim → protege reputação

    return {"score": score, "signals": signals}
