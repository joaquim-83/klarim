"""KL-91 — Cold outreach de alertas: templates de TEXTO PURO (sem links) + rotação
de remetentes entre subdomínios verificados no Resend.

Motivação: os alertas caíam no spam (linguagem de urgência + links trackáveis +
envio por um único domínio recém-criado). A correção:

  1. 3 variantes de **texto puro**, SEM links clicáveis, sem emojis, sem urgência,
     sem CTA. Opt-out por RESPOSTA ("responda com remover"). O `klarim.net` é
     mencionado como TEXTO, nunca como link.
  2. **Rotação round-robin** entre `alertas.klarim.net` e `aviso.klarim.net` (ambos
     verificados no Resend — DKIM/SPF/DMARC). O domínio principal `klarim.net` fica
     EXCLUSIVO do transacional (isolamento de reputação, ver `docs/ARCHITECTURE.md`).

Tudo aqui é **puro/testável** (sem I/O): o `alert_worker` chama `load_senders`,
`pick_sender`, `flag_high_bounce`, `choose_variant` e `build_cold_email`. O envio em
si é do `KlarimMailer.send_cold_alert`.
"""

from __future__ import annotations

import os
import random
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

# Subdomínios verificados no Resend (Cloudflare, sa-east-1, DKIM verified). O domínio
# principal (klarim.net) NÃO entra aqui — é do transacional. Sobrescrevível via env
# `ALERT_SENDER_EMAILS` (CSV) para adicionar/trocar remetentes sem redeploy de código.
DEFAULT_SENDER_EMAILS: Tuple[str, ...] = (
    "scan@alertas.klarim.net",
    "scan@aviso.klarim.net",
)

# Nome de exibição do remetente (De: "Klarim <scan@…>"). Reduz cara de spam vs. só o
# e-mail cru, mantendo o tom institucional da assinatura.
DEFAULT_SENDER_NAME = "Klarim"

# Caixa que recebe as respostas de opt-out ("remover"). Inbox Hostinger (scan@klarim.net),
# lida no painel Inbox — é também o Reply-To padrão de todo e-mail (KL-67).
OPT_OUT_MAILBOX = "scan@klarim.net"

# Circuit breaker: pausa um remetente cujo bounce rate passar disto (amostra mínima).
DEFAULT_MAX_BOUNCE_RATE = 5.0
DEFAULT_BOUNCE_MIN_SAMPLE = 20


@dataclass
class Sender:
    """Um remetente cold (subdomínio dedicado). `status` é 'active' ou 'paused'
    (circuit breaker do ciclo)."""
    name: str          # rótulo curto derivado do subdomínio (ex.: "alertas")
    email: str         # "scan@alertas.klarim.net"
    from_domain: str   # "alertas.klarim.net" — casa com `email_log.from_domain`
    status: str = "active"

    @property
    def from_address(self) -> str:
        """Campo `from` do Resend: 'Klarim <scan@alertas.klarim.net>'."""
        name = (os.environ.get("ALERT_SENDER_NAME") or DEFAULT_SENDER_NAME).strip()
        return f"{name} <{self.email}>" if name else self.email


def _domain_of(email: str) -> str:
    return (email or "").rsplit("@", 1)[-1].strip().lower()


def load_senders(env: Optional[Dict[str, str]] = None) -> List[Sender]:
    """Constrói a lista de remetentes cold a partir do env (fail-safe → defaults).

    `ALERT_SENDER_EMAILS` é um CSV de e-mails; vazio/ausente → `DEFAULT_SENDER_EMAILS`.
    Nunca inclui `klarim.net` cru (guard de isolamento): um e-mail cujo domínio seja
    exatamente `klarim.net` é descartado (o transacional não rotaciona)."""
    src = env if env is not None else os.environ
    raw = (src.get("ALERT_SENDER_EMAILS") or "").strip()
    emails = [e.strip() for e in raw.split(",") if e.strip()] or list(DEFAULT_SENDER_EMAILS)
    senders: List[Sender] = []
    seen = set()
    for e in emails:
        dom = _domain_of(e)
        if not dom or "@" not in e or dom == "klarim.net" or dom in seen:
            continue  # isolamento: klarim.net é só transacional; sem duplicatas
        seen.add(dom)
        senders.append(Sender(name=dom.split(".")[0], email=e, from_domain=dom))
    return senders


