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
from uuid import uuid4

from jinja2 import Environment, FileSystemLoader, select_autoescape

_HERE = Path(__file__).resolve().parent
_env = Environment(
    loader=FileSystemLoader(str(_HERE / "templates")),
    autoescape=select_autoescape(["html", "xml"]),
)

# Remetente padrão que funciona SEM domínio verificado (bom para testes).
DEFAULT_FROM = "Klarim <onboarding@resend.dev>"
SITE_BASE = "https://klarim.net"
# KL-67 — Reply-To de TODOS os e-mails: o `seguranca@`/`alerta@` são só-envio (Resend,
# sem inbox); as respostas caem em `scan@klarim.net` (inbox Hostinger, painel Inbox).
REPLY_TO_DEFAULT = "scan@klarim.net"

SEMAPHORE_COLOR = {"verde": "#00D26A", "amarelo": "#F2C744", "vermelho": "#FF4D4D"}
SEMAPHORE_LABEL = {"verde": "VERDE", "amarelo": "AMARELO", "vermelho": "VERMELHO"}
SEMAPHORE_EMOJI = {"verde": "🟢", "amarelo": "🟡", "vermelho": "🔴"}

LGPD_SHORT = (
    "Se o seu site coleta dados pessoais (nome, CPF, e-mail, cartão), falhas de "
    "segurança podem resultar em sanções e multas pela LGPD (até R$ 50 milhões por infração)."
)


class KlarimMailerError(RuntimeError):
    """Erro ao enviar e-mail via Resend (chave inválida, domínio não verificado…)."""


# KL-62: tipos de e-mail (os 20 caminhos do diagnóstico). Usado no `email_log` para
# discriminar volume/reputação por canal. `alert_score100` é derivado do alerta.
EMAIL_TYPES = {
    "alert": "Alerta de segurança",
    "alert_score100": "Alerta score 100",
    "evolution": "Email de evolução (rescan)",
    "verification_code": "Código de verificação",
    "profile_view": "Notificação perfil consultado",
    "report_delivery": "Entrega de relatório",
    "report_send": "Envio de PDF por email",
    "password_reset": "Redefinição de senha",
    "account_deleted": "Conta excluída",
    "account_evolution": "Evolução de monitoramento",
    "monitor_offer": "Oferta de monitoramento",
    "monitor_alert": "Alerta de site monitorado",
    "monitor_restored": "Site monitorado restaurado",
    "recovery": "Recuperação de relatório",
    "contact": "Formulário de contato",
    "test": "Email de teste",
    "admin_alert": "Alerta admin (scan-and-report)",
    "admin_report": "Relatório admin",
    "signup_verification": "Código de cadastro (conta)",
    "ownership_verification": "Verificação de propriedade (KL-68)",
    "site_removed": "Site removido do monitoramento (KL-69)",
    "account_deactivated": "Conta desativada pelo admin (KL-69)",
    "account_reactivated": "Conta reativada pelo admin (KL-69)",
    "bulletin": "Boletim de segurança — dono (KL-44 P3)",
    "bulletin_technician": "Laudo técnico — técnico vinculado (KL-44 P3)",
    "technician_invite": "Convite de técnico (KL-44 P3)",
    "vigilia_ssl": "Vigília — certificado SSL",
    "vigilia_domain": "Vigília — registro do domínio",
    "vigilia_score": "Vigília — score de segurança",
    "vigilia_email": "Vigília — proteção de e-mail",
    "vigilia_reputation": "Vigília — reputação",
    "upgrade_confirmed": "Assinatura — pagamento confirmado (KL-44 P6)",
    "trial_warning": "Assinatura — aviso de expiração de trial (KL-44 P6)",
    "trial_expired": "Assinatura — trial expirado (KL-44 P6)",
    "vigilia_uptime": "Vigília — disponibilidade (KL-44 P4)",
    "vigilia_changes": "Vigília — integridade do site (KL-44 P4)",
    "vigilia_phishing": "Vigília — domínios suspeitos (KL-44 P4)",
}


def _domain_of_from(from_str: Any) -> str:
    """Extrai o domínio do campo `from` ('Nome <a@b.com>' ou 'a@b.com') — usado no
    email_log (migração klarimscan.com), para filtrar por domínio de envio."""
    s = str(from_str or "")
    if "<" in s and ">" in s:
        s = s[s.index("<") + 1:s.index(">")]
    return s.rsplit("@", 1)[-1].strip().lower() if "@" in s else ""


def _first_recipient(to: Any) -> str:
    """Normaliza o destinatário (o Resend aceita str ou lista) → primeiro e-mail."""
    if isinstance(to, (list, tuple)):
        return str(to[0]) if to else ""
    return str(to or "")


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


def utm_result_link(target_url: str, campaign: str, target_id=None,
                    bonus_token: Optional[str] = None) -> str:
    """Link /result com UTM (KL-21) — permite rastrear cliques do e-mail por campanha
    e por alvo. Sem target_id, usa o domínio como utm_content. ``bonus_token`` (KL-31)
    adiciona ``&bonus=full&t=<token>`` para o fluxo de scan completo gratuito."""
    content = f"target_{target_id}" if target_id else site_name(target_url)
    link = (f"{SITE_BASE}/result?url={quote(target_url, safe='')}"
            f"&utm_source=klarim&utm_medium=email&utm_campaign={quote(campaign, safe='')}"
            f"&utm_content={quote(content, safe='')}")
    if bonus_token:
        link += f"&bonus=full&t={quote(bonus_token, safe='')}"
    return link


