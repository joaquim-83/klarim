"""KL-92 — Classificação de bot **server-side** (fonte de verdade das métricas de visitante).

O tracker.js (client-side) infla visitantes ~5x porque pre-fetches de e-mail executam
JavaScript no browser do bot. Qualquer defesa client-side depende de código que roda no
browser do bot — insuficiente. A verdade é do **servidor**, que vê o IP real (via
``CF-Connecting-IP``) e classifica bot/humano sem depender do client.

Este módulo é PURO e testável: nenhuma I/O, nenhuma query. Recebe os sinais já extraídos
(IP, User-Agent, país, endpoint, contagem de requests na última hora, user_id) e devolve
``(is_bot, bot_reason)``. Ordem de prioridade das regras:

    IP próprio  →  usuário autenticado  →  datacenter  →  crawler  →  rate  →  pré-fetch

`is_datacenter_ip` usa uma lista ESTÁTICA de CIDRs dos principais provedores de nuvem
(sem lookup externo — atualizada periodicamente). Nosso próprio IP (self-scan/healthcheck)
é excluído explicitamente. Ver KL-92.
"""

from __future__ import annotations

import ipaddress
import os
from typing import Optional, Tuple

# --------------------------------------------------------------------------- #
# IP próprio — NUNCA é bot (self-scan, healthcheck, cron na VM). Vem do IP estático
# de produção (KL-77) e pode ser estendido por env (KLARIM_OWN_IPS, vírgula-separado).
# --------------------------------------------------------------------------- #
_STATIC_OWN_IPS = {"34.135.194.208", "35.238.72.10", "127.0.0.1", "::1"}


def _own_ips() -> set:
    extra = os.environ.get("KLARIM_OWN_IPS", "")
    ours = set(_STATIC_OWN_IPS)
    for ip in extra.split(","):
        ip = ip.strip()
        if ip:
            ours.add(ip)
    return ours


# --------------------------------------------------------------------------- #
# Ranges de datacenter dos principais provedores de nuvem (estático, sem lookup).
# ~30 redes → o loop de verificação é O(30), trivial. Se crescer para 500+, migrar
# para IntervalTree/sorted-list. Cada CIDR é pré-compilado 1x no import.
# --------------------------------------------------------------------------- #
_DATACENTER_CIDRS = (
    # AWS (principais /8 públicos)
    "3.0.0.0/8", "13.0.0.0/8", "18.0.0.0/8", "34.0.0.0/8", "35.0.0.0/8",
    "44.0.0.0/8", "52.0.0.0/8", "54.0.0.0/8",
    # GCP
    "34.64.0.0/10", "35.184.0.0/13",
    # Azure
    "20.0.0.0/8", "40.0.0.0/8",
    # DigitalOcean
    "64.225.0.0/16", "68.183.0.0/16", "134.209.0.0/16", "137.184.0.0/16",
    "138.197.0.0/16", "142.93.0.0/16", "143.198.0.0/16", "157.245.0.0/16",
    "159.65.0.0/16", "159.89.0.0/16", "161.35.0.0/16", "164.90.0.0/16",
    "165.227.0.0/16", "167.172.0.0/16", "174.138.0.0/16", "178.128.0.0/16",
    "178.62.0.0/16",
    # Hetzner
    "65.108.0.0/16", "65.109.0.0/16", "135.181.0.0/16", "95.216.0.0/16",
)

# Pré-compila (dedup implícito de CIDRs iguais como 52.0.0.0/8) uma única vez.
_DATACENTER_NETWORKS = [ipaddress.ip_network(c) for c in dict.fromkeys(_DATACENTER_CIDRS)]


# --------------------------------------------------------------------------- #
# User-Agents de crawler/cliente automatizado declarado. Comparação case-insensitive
# por substring — barata e suficiente (crawlers se identificam honestamente).
# --------------------------------------------------------------------------- #
_CRAWLER_PATTERNS = (
    "googlebot", "bingbot", "yandexbot", "baiduspider", "duckduckbot",
    "slurp", "ia_archiver", "facebookexternalhit", "twitterbot",
    "linkedinbot", "whatsapp", "telegrambot", "discordbot",
    "applebot", "semrushbot", "ahrefsbot", "mj12bot", "dotbot",
    "petalbot", "bytespider", "sogou", "seznambot",
    "python-requests", "httpx", "curl/", "wget/", "go-http-client",
    "java/", "php/", "ruby/", "libwww", "headlesschrome", "phantomjs",
)


