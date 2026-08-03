#!/usr/bin/env python3
"""KL-141 (Prompt 4) — notifica o resultado do Security Gate (e-mail via Resend e/ou webhook).

Chamado pelo job `security-gate` do CI **só em falha** (`if: failure()`). Fail-safe: sem
`RESEND_API_KEY`/URL, apenas avisa (não quebra o job). NÃO envia o VALOR de credenciais — o report
já traz só tipo+localização+severidade (o check de credenciais nunca gravou o valor)."""
from __future__ import annotations

import argparse
import json
import os
import sys
from typing import List


def _fails(report: dict) -> List[dict]:
    return [r for r in report.get("results", []) if r.get("status") == "fail"]


def email_subject(report: dict) -> str:
    return (f"🔴 Security Gate FAILED — {report.get('url', '?')} — "
            f"Score {report.get('score', '?')}/100")


def email_body(report: dict) -> str:
    lines = [
        f"🔴 Security Gate FAILED — {report.get('url', '?')}",
        "",
        f"Score: {report.get('score', '?')}/100",
        f"Critical: {report.get('critical', 0)} | High: {report.get('high', 0)} "
        f"| Medium: {report.get('medium', 0)}",
        "",
        "Findings:",
    ]
    for f in _fails(report):
        lines.append(f"  ❌ [{str(f.get('severity', '')).upper()}] {f.get('check')}: {f.get('detail')}")
    if report.get("error"):
        lines.append(f"  ⚠️  Erro de execução: {report['error']}")
    lines += [
        "",
        f"Duração: {report.get('duration_ms', '?')}ms",
        "",
        "Alerta automático do Klarim Security Gate após o deploy em produção.",
        "Verifique os findings acima e tome ação (o deploy NÃO foi revertido).",
        "",
        "Klarim — segurança web para empresas brasileiras",
    ]
    return "\n".join(lines)


def webhook_payload(report: dict) -> dict:
    fails = _fails(report)
    text = (f"🔴 Security Gate FAILED — {report.get('url', '?')}\n"
            f"Score: {report.get('score', '?')}/100 | "
            f"Critical: {report.get('critical', 0)} | High: {report.get('high', 0)}\n"
            + "\n".join(f"  ❌ [{f.get('severity')}] {f.get('detail')}" for f in fails[:10]))
    return {"text": text}


def send_email(report: dict, recipients_str: str) -> bool:
    """Envia o alerta via Resend. Sem key/destinatário → avisa e retorna False (não quebra)."""
    api_key = os.environ.get("RESEND_API_KEY")
    if not api_key:
        print("RESEND_API_KEY não configurada — e-mail não enviado", flush=True)
        return False
    recipients = [r.strip() for r in (recipients_str or "").split(",") if r.strip()]
    if not recipients:
        print("Sem destinatários — e-mail não enviado", flush=True)
        return False
    import httpx
    try:
        resp = httpx.post(
            "https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {api_key}"},
            json={"from": "Klarim Security Gate <seguranca@klarim.net>",
                  "to": recipients, "subject": email_subject(report),
                  "text": email_body(report)},
            timeout=10)
    except Exception as exc:  # noqa: BLE001 - notificação nunca derruba o job
        print(f"Erro ao enviar e-mail: {exc!r}", flush=True)
        return False
    if resp.status_code == 200:
        print(f"E-mail enviado para {recipients}", flush=True)
        return True
    print(f"Erro ao enviar e-mail: {resp.status_code} {resp.text}", flush=True)
    return False


def send_webhook(report: dict, webhook_url: str) -> bool:
    """POST do resultado a um webhook (Slack/Discord/…). Sem URL → avisa e retorna False."""
    url = webhook_url or os.environ.get("GATE_WEBHOOK_URL")
    if not url:
        print("Webhook URL não configurada — webhook não enviado", flush=True)
        return False
    import httpx
    try:
        resp = httpx.post(url, json=webhook_payload(report), timeout=10)
    except Exception as exc:  # noqa: BLE001
        print(f"Erro no webhook: {exc!r}", flush=True)
        return False
    print(f"Webhook: {resp.status_code}", flush=True)
    return resp.status_code < 400


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Notifica o resultado do Klarim Security Gate")
    p.add_argument("--report", required=True, help="Path do JSON report do Gate")
    p.add_argument("--channel", default="email", choices=["email", "webhook", "both"])
    p.add_argument("--recipients", help="E-mails (separados por vírgula)")
    p.add_argument("--webhook-url", help="URL do webhook")
    args = p.parse_args(argv)

    try:
        with open(args.report, encoding="utf-8") as f:
            report = json.load(f)
    except Exception as exc:  # noqa: BLE001 - report ausente/ilegível → avisa, não quebra
        print(f"Não foi possível ler o report {args.report}: {exc!r}", flush=True)
        return 0

    if args.channel in ("email", "both"):
        send_email(report, args.recipients)
    if args.channel in ("webhook", "both"):
        send_webhook(report, args.webhook_url)
    return 0


if __name__ == "__main__":
    sys.exit(main())