def unsubscribe_token(email: str, secret: str) -> str:
    """HMAC-SHA256 do e-mail (32 chars) — impede descadastro por terceiros."""
    return hmac.new(secret.encode(), email.strip().lower().encode(), hashlib.sha256).hexdigest()[:32]


def build_unsubscribe_link(email: str, secret: str) -> str:
    return f"{SITE_BASE}/api/unsubscribe?email={quote(email)}&token={unsubscribe_token(email, secret)}"


def list_unsubscribe_headers(unsubscribe_url: Optional[str]) -> Dict[str, str]:
    """Headers RFC 8058 (one-click) para e-mails **proativos** (alerta, profile_view).
    O botão "Cancelar inscrição" do Gmail/Outlook/Apple Mail usa isto; melhora a
    reputação e evita cliques falsos de pre-fetch. Vazio se não há URL de descadastro."""
    if not unsubscribe_url:
        return {}
    return {"List-Unsubscribe": f"<{unsubscribe_url}>",
            "List-Unsubscribe-Post": "List-Unsubscribe=One-Click"}


# --------------------------------------------------------------------------- #
# Corpo em TEXTO PURO dos e-mails PROATIVOS (alerta + perfil consultado) — KL-44.
# O template HTML (alert.html / alert_score100.html / profile_view.html) foi mantido
# como referência, mas os envios proativos saem em plain text: parecem menos "e-mail
# marketing" (dark mode, botões, cards) e caem menos no spam. O CTA aponta para o
# perfil público `/site/{domain}` com UTM (não mais o /result).
# --------------------------------------------------------------------------- #

def proactive_profile_link(domain: str, campaign: str) -> str:
    """Link para o perfil público `/site/{domain}` com UTM (e-mail proativo)."""
    return (f"{SITE_BASE}/site/{domain}"
            f"?utm_source=klarim&utm_medium=email&utm_campaign={campaign}")


def proactive_sector_link(sector_slug: str, campaign: str) -> str:
    """KL-20 — link para a página de setor `/setor/{slug}` com UTM (2º CTA do alerta)."""
    return (f"{SITE_BASE}/setor/{sector_slug}"
            f"?utm_source=klarim&utm_medium=email&utm_campaign={campaign}")


def _unsub_line(unsubscribe_url: Optional[str], label: str) -> str:
    """Linha de descadastro no rodapé — omitida se não houver link (evita 'None')."""
    return f"\n\n{label} {unsubscribe_url}" if unsubscribe_url else ""


def alert_subject(domain: str, is_score100: bool = False) -> str:
    """Assunto do alerta proativo (KL-44). Score 100 verde → parabéns."""
    if is_score100:
        return f"Parabéns! O site {domain} alcançou nota máxima em segurança"
    return f"Alguém verificou a segurança do site {domain}"


def build_alert_text(domain: str, score: int, unsubscribe_url: Optional[str],
                     is_score100: bool = False, risk_summary: Optional[dict] = None,
                     benchmark_line: str = "", sector_slug: str = "") -> str:
    """Corpo em texto puro do alerta proativo (KL-44 + KL-20).

    KL-20: com `risk_summary` (de `reporter.risk_messages.build_risk_summary`) o alerta
    lista até 3 **consequências concretas para o negócio** (linguagem de dono, não jargão)
    em vez do bloco genérico; `benchmark_line` compara com o setor; e há **CTA duplo**
    (perfil + página de setor). Sem `risk_summary` → corpo genérico (retrocompatível)."""
    if is_score100:
        body = (
            "Olá,\n\n"
            f"Parabéns! O site {domain} alcançou nota 100/100 em segurança\n"
            "digital na plataforma Klarim.\n\n"
            "Veja o resultado completo em:\n"
            f"{proactive_profile_link(domain, 'alerta_score100')}\n\n"
            "Seu site passou em todas as 48 verificações de segurança.\n"
            "Isso é raro — menos de 2% dos sites analisados atingem essa nota.\n\n"
            "Se este é o seu site, crie uma conta gratuita para monitorar\n"
            "e manter a nota máxima.\n\n"
            "--\nKlarim Scanner\nklarimscan.com"
        )
        return body + _unsub_line(unsubscribe_url, "Não quer receber mais avisos?")

    lines = ["Olá,", ""]
    risks = (risk_summary or {}).get("risks") or []
    if risks:
        head = benchmark_line or f"O site {domain} recebeu nota {score}/100 em segurança."
        lines += [head, "", "O que isso pode significar para o seu negócio:"]
        lines += [f"⚠ {r['message']}" for r in risks]
        remaining = (risk_summary or {}).get("remaining_count") or 0
        if remaining > 0:
            lines.append(f"E mais {remaining} {'item' if remaining == 1 else 'itens'} "
                         "que podem ser melhorados.")
    else:
        lines += [f"O site {domain} foi verificado na plataforma Klarim e recebeu",
                  f"nota {score}/100 em segurança digital."]
        if benchmark_line:
            lines += ["", benchmark_line]
    # CTA duplo (KL-20): perfil + página de setor (expõe o ecossistema KL-74).
    lines += ["", "Veja seu resultado completo:", proactive_profile_link(domain, "alerta")]
    if sector_slug and sector_slug != "outro":
        plural = (risk_summary or {}).get("plural") or "sites"
        lines += ["", f"Compare com o setor de {plural}:",
                  proactive_sector_link(sector_slug, "alerta")]
    lines += ["", "O Klarim é uma ferramenta gratuita que analisa a segurança de",
              "sites brasileiros de forma passiva — sem acessar dados nem instalar nada.",
              "A verificação cobre certificado SSL, headers de proteção, e-mail e mais 48 pontos.",
              "", "Se este é o seu site, crie uma conta gratuita para monitorar o score",
              "e receber alertas quando algo mudar.", "", "--\nKlarim Scanner\nklarimscan.com"]
    return "\n".join(lines) + _unsub_line(unsubscribe_url, "Não quer receber mais avisos?")


