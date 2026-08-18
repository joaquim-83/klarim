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

# Penalidade p/ e-mail genérico que NÃO casa o domínio do site. 2026-07-20: reduzida de -20 → 0.
# A premissa "gmail = terceiro (não é o dono)" é FALSA para muitas PMEs brasileiras, que usam
# gmail como e-mail comercial — o -20 barrava leads legítimos (0/258 targets free passavam do
# threshold). 0 = desligado; mantido nomeado para retomar (ex.: -10) se a reputação do klarim.net
# (dom. recém-migrado) pedir. Só afeta o SINAL negativo; o bônus corporativo (+10) segue exclusivo
# de domínio próprio.
MISMATCH_FREE_PENALTY = 0

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

# KL-146 — fator de TIPO de e-mail (pessoal vs genérico), para ORDENAR a fila de alerta. Dados de
# produção: `contato@` gera 66% dos bounces (8,7% de taxa) vs. e-mails pessoais (3,6%). A solução
# NÃO é filtrar (a regra de envio do KL-145 = sintaxe+MX+blocklist é INALTERADA) — é REORDENAR:
# pessoais primeiro, genéricos depois. A blocklist aprende com os bounces dos genéricos antes de
# enviar muitos. Este fator SUBSTITUI a penalidade role-based do KL-136 (`ALERT_ROLE_PENALTY`, -5) —
# não acumula com ela: o próprio fator já dá ≤0 aos genéricos e +15 aos pessoais.
GENERIC_HIGH_BOUNCE = {"contato"}                     # 8,7% bounce → -10
GENERIC_MEDIUM_BOUNCE = {"atendimento", "sac"}        # 6-7% bounce → -5
# Genéricos sem sinal de bounce elevado → 0 (nem bônus de pessoal, nem penalidade). Lista do card
# KL-146; a UNIÃO com ROLE_BASED_PREFIXES garante que um genérico NÃO listado (ex.: `noreply`,
# `financeiro`, `rh`) também caia em 0 — nunca seja tratado como pessoal (+15).
GENERIC_NEUTRAL = {
    "comercial", "vendas", "suporte", "info", "faleconosco", "falecom",
    "email", "adm", "admin", "cadastro", "contact", "hello", "oi",
}


def _email_type_factor(email: Optional[str]) -> int:
    """Fator de priorização por tipo de e-mail (só ORDENA a fila, KL-146 — NUNCA bloqueia envio):
    pessoal (+15) > genérico neutro (0) > genérico medium-bounce (-5) > genérico high-bounce (-10).

    Um prefixo genérico não listado nos 3 conjuntos cai na UNIÃO com `ROLE_BASED_PREFIXES` → 0
    (neutro), NUNCA +15 — senão `noreply@`/`naoresponda@` seriam priorizados como se fossem
    pessoas. Substitui a penalidade role-based do KL-136 (não acumula)."""
    if not email or "@" not in email:
        return 0
    prefix = email.split("@")[0].lower().strip()
    if not prefix:
        return 0                                       # "@dominio.com" → sem prefixo
    if prefix in GENERIC_HIGH_BOUNCE:
        return -10
    if prefix in GENERIC_MEDIUM_BOUNCE:
        return -5
    if prefix in GENERIC_NEUTRAL or prefix in ROLE_BASED_PREFIXES:
        return 0
    return 15                                          # não é genérico conhecido → provável pessoal


# KL-168 — REGRESSÃO do KL-167: a lista expandida (contato/atendimento/sac/info/comercial/vendas)
# cobria ~39% dos e-mails elegíveis da base BR (contato@/sac@/info@ SÃO o e-mail principal de muitos
# negócios daqui — não descartáveis como em mercados com e-mail pessoal do dono). Com o filtro LIGADO
# por default, o worker esvaziava os poucos pessoais nos primeiros ciclos e parava (97% blocked_generic).
# Fix: o filtro passa a ser OPT-IN (`ALERT_SKIP_GENERIC` default FALSE no alert_worker) e, quando
# ligado, filtra só os DOIS piores por bounce (contato@ 8,7% e sac@). O `_email_type_factor` acima
# segue REORDENANDO os demais genéricos (KL-146) sem bloquear ninguém.
GENERIC_ALERT_SKIP_PREFIXES = (
    "contato", "sac",
)
# Fronteira de palavra p/ o "começa com" do card sem overmatch: `sac2@`/`sac.loja@` casam;
# `sacha@`/`informatica@`/`vendasparceladas`… NÃO (o prefixo tem de terminar em separador/dígito).
_GENERIC_BOUNDARY = "0123456789.-_+"


def is_generic_alert_email(email: Optional[str]) -> bool:
    """KL-167 — True se o e-mail é uma caixa genérica que não deve receber alerta cold.

    Casa quando o local-part É um dos prefixos genéricos, ou começa com ele seguido de uma
    fronteira de palavra (dígito/`.`/`-`/`_`/`+`). Evita o overmatch de um `startswith` puro
    (não pega `sacha@`, `informatica@`). Pura/testável."""
    local, _ = _email_parts(email or "")
    if not local:
        return False
    for p in GENERIC_ALERT_SKIP_PREFIXES:
        if local == p or (local.startswith(p) and local[len(p)] in _GENERIC_BOUNDARY):
            return True
    return False


# Nome do sinal no breakdown, por valor do fator (transparência no detalhe admin).
_EMAIL_TYPE_SIGNAL = {
    15: "email_type_personal",
    0: "email_type_generic",
    -5: "email_type_generic_medium_bounce",
    -10: "email_type_generic_high_bounce",
}


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
    if MISMATCH_FREE_PENALTY and valid_email and not matches and edomain in FREE_EMAIL_DOMAINS:
        add(MISMATCH_FREE_PENALTY, "email_mismatch_free")   # e-mail genérico de terceiro (hoje 0)
    # KL-146 — fator de TIPO de e-mail (pessoal vs genérico). SUBSTITUI a penalidade role-based do
    # KL-136 (não acumula): o próprio fator já dá +15 aos pessoais, 0 aos genéricos neutros, -5/-10
    # aos genéricos com bounce elevado. Só ORDENA a fila (KL-145: envio = sintaxe+MX+blocklist, não
    # muda). O 'role' da Reoon (caixa de função confirmada) rebaixa um prefixo que PARECIA pessoal —
    # não premiar como pessoa (evita dobrar/contradizer o sinal do prefixo).
    if valid_email:
        type_pts = _email_type_factor(contact_email)
        verify_status = (target.get("email_verify_status") or "").strip().lower()
        if type_pts == 15 and verify_status == "role":
            add(0, "email_type_role_verified")          # Reoon: caixa de função disfarçada de pessoal
        else:
            add(type_pts, _EMAIL_TYPE_SIGNAL[type_pts])
    if target.get("status") == "descartado" or (sc is not None and sc < 40):
        add(-10, "abandoned_or_low_score")
    # Bounce por DOMÍNIO só penaliza domínio próprio/corporativo. Num provedor genérico
    # (gmail/outlook/…), um bounce em joao@gmail.com NÃO diz nada sobre maria@gmail.com — são
    # endereços independentes. Penalizar o domínio inteiro filtrava ~38% do pool (fix 2026-07-20).
    if domain_bounced and edomain not in FREE_EMAIL_DOMAINS:
        add(-40, "bounce_domain")                 # histórico ruim → protege reputação

    return {"score": score, "signals": signals}