# --------------------------------------------------------------------------- #
# Ações HUMANAS: uma vez que um IP as executa, ele é confirmado humano (retroatividade).
# Endpoint SEM o prefixo /api (o Nginx faz `rewrite ^/api/(.*)$ /$1`). Ver KL-92.
# --------------------------------------------------------------------------- #
HUMAN_ACTIONS = frozenset({
    ("GET", "/scan/result"),
    ("POST", "/scan/result"),
    ("GET", "/scan/summary"),
    ("POST", "/scan/summary"),
    ("POST", "/account/signup"),
    ("POST", "/account/signup-from-alert"),
    ("POST", "/account/login"),
    ("GET", "/report/pdf"),
    ("POST", "/events"),
})

_RATE_THRESHOLD = 50  # >50 requests/h do mesmo IP sem conta → bot


def is_datacenter_ip(ip: str) -> bool:
    """True se o IP pertence a um range de datacenter conhecido. Fail-open: IP inválido
    (ou IPv6 fora dos ranges) → False (não classifica como bot por engano)."""
    if not ip:
        return False
    try:
        addr = ipaddress.ip_address(ip.strip())
    except ValueError:
        return False
    return any(addr in net for net in _DATACENTER_NETWORKS if addr.version == net.version)


def is_crawler_ua(user_agent: Optional[str]) -> bool:
    """True se o User-Agent contém um padrão de crawler/cliente automatizado declarado.
    UA vazio NÃO é automaticamente crawler (navegadores legítimos raramente, mas o
    critério é declaração explícita — UA ausente cai nas outras regras)."""
    if not user_agent:
        return False
    ua = user_agent.lower()
    return any(p in ua for p in _CRAWLER_PATTERNS)


def is_human_action(method: str, endpoint: str) -> bool:
    """True se (método, endpoint) é uma ação que só um humano executa (scan, signup,
    login, download de PDF, evento de interação). Base da retroatividade (KL-92)."""
    return (str(method or "").upper(), str(endpoint or "")) in HUMAN_ACTIONS


def classify_bot(ip: str, user_agent: Optional[str], country: Optional[str],
                 endpoint: str, request_count_last_hour: int = 0,
                 user_id: Optional[int] = None,
                 has_other_requests: bool = True) -> Tuple[bool, Optional[str]]:
    """Classifica um request como bot/humano. Função PURA (sem I/O).

    Retorna ``(is_bot, bot_reason)`` — ``bot_reason`` ∈ {``datacenter_ip``, ``crawler_ua``,
    ``high_rate``, ``prefetch_pattern``} ou ``None`` quando humano.

    Ordem (a 1ª regra que casa vence):
      1. **IP próprio** (self-scan/healthcheck/cron) → humano.
      2. **Usuário autenticado** (``user_id`` presente) → humano por definição (logou com
         senha); evita falso-positivo de dev/cliente atrás de VPN ou nuvem.
      3. **Datacenter** (nuvem) → ``datacenter_ip``.
      4. **Crawler declarado** no User-Agent → ``crawler_ua``.
      5. **Rate anormal** (>50 req/h) sem conta → ``high_rate``.
      6. **Padrão de pré-fetch** (EUA + ``/site/*`` sem sequência de navegação) →
         ``prefetch_pattern``.

    ``request_count_last_hour`` vem de um contador Redis (``access_rate:{ip}``, TTL 1h);
    ``has_other_requests`` indica se o IP já apareceu antes (contador > 1). A retroatividade
    (``is_human_action``) corrige, no banco, IPs de datacenter que fizeram ação humana."""
    ip = (ip or "").strip()
    if not ip or ip == "unknown":
        return False, None  # sem IP confiável → não acusa (fail-open)

    if ip in _own_ips():
        return False, None

    if user_id is not None:
        return False, None

    if is_datacenter_ip(ip):
        return True, "datacenter_ip"

    if is_crawler_ua(user_agent):
        return True, "crawler_ua"

    if request_count_last_hour > _RATE_THRESHOLD and user_id is None:
        return True, "high_rate"

    ep = str(endpoint or "")
    if (country or "").upper() == "US" and ep.startswith("/site/") and not has_other_requests:
        return True, "prefetch_pattern"

    return False, None
