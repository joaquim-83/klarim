"""Lead scoring do Klarim (KL-61) — PQL (Product Qualified Lead).

Módulo **puro** (sem I/O, sem imports de api.main/store) — a pontuação é derivada de
sinais comportamentais no produto. A classificação (cold/warm/hot/pql) é **sempre**
calculada a partir do score — nunca setada à mão. Score mínimo é 0 (nunca negativo).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

# Sinais positivos (comportamento no produto).
SCORING_RULES = {
    "email_verified": 10,    # verificou e-mail para scan (baseline)
    "scan_completed": 15,    # scan completou com resultado
    "score_below_70": 10,    # site com score < 70 (dor)
    "score_below_50": 20,    # site com score < 50 (dor crítica; cumulativo com < 70)
    "account_created": 25,   # criou conta na plataforma
    "monitoring_added": 30,  # adicionou site ao monitoramento
    "multiple_scans": 20,    # escaneou 2+ URLs diferentes
    "rescan": 15,            # re-escaneou o mesmo site (voltou)
    "corporate_email": 5,    # e-mail corporativo (não @gmail etc.)
}

# Decaimento temporal.
DECAY_RULES = {
    "inactive_14d": -15,     # sem atividade em 14+ dias
}

# Rótulos legíveis (para a composição do score no detalhe do lead).
RULE_LABELS = {
    "email_verified": "E-mail verificado",
    "scan_completed": "Scan completado",
    "score_below_70": "Score do site < 70",
    "score_below_50": "Score do site < 50 (crítico)",
    "account_created": "Conta criada",
    "monitoring_added": "Monitoramento ativo",
    "multiple_scans": "Múltiplos scans (2+ URLs)",
    "rescan": "Re-scan do mesmo site",
    "corporate_email": "E-mail corporativo",
    "inactive_14d": "Sem atividade há 14+ dias",
}

# Classificação derivada do score (faixas inclusivas).
CLASSIFICATION_THRESHOLDS = {
    "cold": (0, 20),
    "warm": (21, 40),
    "hot": (41, 60),
    "pql": (61, 999),
}

# E-mails genéricos (is_corporate_email = False).
GENERIC_DOMAINS = {
    "gmail.com", "hotmail.com", "outlook.com", "yahoo.com",
    "live.com", "icloud.com", "protonmail.com", "uol.com.br",
    "bol.com.br", "terra.com.br", "ig.com.br", "globo.com",
    "msn.com", "aol.com", "mail.com", "zoho.com",
    "yahoo.com.br", "hotmail.com.br", "outlook.com.br",
}


def is_corporate_email(email: Optional[str]) -> bool:
    """True se o domínio do e-mail NÃO é genérico (gmail/hotmail/…)."""
    try:
        domain = (email or "").split("@", 1)[1].lower().strip()
    except (IndexError, AttributeError):
        return False
    return bool(domain) and domain not in GENERIC_DOMAINS


def _days_since(dt: Any, now: datetime) -> Optional[float]:
    if not dt:
        return None
    if isinstance(dt, str):
        try:
            dt = datetime.fromisoformat(dt.replace("Z", "+00:00"))
        except ValueError:
            return None
    if not isinstance(dt, datetime):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return (now - dt).total_seconds() / 86400.0


def score_breakdown(lead_data: Dict[str, Any],
                    now: Optional[datetime] = None) -> List[Dict[str, Any]]:
    """Lista de `{key, label, points, applied}` — a composição do score (detalhe do lead)."""
    now = now or datetime.now(timezone.utc)
    total_scans = int(lead_data.get("total_scans") or 0)
    distinct_urls = int(lead_data.get("distinct_urls")
                        or len(lead_data.get("urls_scanned") or []) or 0)
    worst = lead_data.get("worst_score")
    has_account = bool(lead_data.get("has_account"))
    has_monitoring = bool(lead_data.get("has_monitoring"))
    is_corp = bool(lead_data.get("is_corporate_email"))
    inactive_days = _days_since(lead_data.get("last_activity_at"), now)

    applied = {
        "email_verified": True,  # baseline: todo lead verificou um e-mail
        "scan_completed": total_scans >= 1,
        "score_below_70": worst is not None and worst < 70,
        "score_below_50": worst is not None and worst < 50,
        "account_created": has_account,
        "monitoring_added": has_monitoring,
        "multiple_scans": distinct_urls >= 2,
        "rescan": total_scans > max(distinct_urls, 1),
        "corporate_email": is_corp,
        "inactive_14d": inactive_days is not None and inactive_days >= 14,
    }
    points = {**SCORING_RULES, **DECAY_RULES}
    return [{"key": k, "label": RULE_LABELS[k], "points": points[k], "applied": applied[k]}
            for k in points]


def classify(score: int) -> str:
    """Classificação (cold/warm/hot/pql) a partir do score — sempre derivada."""
    for name, (lo, hi) in CLASSIFICATION_THRESHOLDS.items():
        if lo <= score <= hi:
            return name
    return "pql"


def calculate_lead_score(lead_data: Dict[str, Any],
                         now: Optional[datetime] = None) -> Tuple[int, str]:
    """(score, classification). Soma os sinais aplicados; score >= 0."""
    total = sum(item["points"] for item in score_breakdown(lead_data, now) if item["applied"])
    total = max(0, total)
    return total, classify(total)
