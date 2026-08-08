"""KL-154 — adapta o `CheckResult` do scanner para o `Result` do Gate.

O scanner (`scanner/checks/base.py`) e o Gate (`security_gate/models.py`) têm modelos
DIFERENTES de resultado. Este adaptador é a ponte de via ÚNICA (o Gate importa do scanner,
NUNCA o contrário). Traduz por VALOR de string (não por identidade de enum) para não acoplar o
Gate à implementação interna do scanner — se o scanner reorganizar seus enums mantendo os mesmos
rótulos, a tradução continua valendo. Todo acesso a campo é defensivo (`getattr`): se a interface
do scanner mudar, degrada para um default em vez de quebrar."""
from __future__ import annotations

from ..models import Result, Severity, Status

# Status do scanner (strings "PASS"/"FAIL"/"INCONCLUSO") → Status do Gate.
# INCONCLUSO é neutro no score do scanner; vira ERROR no Gate (também neutro no score do Gate).
_STATUS_MAP = {
    "PASS": Status.PASS,
    "FAIL": Status.FAIL,
    "INCONCLUSO": Status.ERROR,
}

# Severidade do scanner (PT-BR "CRITICA"/"ALTA"/"MEDIA"/"BAIXA") → Severidade do Gate.
# Aceita também os rótulos EN por robustez (o mapeamento nunca deve depender do idioma).
_SEVERITY_MAP = {
    "CRITICA": Severity.CRITICAL,
    "CRITICAL": Severity.CRITICAL,
    "ALTA": Severity.HIGH,
    "HIGH": Severity.HIGH,
    "MEDIA": Severity.MEDIUM,
    "MEDIUM": Severity.MEDIUM,
    "BAIXA": Severity.LOW,
    "LOW": Severity.LOW,
    "INFO": Severity.INFO,
}


def adapt_check_result(check_result, check_name: str, category: str = "surface") -> Result:
    """Converte um `CheckResult` do scanner num `Result` do Gate.

    - `status`: PASS→PASS · FAIL→FAIL · INCONCLUSO→ERROR (default ERROR se desconhecido).
    - `severity`: CRITICA→CRITICAL · ALTA→HIGH · MEDIA→MEDIUM · BAIXA→LOW (default MEDIUM).
    - `detail`: a `evidence` do scanner (fallback: `name` do check, depois `check_name`).
    """
    raw_status = str(getattr(check_result, "status", "") or "").upper()
    status = _STATUS_MAP.get(raw_status, Status.ERROR)

    raw_severity = str(getattr(check_result, "severity", "") or "").upper()
    severity = _SEVERITY_MAP.get(raw_severity, Severity.MEDIUM)

    detail = (getattr(check_result, "evidence", "")
              or getattr(check_result, "name", "")
              or check_name)

    return Result(
        check=check_name,
        category=category,
        path="/",
        status=status,
        severity=severity,
        detail=detail,
    )
