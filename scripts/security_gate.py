#!/usr/bin/env python3
"""KL-141 — Klarim Security Gate (CLI). Scanner de exposição/configuração pós-deploy.

Uso:
    python scripts/security_gate.py https://klarim.net
    python scripts/security_gate.py https://klarim.net --fail-on high --json
    python scripts/security_gate.py https://klarim.net --checks headers,ssl --config security-gate.yml

Exit codes: 0 = passou · 1 = falhou (finding ≥ threshold `--fail-on`) · 2 = erro (site offline,
config inválida, exceção). Roda apenas contra alvos AUTORIZADOS (Fase 1: o próprio CI da Klarim)."""
from __future__ import annotations

import argparse
import asyncio
import sys

# Permite rodar como `python scripts/security_gate.py` de qualquer CWD (adiciona a raiz do repo).
_HERE = __import__("os").path.dirname(__import__("os").path.abspath(__file__))
_ROOT = __import__("os").path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from security_gate.config import load_config                     # noqa: E402
from security_gate.engine import run_all                         # noqa: E402
from security_gate.formatters import format_json, format_terminal  # noqa: E402
from security_gate.models import SEVERITY_RANK, Severity, Status  # noqa: E402


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Klarim Security Gate — scanner de exposição pós-deploy")
    p.add_argument("url", help="URL do site a verificar")
    p.add_argument("--fail-on", default="critical",
                   choices=["critical", "high", "medium", "low"],
                   help="Menor severidade que reprova (exit 1). Default: critical")
    p.add_argument("--timeout", type=int, default=60, help="Timeout global (s). Default: 60")
    p.add_argument("--checks", default=None,
                   help="Checks a rodar (CSV). Disponíveis: headers,ssl,exposure,credentials,api,"
                        "cors,cookies,https_redirect,open_redirect,error_disclosure,jwt,"
                        "form_security,dns,email_security,dependencies,tls_ciphers,subdomain,"
                        "infrastructure,rate_limit")
    p.add_argument("--config", default="security-gate.yml", help="YAML de configuração")
    p.add_argument("--json", action="store_true", help="Saída em JSON (integração)")
    p.add_argument("--quiet", action="store_true", help="Mostra só os FAIL/ERROR")
    return p


def _has_failure(report, fail_on: str) -> bool:
    """FAIL cuja severidade é >= o threshold `--fail-on` (rank, não comparação de string)."""
    threshold = SEVERITY_RANK[Severity(fail_on)]
    return any(r.status == Status.FAIL and SEVERITY_RANK.get(r.severity, 0) >= threshold
               for r in report.results)


def main(argv=None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        config = load_config(args.config, args)
    except Exception as exc:  # noqa: BLE001 - config inválida → exit 2
        print(f"Erro ao carregar a configuração: {exc!r}", file=sys.stderr)
        return 2

    report = asyncio.run(run_all(url=args.url, timeout=config.timeout,
                                 checks=config.checks, config=config))

    print(format_json(report) if args.json else format_terminal(report, quiet=args.quiet))

    if report.error:                       # falha de infra (site offline/DNS/timeout total)
        return 2
    return 1 if _has_failure(report, args.fail_on) else 0


if __name__ == "__main__":
    sys.exit(main())
