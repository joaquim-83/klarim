"""KL-141 — Security Gate: scanner de EXPOSIÇÃO/configuração pós-deploy (não é DAST — não
envia payloads de ataque). Roda em ~30s no CI/CD e verifica o que ficou exposto após o deploy:
arquivos de config (`.env`/`.git`), painéis/docs/debug sem auth, headers de segurança e SSL.

Fase 1 (KL-141): dogfooding no próprio CI/CD da Klarim. Módulo SEPARADO do `scanner/` (portável).
Prompt 1/4: engine + models + checks de exposição/headers/SSL."""
from __future__ import annotations

from .engine import run_all
from .models import Config, GateReport, Result, Severity, Status

__all__ = ["run_all", "GateReport", "Result", "Severity", "Status", "Config"]
__version__ = "1.0"
