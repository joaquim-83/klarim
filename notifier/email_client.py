"""Client de e-mail (Resend) para alertas e entrega de relatórios.

O SDK `resend` é síncrono; encapsulamos em ``asyncio.to_thread`` para não travar
o event loop da API. Templates HTML renderizados com Jinja2 (table-based para
compatibilidade com Gmail/Outlook).
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import quote, urlparse

from jinja2 import Environment, FileSystemLoader, select_autoescape

_HERE = Path(__file__).resolve().parent
_env = Environment(
    loader=FileSystemLoader(str(_HERE / "templates")),
    autoescape=select_autoescape(["html", "xml"]),
)

# Remetente padrão que funciona SEM domínio verificado (bom para testes).
DEFAULT_FROM = "Klarim <onboarding@resend.dev>"
SITE_BASE = "https://klarim.net"

SEMAPHORE_COLOR = {"verde": "#00D26A", "amarelo": "#F2C744", "vermelho": "#FF4D4D"}
SEMAPHORE_LABEL = {"verde": "VERDE", "amarelo": "AMARELO", "vermelho": "VERMELHO"}
SEMAPHORE_EMOJI = {"verde": "🟢", "amarelo": "🟡", "vermelho": "🔴"}

LGPD_SHORT = (
    "Se o seu site coleta dados pessoais (nome, CPF, e-mail, cartão), falhas de "
    "segurança podem resultar em sanções e multas pela LGPD (até R$ 50 milhões por infração)."
)


class KlarimMailerError(RuntimeError):
    """Erro ao enviar e-mail via Resend (chave inválida, domínio não verificado…)."""


def semaphore_from_score(score: int) -> str:
    # Alinhado à calibração KL-12 (verde exige >= 90). Aqui só temos o score;
    # o semáforo autoritativo (que também bloqueia por FALHA alta) vem do scan.
    if score >= 90:
        return "verde"
    if score >= 50:
        return "amarelo"
    return "vermelho"


def site_name(url: str) -> str:
    host = (urlparse(url).hostname or url).lower()
    return host[4:] if host.startswith("www.") else host


def utm_result_link(target_url: str, campaign: str, target_id=None) -> str:
    """Link /result com UTM (KL-21) — permite rastrear cliques do e-mail por campanha
    e por alvo. Sem target_id, usa o domínio como utm_content."""
    content = f"target_{target_id}" if target_id else site_name(target_url)
    return (f"{SITE_BASE}/result?url={quote(target_url, safe='')}"
            f"&utm_source=klarim&utm_medium=email&utm_campaign={quote(campaign, safe='')}"
            f"&utm_content={quote(content, safe='')}")


def unsubscribe_token(email: str, secret: str) -> str:
    """HMAC-SHA256 do e-mail (32 chars) — impede descadastro por terceiros."""
    return hmac.new(secret.encode(), email.strip().lower().encode(), hashlib.sha256).hexdigest()[:32]


def build_unsubscribe_link(email: str, secret: str) -> str:
    return f"{SITE_BASE}/api/unsubscribe?email={quote(email)}&token={unsubscribe_token(email, secret)}"


# Endpoint da Batch API do Resend (envia até 100 e-mails em 1 request — KL-23).
RESEND_BATCH_URL = "https://api.resend.com/emails/batch"
BATCH_MAX = 100  # limite da Resend Batch API por request


def batch_idempotency_key(items: List[Dict[str, Any]]) -> str:
    """Chave idempotente determinística por batch (KL-23).

    Baseada nos e-mails do batch + a data (UTC): reenviar o MESMO batch no mesmo
    dia (retry após timeout/erro de rede) reusa a chave e o Resend não duplica.
    Válida por 24h no Resend. Cada item deve ter a chave ``to_email``.
    """
    emails = sorted(a.get("to_email", "") for a in items)
    date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    raw = json.dumps(emails, ensure_ascii=False) + date
    return "batch_" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


class KlarimMailer:
    def __init__(self, api_key: str, from_address: Optional[str] = None) -> None:
        if not api_key:
            raise ValueError("RESEND_API_KEY não configurada")
        self.api_key = api_key
        self.from_address = from_address or os.environ.get("RESEND_FROM") or DEFAULT_FROM

    # ----- envio (thread) -------------------------------------------------- #

    async def _send(self, params: Dict[str, Any]) -> Dict[str, Any]:
        return await asyncio.to_thread(self._send_sync, params)

    def _send_sync(self, params: Dict[str, Any]) -> Dict[str, Any]:
        import resend  # import tardio: só necessário no envio real

        resend.api_key = self.api_key
        try:
            resp = resend.Emails.send(params)
        except Exception as exc:  # noqa: BLE001 - normaliza erros do SDK
            raise KlarimMailerError(f"Falha no envio Resend: {exc}") from exc
        email_id = resp.get("id") if isinstance(resp, dict) else getattr(resp, "id", None)
        return {"email_id": email_id, "raw": resp if isinstance(resp, dict) else str(resp)}

    # ----- batch (KL-23) --------------------------------------------------- #

    async def _send_batch(
        self, payloads: List[Dict[str, Any]], items: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """Envia os payloads via Batch API (com idempotency key) e conta o resultado.

        ``items`` são os dicts originais (com ``to_email``) usados para derivar a
        chave idempotente. Os IDs voltam na mesma ordem do input.
        """
        key = batch_idempotency_key(items)
        body = await self._send_batch_raw(payloads, key)
        data = body.get("data") if isinstance(body, dict) else None
        ids = [d.get("id") for d in (data or []) if isinstance(d, dict)]
        sent = len([i for i in ids if i])
        return {"sent": sent, "failed": len(payloads) - sent, "ids": ids}

    async def _send_batch_raw(
        self, payloads: List[Dict[str, Any]], idempotency_key: str
    ) -> Dict[str, Any]:
        """POST /emails/batch com header ``Idempotency-Key``.

        O SDK Python do Resend não expõe o header de idempotência no
        ``Batch.send()``, então falamos com a API via httpx diretamente.
        """
        import httpx  # import tardio: só no envio real

        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    RESEND_BATCH_URL,
                    json=payloads,
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                        "Idempotency-Key": idempotency_key,
                    },
                    timeout=30,
                )
        except Exception as exc:  # noqa: BLE001 - erro de rede vira erro do mailer
            raise KlarimMailerError(f"Falha no batch Resend: {exc}") from exc

        try:
            body = resp.json()
        except Exception:  # noqa: BLE001
            body = {}
        if resp.status_code >= 400:
            detail = body.get("message") or body.get("error") or resp.text
            raise KlarimMailerError(f"Falha no batch Resend ({resp.status_code}): {detail}")
        return body if isinstance(body, dict) else {}

    # ----- alertas / relatórios ------------------------------------------- #

    def _alert_params(
        self,
        to_email: str,
        target_url: str,
        score: int,
        semaphore: str,
        fail_count: int,
        severity_counts: Dict[str, int],
        unsubscribe_link: Optional[str] = None,
        risk_messages: Optional[list] = None,
        target_id=None,
    ) -> Dict[str, Any]:
        """Monta o payload Resend de um alerta (from/to/subject/html).

        Compartilhado pelo envio único (`send_alert`) e pelo batch (`send_alert_batch`).
        """
        site = site_name(target_url)
        if unsubscribe_link is None:
            secret = os.environ.get("UNSUBSCRIBE_SECRET")
            if secret:
                unsubscribe_link = build_unsubscribe_link(to_email, secret)
        html = _env.get_template("alert.html").render(
            **self._score_ctx(score, semaphore),
            site_name=site,
            target_url=target_url,
            fail_count=fail_count,
            sev=severity_counts or {},
            risk_messages=(risk_messages or [])[:3],  # KL-20: máx 3 riscos no e-mail
            result_link=utm_result_link(target_url, "alerta", target_id),
            lgpd=LGPD_SHORT,
            unsubscribe_link=unsubscribe_link,
        )
        subject = f"⚠️ Encontramos {fail_count} problema(s) de segurança em {site}"
        return {"from": self.from_address, "to": [to_email], "subject": subject, "html": html}

    async def send_alert(
        self,
        to_email: str,
        target_url: str,
        score: int,
        semaphore: str,
        fail_count: int,
        severity_counts: Dict[str, int],
        unsubscribe_link: Optional[str] = None,
        risk_messages: Optional[list] = None,
        target_id=None,
    ) -> Dict[str, Any]:
        """Alerta gratuito (semáforo) — o anzol do funil."""
        return await self._send(self._alert_params(
            to_email, target_url, score, semaphore, fail_count, severity_counts,
            unsubscribe_link=unsubscribe_link, risk_messages=risk_messages, target_id=target_id))

    async def send_alert_batch(self, alerts: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Envia até 100 alertas em 1 request via Resend Batch API (KL-23).

        Cada item de ``alerts`` é um dict com: ``to_email``, ``target_url``,
        ``score``, ``semaphore``, ``fail_count``, ``severity_counts``,
        ``risk_messages``, ``unsubscribe_link`` (e opcionalmente ``target_id``,
        ``risk_summary`` — ignorado na renderização). Retorna
        ``{"sent": N, "failed": N, "ids": [...]}`` com os IDs na ordem do input.
        """
        batch = list(alerts)[:BATCH_MAX]
        if not batch:
            return {"sent": 0, "failed": 0, "ids": []}
        payloads = [
            self._alert_params(
                a["to_email"], a["target_url"], a.get("score", 0), a.get("semaphore", ""),
                a.get("fail_count", 0), a.get("severity_counts") or {},
                unsubscribe_link=a.get("unsubscribe_link"),
                risk_messages=a.get("risk_messages"), target_id=a.get("target_id"))
            for a in batch
        ]
        return await self._send_batch(payloads, batch)

    def _evolution_params(
        self,
        to_email: str,
        target_url: str,
        old_score: int,
        new_score: int,
        evolution: str,
        semaphore: str,
        fail_count: int,
        severity_counts: Dict[str, int],
        price_display: str,
        unsubscribe_link: Optional[str] = None,
        risk_messages: Optional[list] = None,
        target_id=None,
    ) -> Dict[str, Any]:
        """Monta o payload Resend de um e-mail de evolução (KL-13).

        Compartilhado pelo envio único (`send_evolution`) e pelo batch
        (`send_evolution_batch`). Escolhe o template pelo tipo de evolução.
        """
        site = site_name(target_url)
        if unsubscribe_link is None:
            secret = os.environ.get("UNSUBSCRIBE_SECRET")
            if secret:
                unsubscribe_link = build_unsubscribe_link(to_email, secret)

        templates = {
            "improved": ("evolution_improved.html",
                         f"🎉 Seu site melhorou! {site} — de {old_score} para {new_score}"),
            "worsened": ("evolution_worsened.html",
                         f"⚠️ Novos problemas encontrados — {site} caiu de {old_score} para {new_score}"),
        }
        # unchanged / first_rescan caem no template mensal.
        template_name, subject = templates.get(
            evolution,
            ("evolution_unchanged.html", f"📊 Varredura mensal — {site} permanece em {new_score}/100"),
        )
        html = _env.get_template(template_name).render(
            **self._score_ctx(new_score, semaphore),
            old_score=old_score,
            new_score=new_score,
            site_name=site,
            target_url=target_url,
            fail_count=fail_count,
            sev=severity_counts or {},
            risk_messages=(risk_messages or [])[:3],  # KL-20
            result_link=utm_result_link(target_url, f"evolucao_{evolution}", target_id),
            price_display=price_display,
            lgpd=LGPD_SHORT,
            unsubscribe_link=unsubscribe_link,
        )
        return {"from": self.from_address, "to": [to_email], "subject": subject, "html": html}

    async def send_evolution(
        self,
        to_email: str,
        target_url: str,
        old_score: int,
        new_score: int,
        evolution: str,
        semaphore: str,
        fail_count: int,
        severity_counts: Dict[str, int],
        price_display: str,
        unsubscribe_link: Optional[str] = None,
        risk_messages: Optional[list] = None,
        target_id=None,
    ) -> Dict[str, Any]:
        """E-mail de evolução do score (KL-13). Escolhe o template pelo tipo."""
        return await self._send(self._evolution_params(
            to_email, target_url, old_score, new_score, evolution, semaphore,
            fail_count, severity_counts, price_display, unsubscribe_link=unsubscribe_link,
            risk_messages=risk_messages, target_id=target_id))

    async def send_evolution_batch(self, evolutions: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Envia até 100 e-mails de evolução em 1 request via Resend Batch API (KL-23).

        Cada item é um dict com: ``to_email``, ``target_url``, ``old_score``,
        ``new_score``, ``evolution``, ``semaphore``, ``fail_count``,
        ``severity_counts``, ``price_display``, ``risk_messages``,
        ``unsubscribe_link`` (e opcionalmente ``target_id``). Chaves extras
        (ex.: ``rescan_id``) são ignoradas. Retorna ``{"sent", "failed", "ids"}``
        com os IDs na ordem do input.
        """
        batch = list(evolutions)[:BATCH_MAX]
        if not batch:
            return {"sent": 0, "failed": 0, "ids": []}
        payloads = [
            self._evolution_params(
                e["to_email"], e["target_url"], e.get("old_score"), e.get("new_score"),
                e.get("evolution", "unchanged"), e.get("semaphore", ""),
                e.get("fail_count", 0), e.get("severity_counts") or {},
                e.get("price_display", ""), unsubscribe_link=e.get("unsubscribe_link"),
                risk_messages=e.get("risk_messages"), target_id=e.get("target_id"))
            for e in batch
        ]
        return await self._send_batch(payloads, batch)

    async def send_report(
        self,
        to_email: str,
        target_url: str,
        score: int,
        executive_pdf: bytes,
        technical_pdf: bytes,
    ) -> Dict[str, Any]:
        """Entrega do relatório pago, com os dois PDFs anexados."""
        site = site_name(target_url)
        semaphore = semaphore_from_score(score)
        html = _env.get_template("report_delivery.html").render(
            **self._score_ctx(score, semaphore),
            site_name=site,
            target_url=target_url,
        )
        subject = f"✅ Seu Relatório de Segurança — {site} — Score {score}/100"
        attachments = [
            {"filename": f"klarim_executivo_{site}.pdf",
             "content": base64.b64encode(executive_pdf).decode("ascii")},
            {"filename": f"klarim_tecnico_{site}.pdf",
             "content": base64.b64encode(technical_pdf).decode("ascii")},
        ]
        return await self._send({
            "from": self.from_address, "to": [to_email], "subject": subject,
            "html": html, "attachments": attachments,
        })

    async def send_recovery_link(self, to_email: str, recovery_url: str) -> Dict[str, Any]:
        """Envia o link temporário de recuperação de relatórios."""
        sep = "&" if "?" in recovery_url else "?"
        recovery_url = f"{recovery_url}{sep}utm_source=klarim&utm_medium=email&utm_campaign=recuperacao"
        html = _env.get_template("recovery.html").render(recovery_url=recovery_url)
        return await self._send({
            "from": self.from_address,
            "to": [to_email],
            "subject": "🔑 Acesso aos seus relatórios Klarim",
            "html": html,
        })

    async def send_test(self, to_email: str) -> Dict[str, Any]:
        """E-mail de teste para validar a configuração do Resend."""
        html = (
            "<div style=\"font-family:Arial,sans-serif;background:#0D1117;color:#E6EDF3;"
            "padding:24px;border-radius:8px\">"
            "<h2 style=\"color:#FF6B35\">Klarim — teste de e-mail</h2>"
            "<p>Se você recebeu isto, a integração com o Resend está funcionando. ✅</p>"
            "<p style=\"color:#8B949E;font-size:12px\">klarim.net</p></div>"
        )
        return await self._send(
            {"from": self.from_address, "to": [to_email], "subject": "Teste — Klarim", "html": html}
        )

    async def send_contact(
        self, name: str, email: str, message: str, to_address: str = "scan@klarim.net"
    ) -> Dict[str, Any]:
        """Encaminha uma mensagem do formulário de contato do site para o time.

        `reply_to` aponta para o remetente, então basta responder o e-mail. Os
        valores já chegam validados/sanitizados pelo endpoint; ainda assim faz
        escape de HTML (defense-in-depth).
        """
        import html as _html

        safe_name = _html.escape(name or "").strip() or "—"
        safe_email = _html.escape(email or "")
        safe_message = _html.escape(message or "").replace("\n", "<br>")
        body = (
            "<div style=\"font-family:Arial,sans-serif;background:#0D1117;color:#E6EDF3;"
            "padding:24px;border-radius:8px\">"
            "<h2 style=\"color:#FF6B35\">Nova mensagem de contato — klarim.net</h2>"
            f"<p><b>Nome:</b> {safe_name}</p>"
            f"<p><b>E-mail:</b> {safe_email}</p>"
            f"<p><b>Mensagem:</b><br>{safe_message}</p>"
            "</div>"
        )
        params = {
            "from": self.from_address,
            "to": [to_address],
            "subject": f"[Contato Klarim] {safe_name if safe_name != '—' else safe_email}",
            "html": body,
        }
        if email:
            params["reply_to"] = email
        return await self._send(params)

    # ----- helpers --------------------------------------------------------- #

    def _score_ctx(self, score: int, semaphore: str) -> Dict[str, Any]:
        return {
            "score": score,
            "semaphore": semaphore,
            "semaphore_label": SEMAPHORE_LABEL.get(semaphore, ""),
            "semaphore_emoji": SEMAPHORE_EMOJI.get(semaphore, ""),
            "score_color": SEMAPHORE_COLOR.get(semaphore, "#FF4D4D"),
            "referral_link": f"{SITE_BASE}/parceiros",
        }