def flag_high_bounce(senders: Sequence[Sender],
                     by_domain: Dict[str, Dict[str, float]],
                     max_rate: float = DEFAULT_MAX_BOUNCE_RATE,
                     min_sample: int = DEFAULT_BOUNCE_MIN_SAMPLE) -> List[Tuple[str, float]]:
    """Circuit breaker (KL-91 §6): marca `status='paused'` os remetentes cujo bounce
    rate passou de `max_rate` (com amostra ≥ `min_sample`). **Muta** os senders e
    devolve [(from_domain, rate)] dos pausados (para o worker logar CRITICAL). O outro
    remetente continua ativo — a rotação simplesmente o ignora."""
    paused: List[Tuple[str, float]] = []
    for s in senders:
        d = by_domain.get(s.from_domain) or {}
        total = int(d.get("total") or 0)
        bounced = int(d.get("bounced") or 0)
        if total < min_sample:
            continue
        rate = 100.0 * bounced / total if total else 0.0
        if rate > max_rate:
            s.status = "paused"
            paused.append((s.from_domain, round(rate, 1)))
    return paused


def pick_sender(senders: Sequence[Sender], counts: Dict[str, int],
                daily_limit: int) -> Optional[Sender]:
    """Round-robin (KL-91 §2): entre os remetentes ATIVOS que ainda não bateram o
    limite diário, escolhe o de MENOR contagem hoje (empate → nome do domínio, estável).
    None quando todos atingiram o limite ou estão pausados → o worker para o ciclo."""
    available = [s for s in senders
                 if s.status == "active" and counts.get(s.from_domain, 0) < daily_limit]
    if not available:
        return None
    return min(available, key=lambda s: (counts.get(s.from_domain, 0), s.from_domain))


def choose_variant(has_sector_data: bool, rng: Optional[random.Random] = None) -> int:
    """Escolhe a variante do template (KL-91 §2). Com dados de setor → 1/2/3; sem →
    1 ou 3 (a 2 depende de setor + média). `rng` injeta determinismo nos testes."""
    r = rng or random
    return r.choice([1, 2, 3]) if has_sector_data else r.choice([1, 3])


# --------------------------------------------------------------------------- #
# Templates de TEXTO PURO (PT-BR, com acentuação correta — texto sem acento parece
# MAIS spam/scam, não menos). SEM links, SEM emoji, SEM urgência, SEM CTA. O opt-out
# é por resposta. `klarim.net` aparece só como texto.
# --------------------------------------------------------------------------- #

# KL-100 — referência de transparência (texto, não link clicável) em todos os templates cold.
_METHODOLOGY_LINE = "Saiba mais sobre nossa metodologia: klarim.net/metodologia"
_SIGNATURE_FULL = (
    "--\n"
    "Klarim - Segurança web para o Brasil\n"
    "klarim.net\n"
    f"{_METHODOLOGY_LINE}\n"
    "Scanner 100% passivo."
)
_SIGNATURE_SHORT = f"--\nKlarim\nklarim.net\n{_METHODOLOGY_LINE}"

_OPT_OUT_REPLY = 'Se não deseja receber este tipo de comunicação, basta\nresponder este e-mail com "remover".'
_OPT_OUT_REPLY_SHORT = 'Para não receber mais, responda com "remover".'
_OPT_OUT_REPLY_ALT = 'Se preferir não receber mais, responda "remover".'