def build_profile_view_text(domain: str, score: int,
                            unsubscribe_url: Optional[str]) -> str:
    """Corpo em texto puro da notificação 'alguém consultou seu perfil' (KL-44)."""
    body = (
        "Olá,\n\n"
        f"Alguém consultou o perfil de segurança do site {domain}\n"
        f"no Klarim. A nota atual é {score}/100.\n\n"
        "Veja o que foi encontrado:\n"
        f"{proactive_profile_link(domain, 'profile_view')}\n\n"
        "O Klarim é uma plataforma gratuita de segurança web.\n"
        "A análise é 100% passiva — nenhum dado do site foi acessado.\n\n"
        "--\nKlarim Scanner\nklarimscan.com"
    )
    return body + _unsub_line(unsubscribe_url, "Não deseja receber avisos?")


# Endpoint da Batch API do Resend (envia até 100 e-mails em 1 request — KL-23).
RESEND_BATCH_URL = "https://api.resend.com/emails/batch"
RESEND_EMAILS_URL = "https://api.resend.com/emails"  # GET /emails/{id} — status (KL-24)
BATCH_MAX = 100  # limite da Resend Batch API por request


def verify_resend_signature(secret: str, headers: Any, raw_body: Any) -> bool:
    """Valida a assinatura do webhook do Resend (esquema **Svix**, KL-24).

    O Resend assina via Svix: headers ``svix-id``, ``svix-timestamp``,
    ``svix-signature``; o conteúdo assinado é ``{id}.{timestamp}.{body}`` e o
    segredo é ``whsec_<base64>``. O header de assinatura traz itens
    ``v1,<base64sig>`` separados por espaço. Comparação em tempo constante.
    ``headers`` pode ser um dict ou os headers do Request (`.get` case-insensitive).
    """
    def _h(*names):
        for n in names:
            v = headers.get(n)
            if v:
                return v
        return ""

    svix_id = _h("svix-id", "webhook-id")
    svix_ts = _h("svix-timestamp", "webhook-timestamp")
    svix_sig = _h("svix-signature", "webhook-signature")
    if not (secret and svix_id and svix_ts and svix_sig):
        return False

    body = raw_body.decode("utf-8") if isinstance(raw_body, (bytes, bytearray)) else str(raw_body)
    signed = f"{svix_id}.{svix_ts}.{body}"
    key = secret[len("whsec_"):] if secret.startswith("whsec_") else secret
    try:
        secret_bytes = base64.b64decode(key)
    except Exception:  # noqa: BLE001 - segredo não-base64: usa cru
        secret_bytes = key.encode("utf-8")
    expected = base64.b64encode(
        hmac.new(secret_bytes, signed.encode("utf-8"), hashlib.sha256).digest()
    ).decode("ascii")

    for part in svix_sig.split():
        sig = part.split(",", 1)[1] if "," in part else part
        if hmac.compare_digest(sig, expected):
            return True
    return False


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
    def __init__(self, api_key: str, from_address: Optional[str] = None,
                 store: Any = None) -> None:
        if not api_key:
            raise ValueError("RESEND_API_KEY não configurada")
        self.api_key = api_key
        self.from_address = from_address or os.environ.get("RESEND_FROM") or DEFAULT_FROM
        # KL-62: acesso ao store para log unificado + blocklist. Se não for injetado
        # (produção), usa o singleton lazy — sem import no topo (evita ciclo).
        self._store = store

    def _proactive_from(self) -> str:
        """Remetente dos e-mails **PROATIVOS** (cold: alerta + perfil consultado).
        Migração de reputação: sai de `ALERT_FROM_EMAIL`/`ALERT_FROM_NAME`
        (`alerta@klarimscan.com`), isolando o domínio principal. **Fail-safe:** sem a
        var, cai para `self.from_address` (o remetente normal) — nunca quebra. Lido do
        env a cada envio, então a troca vale sem reiniciar."""
        email = (os.environ.get("ALERT_FROM_EMAIL") or "").strip()
        if not email:
            return self.from_address
        name = (os.environ.get("ALERT_FROM_NAME") or "Klarim").strip()
        return f"{name} <{email}>"

    # ----- log unificado + blocklist (KL-62) ------------------------------- #

    def _get_store(self) -> Any:
        if self._store is not None:
            return self._store
        try:
            from discovery.store import get_target_store
            return get_target_store()
        except Exception:  # noqa: BLE001 - sem store, o mailer ainda envia (fail-open)
            return None

    async def _is_blocked(self, email: str) -> bool:
        """True se o e-mail está na blocklist (KL-24). Fail-open: erro → False (envia)."""
        if not email:
            return False
        store = self._get_store()
        if store is None:
            return False
        try:
            return bool(await store.is_email_blocked(email))
        except Exception:  # noqa: BLE001 - nunca bloquear um envio por falha de infra
            return False

    async def _log_email(self, **kw: Any) -> None:
        """Grava no email_log (KL-62). Fire-and-forget: nunca derruba o envio."""
        store = self._get_store()
        if store is None:
            return
        try:
            await store.log_email(**kw)
        except Exception as exc:  # noqa: BLE001
            print(f"[email_log] falha ao gravar: {exc!r}", flush=True)

    # ----- envio (thread) -------------------------------------------------- #

    async def _send(self, params: Dict[str, Any], *, email_type: str = "unknown",
                    target_id: Optional[int] = None, domain: Optional[str] = None,
                    source: Optional[str] = None, skip_blocklist: bool = False) -> Dict[str, Any]:
        """Envia um e-mail individual e o registra no `email_log` (KL-62).

        Checa a blocklist antes (exceto transacionais, `skip_blocklist=True`). Loga
        `blocked`/`sent`/`failed`. Um e-mail bloqueado retorna `email_id=None`."""
        params.setdefault("reply_to", REPLY_TO_DEFAULT)  # KL-67 (send_contact já define o seu)
        to = _first_recipient(params.get("to"))
        subject = params.get("subject")
        from_domain = _domain_of_from(params.get("from"))
        if not skip_blocklist and await self._is_blocked(to):
            await self._log_email(email_id=None, to_email=to, email_type=email_type,
                                  subject=subject, target_id=target_id, domain=domain,
                                  status="blocked", blocked_reason="blocklist", source=source,
                                  from_domain=from_domain)
            print(f"[email] BLOQUEADO (blocklist) {email_type} → {to}", flush=True)
            return {"email_id": None, "raw": None, "blocked": True}
        try:
            result = await asyncio.to_thread(self._send_sync, params)
        except Exception as exc:  # noqa: BLE001 - loga a falha e propaga
            await self._log_email(email_id=None, to_email=to, email_type=email_type,
                                  subject=subject, target_id=target_id, domain=domain,
                                  status="failed", error=str(exc), source=source,
                                  from_domain=from_domain)
            raise
        await self._log_email(email_id=result.get("email_id"), to_email=to,
                              email_type=email_type, subject=subject, target_id=target_id,
                              domain=domain, status="sent", source=source,
                              from_domain=from_domain)
        return result

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
        self, payloads: List[Dict[str, Any]], items: List[Dict[str, Any]],
        *, email_type: str = "unknown", source: Optional[str] = None,
        skip_blocklist: bool = False, types: Optional[List[str]] = None
    ) -> Dict[str, Any]:
        """Envia os payloads via Batch API (com idempotency key) e loga cada e-mail no
        `email_log` (KL-62). ``items`` são os dicts originais (com ``to_email``,
        ``target_id``, ``target_url``). ``types`` (opcional) dá o email_type por item.

        A blocklist é honrada **preservando o alinhamento** do retorno: um e-mail
        bloqueado vira ``None`` na sua posição em ``ids`` (o `AlertWorker` mapeia
        `ids[i]` → `alerts[i]` posicionalmente). Retorna ``{sent, failed, ids}`` com
        ``ids`` 1:1 com o input."""
        for p in payloads:   # KL-67 — Reply-To → scan@klarim.net em todo e-mail do batch
            p.setdefault("reply_to", REPLY_TO_DEFAULT)
        n = len(payloads)
        to_list = [_first_recipient(p.get("to")) for p in payloads]
        blocked = [False] * n
        if not skip_blocklist:
            for i, to in enumerate(to_list):
                if to and await self._is_blocked(to):
                    blocked[i] = True
        send_idx = [i for i in range(n) if not blocked[i]]
        send_payloads = [payloads[i] for i in send_idx]
        send_items = [items[i] for i in send_idx]

        sent_ids: List[Optional[str]] = []
        if send_payloads:
            key = batch_idempotency_key(send_items)
            body = await self._send_batch_raw(send_payloads, key)
            data = body.get("data") if isinstance(body, dict) else None
            sent_ids = [d.get("id") for d in (data or []) if isinstance(d, dict)]

        # Reconstrói os ids 1:1 com o input (None nas posições bloqueadas/sem id) e loga.
        batch_id = uuid4().hex
        sent_iter = iter(sent_ids)
        ids: List[Optional[str]] = []
        for i in range(n):
            etype = types[i] if types and i < len(types) else email_type
            it = items[i] if i < len(items) else {}
            target_id = it.get("target_id")
            turl = it.get("target_url")
            dom = site_name(turl) if turl else None
            subject = payloads[i].get("subject")
            fdom = _domain_of_from(payloads[i].get("from"))
            if blocked[i]:
                ids.append(None)
                await self._log_email(email_id=None, to_email=to_list[i], email_type=etype,
                                      subject=subject, target_id=target_id, domain=dom,
                                      status="blocked", blocked_reason="blocklist",
                                      source=source, batch_id=batch_id, from_domain=fdom)
            else:
                eid = next(sent_iter, None)
                ids.append(eid)
                await self._log_email(email_id=eid, to_email=to_list[i], email_type=etype,
                                      subject=subject, target_id=target_id, domain=dom,
                                      status=("sent" if eid else "failed"),
                                      source=source, batch_id=batch_id, from_domain=fdom)
        sent = len([i for i in ids if i])
        return {"sent": sent, "failed": n - sent, "ids": ids}

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

    async def get_email_event(self, email_id: str) -> Optional[str]:
        """Último evento de um e-mail no Resend (KL-24): ``delivered`` / ``bounced`` /
        ``complained`` / ``delivery_delayed`` … Retorna None em erro/ausência."""
        import httpx  # import tardio

        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    f"{RESEND_EMAILS_URL}/{email_id}",
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    timeout=15,
                )
        except Exception:  # noqa: BLE001 - erro de rede não deve derrubar o backfill
            return None
        if resp.status_code >= 400:
            return None
        try:
            body = resp.json()
        except Exception:  # noqa: BLE001
            return None
        return body.get("last_event") if isinstance(body, dict) else None

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
        bonus_token: Optional[str] = None,
        sector: str = "",
        risk_summary: Optional[dict] = None,
        benchmark_line: str = "",
    ) -> Dict[str, Any]:
        """Monta o payload Resend de um alerta em **texto puro** (from/to/subject/text).

        Compartilhado pelo envio único (`send_alert`) e pelo batch (`send_alert_batch`).
        KL-44: alerta em plain text (não HTML) → menos cara de "e-mail marketing", cai
        menos no spam; o CTA aponta para o perfil público `/site/{domain}`. Score 100
        verde (KL-31) → assunto/corpo de **parabéns**; senão o alerta normal (KL-27).
        Os templates HTML (`alert.html`/`alert_score100.html`) foram mantidos como
        referência, mas não são mais usados no envio. `fail_count`, `severity_counts`,
        `risk_messages`, `target_id` e `bonus_token` seguem na assinatura (chamadores
        inalterados), mas o corpo em texto não os utiliza.
        """
        site = site_name(target_url)
        if unsubscribe_link is None:
            secret = os.environ.get("UNSUBSCRIBE_SECRET")
            if secret:
                unsubscribe_link = build_unsubscribe_link(to_email, secret)

        is_100 = score == 100 and (semaphore or "").lower() == "verde"
        # PROATIVO (cold) → remetente do domínio de warmup (klarimscan.com), plain text.
        params = {
            "from": self._proactive_from(),
            "to": [to_email],
            "subject": alert_subject(site, is_100),
            "text": build_alert_text(site, score, unsubscribe_link, is_score100=is_100,
                                     risk_summary=risk_summary, benchmark_line=benchmark_line,
                                     sector_slug=sector),
        }
        hdrs = list_unsubscribe_headers(unsubscribe_link)   # RFC 8058 one-click (proativo)
        if hdrs:
            params["headers"] = hdrs
        return params

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
        bonus_token: Optional[str] = None,
        email_type: Optional[str] = None,
        source: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Alerta gratuito (semáforo) — o anzol do funil. KL-62: registra em email_log,
        respeita a blocklist (proativo). `email_type` opcional sobrescreve (ex.: admin)."""
        is_100 = score == 100 and (semaphore or "").lower() == "verde"
        etype = email_type or ("alert_score100" if is_100 else "alert")
        return await self._send(self._alert_params(
            to_email, target_url, score, semaphore, fail_count, severity_counts,
            unsubscribe_link=unsubscribe_link, risk_messages=risk_messages,
            target_id=target_id, bonus_token=bonus_token),
            email_type=etype, target_id=target_id, domain=site_name(target_url), source=source)

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
                risk_messages=a.get("risk_messages"), target_id=a.get("target_id"),
                bonus_token=a.get("bonus_token"),
                sector=a.get("sector") or "", risk_summary=a.get("risk_summary"),
                benchmark_line=a.get("benchmark_line") or "")     # KL-20
            for a in batch
        ]
        # KL-62: email_type por item (score 100 verde → alert_score100).
        types = ["alert_score100" if (a.get("score") == 100 and str(a.get("semaphore", "")).lower() == "verde")
                 else "alert" for a in batch]
        return await self._send_batch(payloads, batch, email_type="alert",
                                      source="alert_worker", types=types)

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

        template_name = {
            "improved": "evolution_improved.html",
            "worsened": "evolution_worsened.html",
        }.get(evolution, "evolution_unchanged.html")  # unchanged / first_rescan
        # KL-27: assunto neutro e único, sem preço e sem detalhes de risco.
        subject = f"{site} — atualização da avaliação de segurança"
        html = _env.get_template(template_name).render(
            **self._score_ctx(new_score, semaphore),
            old_score=old_score,
            new_score=new_score,
            site_name=site,
            target_url=target_url,
            fail_count=fail_count,
            result_link=utm_result_link(target_url, f"evolucao_{evolution}", target_id),
            unsubscribe_link=unsubscribe_link,
        )
        params = {"from": self.from_address, "to": [to_email], "subject": subject, "html": html}
        hdrs = list_unsubscribe_headers(unsubscribe_link)  # proativo a lead → RFC 8058
        if hdrs:
            params["headers"] = hdrs
        return params

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
        """E-mail de evolução do score (KL-13). Escolhe o template pelo tipo. KL-62:
        registra em email_log, respeita a blocklist (proativo)."""
        return await self._send(self._evolution_params(
            to_email, target_url, old_score, new_score, evolution, semaphore,
            fail_count, severity_counts, price_display, unsubscribe_link=unsubscribe_link,
            risk_messages=risk_messages, target_id=target_id),
            email_type="evolution", target_id=target_id, domain=site_name(target_url),
            source="rescan_worker")

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
        return await self._send_batch(payloads, batch, email_type="evolution",
                                      source="rescan_worker")

    async def send_report(
        self,
        to_email: str,
        target_url: str,
        score: int,
        executive_pdf: bytes,
        technical_pdf: bytes,
        email_type: str = "report_delivery",
        source: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Entrega do relatório (PDFs anexados). Transacional → **ignora a blocklist**
        (o usuário tem direito de receber). `email_type`: report_delivery / report_send /
        admin_report (KL-62)."""
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
        }, email_type=email_type, domain=site, source=source, skip_blocklist=True)

    async def send_verification_code(self, to_email: str, code: str, domain: str) -> Dict[str, Any]:
        """Envia o código de 6 dígitos para verificar o e-mail antes do scan (KL-25).
        Transacional (o usuário pediu) → ignora a blocklist, mas é registrado (KL-62)."""
        html = _env.get_template("verification_code.html").render(code=code, domain=domain)
        return await self._send({
            "from": self.from_address,
            "to": [to_email],
            "subject": f"🔐 Seu código Klarim: {code}",
            "html": html,
        }, email_type="verification_code", domain=domain, source="scan_public",
            skip_blocklist=True)

    async def send_ownership_verification(self, to_email: str, domain: str,
                                          code: str) -> Dict[str, Any]:
        """Envia o código de verificação de PROPRIEDADE ao contact_email do site (KL-68).
        Transacional (via `seguranca@klarim.net`, o remetente normal) — o dono do site
        pediu para provar a posse. Ignora a blocklist, mas é registrado (KL-62)."""
        html = _env.get_template("ownership_verification.html").render(code=code, domain=domain)
        return await self._send({
            "from": self.from_address,
            "to": [to_email],
            "subject": f"Código de verificação — {domain}",
            "html": html,
        }, email_type="ownership_verification", domain=domain, source="ownership",
            skip_blocklist=True)

    # ----- KL-44 P6: ciclo de assinatura (transacional, plain text) -------- #

    _PLAN_DISPLAY = {"pro": ("Pro", "R$ 19/mês"), "agency": ("Agency", "R$ 49/mês")}

    async def send_upgrade_confirmed(self, to_email: str, plan: str) -> Dict[str, Any]:
        """Confirmação de pagamento + plano ativo (transacional, o usuário pagou)."""
        name, price = self._PLAN_DISPLAY.get(plan, (plan.capitalize(), ""))
        text = "\n".join([
            "Olá,", "",
            f"Seu pagamento foi confirmado e o plano {name} ({price}) está ativo.",
            "Aproveite o monitoramento avançado, o boletim e as vigílias do seu plano.", "",
            "Acesse seu painel:", f"{SITE_BASE}/dashboard", "",
            "Klarim · Segurança web para o Brasil", SITE_BASE])
        return await self._send({
            "from": self.from_address, "to": [to_email],
            "subject": f"✅ Plano {name} ativo — pagamento confirmado", "text": text,
        }, email_type="upgrade_confirmed", source="billing", skip_blocklist=True)

    async def send_trial_warning(self, to_email: str, days: int,
                                 ends_label: str = "") -> Dict[str, Any]:
        """Aviso de expiração de trial (7 dias / 1 dia antes). Transacional."""
        when = f" ({ends_label})" if ends_label else ""
        if days <= 1:
            subject = "Último dia do seu trial Klarim"
            body = [f"Seu período de teste expira amanhã{when}.", "",
                    "Faça upgrade agora para não perder o monitoramento avançado:",
                    f"{SITE_BASE}/dashboard?upgrade=pro"]
        else:
            subject = f"Seu trial Klarim expira em {days} dias"
            body = [f"Seu período de teste do plano Pro expira em {days} dias{when}.", "",
                    "Após a expiração, sua conta será rebaixada para o plano Gratuito:",
                    "  • Monitoramento de 1 site (em vez de 5)",
                    "  • Boletim mensal (em vez de semanal)",
                    "  • Sem vigílias avançadas", "",
                    "Para continuar com o Pro:", f"{SITE_BASE}/dashboard?upgrade=pro"]
        text = "\n".join(["Olá,", ""] + body + ["", "Klarim · Segurança web para o Brasil", SITE_BASE])
        return await self._send({
            "from": self.from_address, "to": [to_email], "subject": subject, "text": text,
        }, email_type="trial_warning", source="billing", skip_blocklist=True)

    async def send_trial_expired(self, to_email: str) -> Dict[str, Any]:
        """Aviso de trial expirado (após o downgrade silencioso para Free). Transacional."""
        text = "\n".join([
            "Olá,", "",
            "Seu período de teste do plano Pro expirou.", "",
            "Sua conta foi rebaixada para o plano Gratuito. Seus dados foram preservados,",
            "mas o monitoramento avançado foi desativado.", "",
            "Você pode fazer upgrade a qualquer momento:",
            f"{SITE_BASE}/dashboard?upgrade=pro", "",
            "Klarim · Segurança web para o Brasil", SITE_BASE])
        return await self._send({
            "from": self.from_address, "to": [to_email],
            "subject": "Seu trial expirou — conta rebaixada para Gratuito", "text": text,
        }, email_type="trial_expired", source="billing", skip_blocklist=True)

    async def send_signup_verification_code(self, to_email: str, code: str) -> Dict[str, Any]:
        """Envia o código de 6 dígitos para verificar o e-mail no cadastro de conta
        (KL-44 F-03b) — só quando o e-mail ainda NÃO foi verificado no scan (KL-25).
        Transacional (o usuário pediu) → ignora a blocklist, mas é registrado (KL-62)."""
        html = _env.get_template("verification_code.html").render(
            code=code, expires_label="15 minutos",
            purpose="Use-o para confirmar seu e-mail e criar sua conta Klarim.")
        return await self._send({
            "from": self.from_address,
            "to": [to_email],
            "subject": f"🔐 Seu código de cadastro Klarim: {code}",
            "html": html,
        }, email_type="signup_verification", source="account", skip_blocklist=True)

    async def send_password_reset_code(self, to_email: str, code: str) -> Dict[str, Any]:
        """Envia o código de 6 dígitos para redefinir a senha da conta (KL-51 f3)."""
        html = _env.get_template("password_reset_code.html").render(code=code)
        return await self._send({
            "from": self.from_address,
            "to": [to_email],
            "subject": f"🔑 Redefinição de senha Klarim: {code}",
            "html": html,
        }, email_type="password_reset", source="account", skip_blocklist=True)

    async def send_account_deleted(self, to_email: str) -> Dict[str, Any]:
        """Confirma ao usuário que a conta foi excluída (KL-57)."""
        html = _env.get_template("account_deleted.html").render()
        return await self._send({
            "from": self.from_address,
            "to": [to_email],
            "subject": "Sua conta Klarim foi excluída",
            "html": html,
        }, email_type="account_deleted", source="account", skip_blocklist=True)

    async def send_site_removed(self, to_email: str, domain: str) -> Dict[str, Any]:
        """Avisa o usuário que um site foi removido do monitoramento (KL-69, admin/
        limpeza). Transacional (`seguranca@klarim.net`), registrado no email_log."""
        html = _env.get_template("site_removed.html").render(domain=domain)
        return await self._send({
            "from": self.from_address, "to": [to_email],
            "subject": f"Atualização no seu monitoramento — {domain}", "html": html,
        }, email_type="site_removed", domain=domain, source="admin", skip_blocklist=True)

    async def send_account_deactivated(self, to_email: str) -> Dict[str, Any]:
        """Avisa que a conta foi desativada por um admin (KL-69)."""
        html = _env.get_template("account_deactivated.html").render()
        return await self._send({
            "from": self.from_address, "to": [to_email],
            "subject": "Sua conta Klarim foi desativada", "html": html,
        }, email_type="account_deactivated", source="admin", skip_blocklist=True)

    async def send_account_reactivated(self, to_email: str) -> Dict[str, Any]:
        """Avisa que a conta foi reativada por um admin (KL-69)."""
        html = _env.get_template("account_reactivated.html").render()
        return await self._send({
            "from": self.from_address, "to": [to_email],
            "subject": "Sua conta Klarim foi reativada", "html": html,
        }, email_type="account_reactivated", source="admin", skip_blocklist=True)

    async def send_bulletin_owner(self, to_email: str, domain: str, subject: str,
                                  text: str, target_id: Optional[int] = None) -> Dict[str, Any]:
        """Boletim de segurança ao DONO (KL-44 P3). Plain text, **proativo** (klarimscan.com),
        respeita a blocklist, registrado no email_log (`bulletin`)."""
        return await self._send({
            "from": self._proactive_from(), "to": [to_email],
            "subject": subject, "text": text,
        }, email_type="bulletin", target_id=target_id, domain=domain, source="bulletin_worker")

    async def send_bulletin_technician(self, to_email: str, domain: str, subject: str,
                                       text: str, target_id: Optional[int] = None) -> Dict[str, Any]:
        """Laudo técnico ao técnico vinculado (KL-44 P3). Plain text, **transacional**
        (seguranca@klarim.net), ignora blocklist mas registra (`bulletin_technician`)."""
        return await self._send({
            "from": self.from_address, "to": [to_email], "subject": subject, "text": text,
        }, email_type="bulletin_technician", target_id=target_id, domain=domain,
            source="bulletin_worker", skip_blocklist=True)

    async def send_technician_invite(self, to_email: str, domain: str, subject: str,
                                     text: str, target_id: Optional[int] = None) -> Dict[str, Any]:
        """Convite ao técnico (KL-44 P3). Plain text, transacional, registrado
        (`technician_invite`)."""
        return await self._send({
            "from": self.from_address, "to": [to_email], "subject": subject, "text": text,
        }, email_type="technician_invite", target_id=target_id, domain=domain,
            source="account", skip_blocklist=True)

    async def send_profile_view(self, to_email: str, domain: str, score: int,
                                semaphore: str, cta_url: str,
                                unsubscribe_link: Optional[str] = None,
                                target_id: Optional[int] = None) -> Dict[str, Any]:
        """Avisa o dono que alguém consultou o perfil público do site (KL-51 f4).
        Proativo → **respeita a blocklist** + registra (KL-62; era o vazamento nº 1).
        KL-44: enviado em **texto puro** (não HTML), como o alerta. `semaphore`/`cta_url`
        seguem na assinatura (chamadores inalterados) mas o corpo em texto não os usa —
        o link é montado do domínio (`/site/{domain}`)."""
        if unsubscribe_link is None:
            secret = os.environ.get("UNSUBSCRIBE_SECRET") or os.environ.get("JWT_SECRET") or ""
            if secret:
                unsubscribe_link = build_unsubscribe_link(to_email, secret)
        params = {
            "from": self._proactive_from(),  # PROATIVO → domínio de warmup (klarimscan.com)
            "to": [to_email],
            "subject": f"Alguém consultou a segurança do site {domain}",
            "text": build_profile_view_text(domain, score, unsubscribe_link),
        }
        hdrs = list_unsubscribe_headers(unsubscribe_link)   # RFC 8058 one-click (proativo)
        if hdrs:
            params["headers"] = hdrs
        return await self._send(params, email_type="profile_view", target_id=target_id,
                                domain=domain, source="profile_view")

    async def send_account_evolution(self, to_email: str, domain: str, prev_score: int,
                                     new_score: int, fixed: int, remaining: int,
                                     link: str) -> Dict[str, Any]:
        """E-mail de evolução do monitoramento mensal de uma conta (KL-51 f3)."""
        html = _env.get_template("account_evolution.html").render(
            domain=domain, prev_score=prev_score, new_score=new_score,
            delta=new_score - prev_score, fixed=fixed, remaining=remaining, link=link)
        return await self._send({
            "from": self.from_address,
            "to": [to_email],
            "subject": f"{domain} — seu score de segurança mudou",
            "html": html,
        }, email_type="account_evolution", domain=domain, source="cron")

    async def send_monitor_offer(self, to_email: str, domain: str,
                                 approve_url: str) -> Dict[str, Any]:
        """Oferta de monitoramento gratuito para um site que atingiu score 100 (KL-29)."""
        html = _env.get_template("monitor_offer.html").render(
            domain=domain, approve_url=approve_url, site_base=SITE_BASE)
        return await self._send({
            "from": self.from_address, "to": [to_email],
            "subject": f"{domain} — monitoramento de segurança gratuito",
            "html": html}, email_type="monitor_offer", domain=domain, source="rescan_worker")

    async def send_monitor_alert(self, to_email: str, domain: str, score: int,
                                 result_url: str, remove_url: str) -> Dict[str, Any]:
        """Alerta: o score do site monitorado caiu abaixo de 100 (KL-29)."""
        html = _env.get_template("monitor_alert.html").render(
            domain=domain, score=score, result_url=result_url,
            remove_url=remove_url, site_base=SITE_BASE)
        return await self._send({
            "from": self.from_address, "to": [to_email],
            "subject": f"{domain} — o score de segurança caiu para {score}/100",
            "html": html}, email_type="monitor_alert", domain=domain, source="rescan_worker")

    async def send_monitor_restored(self, to_email: str, domain: str,
                                    result_url: str, remove_url: str) -> Dict[str, Any]:
        """Restauração: o site voltou a 100/100 e ao selo (KL-29)."""
        html = _env.get_template("monitor_restored.html").render(
            domain=domain, result_url=result_url, remove_url=remove_url, site_base=SITE_BASE)
        return await self._send({
            "from": self.from_address, "to": [to_email],
            "subject": f"{domain} — voltou a 100/100 e ao selo Klarim",
            "html": html}, email_type="monitor_restored", domain=domain, source="rescan_worker")

    # ----- vigílias (KL-44 P2) --------------------------------------------- #

    _VIGILIA_TAG = {"ssl": "Certificado SSL", "domain": "Registro do domínio",
                    "score": "Score de segurança", "email": "Proteção de e-mail",
                    "reputation": "Reputação", "uptime": "Disponibilidade",
                    "changes": "Integridade do site", "phishing": "Domínios suspeitos"}
    _SEV_STYLE = {"critical": ("#F85149", "🔴"), "warning": ("#F0C000", "⚠️"),
                  "info": ("#58A6FF", "ℹ️")}
    # KL-44 P4: uptime/changes/phishing usam um template genérico (data-driven).
    _VIGILIA_GENERIC = {"uptime", "changes", "phishing"}

    async def send_vigilia_alert(self, *, to_email: str, tipo: str, domain: str,
                                 subject: str, title: str, message: str,
                                 action_text: Optional[str] = None,
                                 severity: str = "warning",
                                 data: Optional[dict] = None) -> Dict[str, Any]:
        """Alerta de uma vigília (KL-44 P2). Um template por `tipo`
        (`vigilia_<tipo>.html`). **Proativo** → respeita a blocklist (KL-24/62).
        Registrado no `email_log` com `email_type=vigilia_<tipo>`."""
        sev_color, sev_icon = self._SEV_STYLE.get(severity, self._SEV_STYLE["warning"])
        # `email` usa o template dedicado; uptime/changes/phishing (P4) usam o genérico;
        # o resto casa `vigilia_<tipo>.html`.
        if tipo == "email":
            template = "vigilia_email_security.html"
        elif tipo in self._VIGILIA_GENERIC:
            template = "vigilia_generic.html"
        else:
            template = f"vigilia_{tipo}.html"
        html = _env.get_template(template).render(
            domain=domain, title=title, message=message, action_text=action_text,
            severity=severity, sev_color=sev_color, sev_icon=sev_icon,
            tag_label=self._VIGILIA_TAG.get(tipo, "Vigília"), data=data or {},
            result_url=f"{SITE_BASE}/site/{domain}",
            dashboard_url=f"{SITE_BASE}/dashboard", site_base=SITE_BASE)
        return await self._send({
            "from": self.from_address, "to": [to_email], "subject": subject, "html": html,
        }, email_type=f"vigilia_{tipo}", domain=domain, source="vigilia_worker")

    async def send_recovery_link(self, to_email: str, recovery_url: str) -> Dict[str, Any]:
        """Envia o link temporário de recuperação de relatórios. Transacional → ignora
        a blocklist (o usuário pediu), mas é registrado (KL-62)."""
        sep = "&" if "?" in recovery_url else "?"
        recovery_url = f"{recovery_url}{sep}utm_source=klarim&utm_medium=email&utm_campaign=recuperacao"
        html = _env.get_template("recovery.html").render(recovery_url=recovery_url)
        return await self._send({
            "from": self.from_address,
            "to": [to_email],
            "subject": "🔑 Acesso aos seus relatórios Klarim",
            "html": html,
        }, email_type="recovery", source="recovery", skip_blocklist=True)

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
            {"from": self.from_address, "to": [to_email], "subject": "Teste — Klarim", "html": html},
            email_type="test", source="diagnostic", skip_blocklist=True,
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
        # Destinatário interno (scan@klarim.net) → ignora blocklist, mas registra (KL-62).
        return await self._send(params, email_type="contact", source="contact_form",
                                skip_blocklist=True)

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
