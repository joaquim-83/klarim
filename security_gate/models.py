"""KL-141 — modelos do Security Gate (dataclasses puras, sem I/O).

O Gate é um scanner de **exposição/configuração pós-deploy** (NÃO é DAST — não envia
payloads de ataque). Estes modelos são o contrato entre o engine, os checks e (Prompt 3)
os formatters. Módulo SEPARADO do `scanner/` (portável — futuro pacote pip)."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional


class Severity(Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"


class Status(Enum):
    PASS = "pass"
    FAIL = "fail"
    ERROR = "error"
    SKIP = "skip"


# Ordem de gravidade (para o `--fail-on` da CLI e para a cor do terminal). `.value` é string,
# então comparar diretamente seria lexicográfico e ERRADO — use este rank.
SEVERITY_RANK = {
    Severity.CRITICAL: 4,
    Severity.HIGH: 3,
    Severity.MEDIUM: 2,
    Severity.LOW: 1,
    Severity.INFO: 0,
}


@dataclass
class Result:
    """Resultado de UM check contra UM path/aspecto do alvo."""
    check: str              # ex.: "env_exposed"
    category: str           # ex.: "exposure" | "headers" | "ssl"
    path: str               # ex.: "/.env"
    status: Status
    severity: Severity
    detail: str             # ex.: "/.env acessível (HTTP 200)"
    http_status: Optional[int] = None


# Penalidades por severidade de um FAIL (compõem o score 0-100).
_PENALTIES = {
    Severity.CRITICAL: 20,
    Severity.HIGH: 10,
    Severity.MEDIUM: 5,
    Severity.LOW: 2,
}


@dataclass
class GateReport:
    url: str
    results: List[Result] = field(default_factory=list)
    duration_ms: int = 0
    error: Optional[str] = None

    @property
    def score(self) -> int:
        """100 menos as penalidades de cada FAIL (por severidade). Mínimo 0."""
        total = sum(_PENALTIES.get(r.severity, 0)
                    for r in self.results if r.status == Status.FAIL)
        return max(0, 100 - total)

    @property
    def passed(self) -> bool:
        """O gate passa se NÃO houver nenhum FAIL crítico (o CI usa isto p/ o exit code)."""
        return not any(r.status == Status.FAIL and r.severity == Severity.CRITICAL
                       for r in self.results)

    def _fail_count(self, sev: Severity) -> int:
        return sum(1 for r in self.results
                   if r.status == Status.FAIL and r.severity == sev)

    @property
    def critical_count(self) -> int:
        return self._fail_count(Severity.CRITICAL)

    @property
    def high_count(self) -> int:
        return self._fail_count(Severity.HIGH)

    @property
    def medium_count(self) -> int:
        return self._fail_count(Severity.MEDIUM)


@dataclass
class Config:
    """Configuração do Gate. Prompt 1 usa só `timeout`/`checks`; o carregamento de YAML
    (allowlist de paths, `fail_on`, expected headers) chega no Prompt 3 (`config.py`)."""
    timeout: int = 60
    checks: Optional[List[str]] = None            # None = todos (headers/ssl/exposure)
    fail_on_severity: Severity = Severity.CRITICAL  # nível que reprova o gate (Prompt 3)