def _variant1(domain: str, score: int) -> Tuple[str, str]:
    subject = f"{domain} - análise de segurança disponível"
    body = (
        "Olá,\n\n"
        f"O site {domain} foi incluído na análise pública de\n"
        "segurança web da Klarim.\n\n"
        f"Resultado: score {score} de 100.\n\n"
        "A análise verifica 48 itens de segurança usando apenas\n"
        "informações que o site já expõe publicamente. Nenhum dado\n"
        "privado é acessado ou coletado.\n\n"
        "O resultado completo está disponível para consulta em\n"
        "klarim.net.\n\n"
        f"{_OPT_OUT_REPLY}\n\n"
        f"{_SIGNATURE_FULL}"
    )
    return subject, body


def _variant2(domain: str, score: int, sector_label: str, sector_avg: int) -> Tuple[str, str]:
    subject = f"Segurança web de {domain}"
    body = (
        "Olá,\n\n"
        "A Klarim realiza análises públicas de segurança web de\n"
        f"sites brasileiros. O site {domain}, do setor de\n"
        f"{sector_label}, foi analisado.\n\n"
        f"Score obtido: {score} de 100.\n"
        f"Média do setor {sector_label}: {sector_avg} de 100.\n\n"
        "A análise é gratuita e automática. Verificamos apenas o\n"
        "que o site expõe publicamente - cabeçalhos HTTP,\n"
        "certificados SSL, registros DNS e configurações visíveis.\n\n"
        "O resultado pode ser consultado em klarim.net pesquisando\n"
        "pelo domínio.\n\n"
        f"{_OPT_OUT_REPLY_SHORT}\n\n"
        f"{_SIGNATURE_SHORT}"
    )
    return subject, body


def _variant3(domain: str, score: int) -> Tuple[str, str]:
    subject = f"{domain} e a segurança web"
    body = (
        "Olá,\n\n"
        "Você sabia que a segurança de um site pode ser avaliada\n"
        "publicamente, sem acessar áreas restritas?\n\n"
        f"A Klarim analisou o site {domain} e o resultado\n"
        f"está disponível para consulta. O score obtido foi {score}\n"
        "de 100, com base em 48 verificações de segurança.\n\n"
        "Essas verificações incluem certificado SSL, cabeçalhos\n"
        "de proteção, configurações de e-mail (SPF, DKIM, DMARC)\n"
        "e outros itens que qualquer pessoa pode consultar.\n\n"
        "O resultado completo está em klarim.net.\n\n"
        f"{_OPT_OUT_REPLY_ALT}\n\n"
        f"{_SIGNATURE_FULL}"
    )
    return subject, body


def build_cold_email(variant: int, *, domain: str, score: int,
                     sector_label: str = "", sector_avg: Optional[int] = None
                     ) -> Tuple[str, str]:
    """Renderiza (subject, text) da variante escolhida. A variante 2 exige
    `sector_label` + `sector_avg`; se faltarem, cai para a 1 (defensivo — nunca
    imprime 'None' no corpo)."""
    domain = (domain or "").strip()
    score = int(score if score is not None else 0)
    if variant == 2 and sector_label and sector_avg is not None:
        return _variant2(domain, score, sector_label.strip(), int(sector_avg))
    if variant == 3:
        return _variant3(domain, score)
    return _variant1(domain, score)


def list_unsubscribe_reply_header(mailbox: str = OPT_OUT_MAILBOX) -> Dict[str, str]:
    """Header `List-Unsubscribe` como **mailto** (opt-out por resposta, KL-91 §4).

    Coerente com o corpo ("responda com remover") e com o Reply-To (`scan@klarim.net`):
    o botão nativo "Cancelar inscrição" do Gmail/Outlook abre um e-mail pré-preenchido.
    NÃO emitimos `List-Unsubscribe-Post: One-Click` — o One-Click do RFC 8058 EXIGE uma
    URL https; combiná-lo com mailto é malformado e PIORA a entrega (o oposto do objetivo
    deste card). Sem link no corpo, o opt-out é 100% por resposta."""
    return {"List-Unsubscribe": f"<mailto:{mailbox}?subject=remover>"}
