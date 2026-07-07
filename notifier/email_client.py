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
import os
from pathlib import Path
from typing import Any, Dict, Optional
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
    "segurança podem resultar em sanções pela LGPD (até R$ 50 milhões por infração)."
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


def unsubscribe_token(email: str, secret: str) -> str:
    """HMAC-SHA256 do e-mail (32 chars) — impede descadastro por terceiros."""
    return hmac.new(secret.encode(), email.strip().lower().encode(), hashlib.sha256).hexdigest()[:32]


def build_unsubscribe_link(email: str, secret: str) -> str:
    return f"{SITE_BASE}/api/unsubscribe?email={quote(email)}&token={unsubscribe_token(email, secret)}"


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

    # ----- alertas / relatórios ------------------------------------------- #

    async def send_alert(
        self,
        to_email: str,
        target_url: str,
        score: int,
        semaphore: str,
        fail_count: int,
        severity_counts: Dict[str, int],
        unsubscribe_link: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Alerta gratuito (semáforo) — o anzol do funil."""
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
            result_link=f"{SITE_BASE}/result?url={quote(target_url, safe='')}",
            lgpd=LGPD_SHORT,
            unsubscribe_link=unsubscribe_link,
        )
        subject = f"⚠️ Encontramos {fail_count} problema(s) de segurança em {site}"
        return await self._send(
            {"from": self.from_address, "to": [to_email], "subject": subject, "html": html}
        )

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
    ) -> Dict[str, Any]:
        """E-mail de evolução do score (KL-13). Escolhe o template pelo tipo."""
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
            result_link=f"{SITE_BASE}/result?url={quote(target_url, safe='')}",
            price_display=price_display,
            lgpd=LGPD_SHORT,
            unsubscribe_link=unsubscribe_link,
        )
        return await self._send(
            {"from": self.from_address, "to": [to_email], "subject": subject, "html": html}
        )

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
