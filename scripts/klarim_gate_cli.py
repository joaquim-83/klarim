#!/usr/bin/env python3
"""Klarim Security Gate — CLI para devs (KL-151 Prompt 2/4).

O scan roda no SERVIDOR da Klarim: este CLI só envia a URL + a API key; a API roda a engine e
devolve o resultado. Instalável via pip no futuro; por ora funciona standalone:

    python scripts/klarim_gate_cli.py scan https://meuapp.com.br --api-key KLM_xxxx
    KLARIM_API_KEY=KLM_xxxx python scripts/klarim_gate_cli.py scan https://meuapp.com.br --fail-on high

Exit codes: 0 = passou · 1 = reprovou (finding ≥ --fail-on) · 2 = erro (API fora, key inválida,
domínio não verificado, limite atingido). Só depende de `httpx`.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

API_BASE = os.environ.get("KLARIM_GATE_API", "https://klarim.net/api/gate")


# --------------------------------------------------------------------------- #
# Client HTTP
# --------------------------------------------------------------------------- #

class GateClient:
    def __init__(self, api_key: str, base_url: str = API_BASE):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.headers = {"X-API-Key": api_key}

    def _import_httpx(self):
        try:
            import httpx
            return httpx
        except ImportError:
            print("Erro: o pacote 'httpx' é necessário (pip install httpx).", file=sys.stderr)
            sys.exit(2)

    def scan(self, url, fail_on="critical", timeout=60, metadata=None):
        httpx = self._import_httpx()
        with httpx.Client(timeout=timeout + 30) as c:   # +30s de margem sobre o timeout do scan
            r = c.post(f"{self.base_url}/scan", headers=self.headers, json={
                "url": url, "fail_on": fail_on, "timeout": timeout, "metadata": metadata or {}})
            r.raise_for_status()
            return r.json()

    def list_projects(self):
        httpx = self._import_httpx()
        with httpx.Client(timeout=15) as c:
            r = c.get(f"{self.base_url}/projects", headers=self.headers)
            r.raise_for_status()
            return r.json().get("projects", [])

    def list_runs(self, project_id=None, limit=20):
        httpx = self._import_httpx()
        params = {"limit": limit}
        if project_id:
            params["project_id"] = project_id
        with httpx.Client(timeout=15) as c:
            r = c.get(f"{self.base_url}/runs", headers=self.headers, params=params)
            r.raise_for_status()
            return r.json().get("runs", [])


# --------------------------------------------------------------------------- #
# Formatação (PT-BR)
# --------------------------------------------------------------------------- #

_ICON = {"pass": "✅", "fail": "❌", "error": "⚠️", "skip": "⏭️"}


def _print_result(result: dict, json_mode: bool = False, quiet: bool = False) -> None:
    if json_mode:
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return
    print(f"\n🔍 Klarim Security Gate — {result.get('url', '')}")
    print("━" * 50)
    for r in result.get("results", []):
        if quiet and r.get("status") == "pass":
            continue
        icon = _ICON.get(r.get("status"), "•")
        print(f"  {icon} [{r.get('severity')}] {r.get('check')}: {r.get('detail')}")

    blocked = result.get("checks_blocked") or []
    if blocked:
        print(f"\n🔒 {len(blocked)} checks bloqueados no plano {result.get('plan', 'Free')}:")
        for c in blocked[:5]:
            print(f"   • {c}")
        if len(blocked) > 5:
            print(f"   … e mais {len(blocked) - 5}")
        print("   Faça upgrade → https://klarim.net/security-gate")

    print("\n" + "━" * 50)
    score = result.get("score", 0)
    s = "🟢" if score >= 90 else "🟡" if score >= 50 else "🔴"
    print(f"Score: {score}/100 {s}")
    print(f"Critical: {result.get('critical', 0)} | High: {result.get('high', 0)} "
          f"| Medium: {result.get('medium', 0)}")
    print(f"Duração: {result.get('duration_ms', 0)}ms | Plano: {result.get('plan', 'Free')}")
    print("✅ PASSED — pode subir" if result.get("passed") else "❌ FAILED — problemas encontrados")
    if result.get("dashboard_url"):
        print(f"Dashboard: {result['dashboard_url']}")


def _print_projects(projects: list) -> None:
    if not projects:
        print("Nenhum projeto. Crie um em https://klarim.net/security-gate.")
        return
    print(f"\n{'DOMÍNIO':<32} {'VERIFICADO':<12} MÉTODO")
    print("━" * 60)
    for p in projects:
        v = "✅ sim" if p.get("verified") else "❌ não"
        print(f"{(p.get('domain') or ''):<32} {v:<12} {p.get('verification_method') or '-'}")


def _print_runs(runs: list) -> None:
    if not runs:
        print("Nenhum run ainda.")
        return
    print(f"\n{'ID':<6} {'SCORE':<7} {'PASSOU':<8} {'DATA':<22} URL")
    print("━" * 70)
    for r in runs:
        passed = "✅" if r.get("passed") else "❌"
        print(f"{r.get('id'):<6} {str(r.get('score')):<7} {passed:<8} "
              f"{str(r.get('created_at'))[:19]:<22} {r.get('url', '')}")


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="klarim-gate",
                                description="Klarim Security Gate — scan de segurança pós-deploy")
    sub = p.add_subparsers(dest="command")

    sc = sub.add_parser("scan", help="Rodar um scan de segurança")
    sc.add_argument("url", help="URL do site (domínio verificado no seu projeto)")
    sc.add_argument("--api-key", default=os.environ.get("KLARIM_API_KEY"),
                    help="API key (ou a env KLARIM_API_KEY)")
    sc.add_argument("--fail-on", default="critical", choices=["critical", "high", "medium", "low"],
                    help="Menor severidade que reprova (exit 1). Default: critical")
    sc.add_argument("--timeout", type=int, default=60, help="Timeout do scan (s). Default: 60")
    sc.add_argument("--metadata", type=json.loads, default={},
                    help='JSON com metadados do CI (ex.: \'{"commit":"abc","ci":"gh"}\')')
    sc.add_argument("--json", action="store_true", help="Saída em JSON")
    sc.add_argument("--quiet", action="store_true", help="Mostra só FAIL/ERROR")

    pr = sub.add_parser("projects", help="Listar seus projetos")
    pr.add_argument("--api-key", default=os.environ.get("KLARIM_API_KEY"))

    rn = sub.add_parser("runs", help="Listar seus runs")
    rn.add_argument("--api-key", default=os.environ.get("KLARIM_API_KEY"))
    rn.add_argument("--project-id", type=int, default=None)
    rn.add_argument("--limit", type=int, default=20)
    return p


def main(argv=None) -> int:
    args = _build_parser().parse_args(argv)
    if not args.command:
        _build_parser().print_help()
        return 2
    if not args.api_key:
        print("Erro: API key necessária. Use --api-key ou a env KLARIM_API_KEY.", file=sys.stderr)
        return 2

    base = os.environ.get("KLARIM_GATE_API", API_BASE)
    client = GateClient(api_key=args.api_key, base_url=base)

    try:
        if args.command == "scan":
            result = client.scan(args.url, fail_on=args.fail_on, timeout=args.timeout,
                                 metadata=args.metadata)
            _print_result(result, json_mode=args.json, quiet=args.quiet)
            return 0 if result.get("passed") else 1
        if args.command == "projects":
            _print_projects(client.list_projects())
            return 0
        if args.command == "runs":
            _print_runs(client.list_runs(project_id=args.project_id, limit=args.limit))
            return 0
    except Exception as exc:  # noqa: BLE001 - erro de rede/HTTP/API → exit 2 com mensagem
        _print_error(exc)
        return 2
    return 2


def _print_error(exc) -> None:
    """Mensagem de erro amigável (destaca 401/403/429 da API)."""
    try:
        import httpx
        if isinstance(exc, httpx.HTTPStatusError):
            code = exc.response.status_code
            try:
                detail = exc.response.json().get("detail", "")
            except Exception:  # noqa: BLE001
                detail = exc.response.text[:200]
            hint = {401: "API key inválida ou revogada.",
                    403: "Domínio não verificado ou não registrado.",
                    429: "Limite de scans do plano atingido."}.get(code, "")
            print(f"Erro {code}: {detail or hint} {hint if detail else ''}".strip(), file=sys.stderr)
            return
        if isinstance(exc, (httpx.ConnectError, httpx.TimeoutException)):
            print(f"Erro: não foi possível falar com a API do Gate ({type(exc).__name__}).",
                  file=sys.stderr)
            return
    except ImportError:
        pass
    print(f"Erro: {exc!r}", file=sys.stderr)


if __name__ == "__main__":
    sys.exit(main())
