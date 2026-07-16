"""Domínios públicos/institucionais que ninguém deveria MONITORAR/REIVINDICAR (KL-68).

Regra: o **scan** é livre (motor de aquisição, mostra que a plataforma funciona). O
**monitoramento** e a **reivindicação** são bloqueados para domínios públicos — senão
usuários acabam monitorando `gmail.com`/`google.com`, poluindo os dados e desperdiçando
vigílias. Comportamento **suave/educativo**, não punitivo (a conta é criada normalmente;
só o vínculo do site é recusado, com mensagem que explica o porquê).

Puro (sem I/O) → testável offline e reutilizável no backend e (via API) no frontend.
"""

from __future__ import annotations

from typing import Optional, Tuple

# Domínios que NINGUÉM deveria monitorar na Klarim (exato ou como zona-pai de subdomínio).
BLOCKED_DOMAINS = {
    # Big Tech / Global
    "google.com", "gmail.com", "youtube.com", "android.com",
    "apple.com", "icloud.com", "microsoft.com", "outlook.com",
    "live.com", "bing.com", "office.com", "linkedin.com",
    "facebook.com", "instagram.com", "whatsapp.com", "meta.com",
    "twitter.com", "x.com", "amazon.com", "aws.amazon.com",
    "netflix.com", "spotify.com", "tiktok.com", "telegram.org",
    "yahoo.com", "wikipedia.org", "reddit.com", "pinterest.com",
    "github.com", "stackoverflow.com", "openai.com", "anthropic.com",
    # Email providers
    "hotmail.com", "protonmail.com", "zoho.com", "mail.com",
    "aol.com", "yandex.com", "gmx.com",
    # Cloud / Hosting / CDN
    "cloudflare.com", "vercel.com", "netlify.com", "heroku.com",
    "digitalocean.com", "godaddy.com", "namecheap.com",
    "wordpress.com", "wix.com", "squarespace.com", "shopify.com",
    # Desenvolvimento / Linguagens
    "python.org", "nodejs.org", "npmjs.com", "pypi.org",
    "docker.com", "kubernetes.io", "rust-lang.org",
    # Brasil — Grandes portais
    "globo.com", "g1.com.br", "uol.com.br", "terra.com.br",
    "ig.com.br", "r7.com", "folha.uol.com.br", "estadao.com.br",
    # Brasil — E-commerce
    "mercadolivre.com.br", "magazineluiza.com.br", "magalu.com.br",
    "americanas.com.br", "casasbahia.com.br", "amazon.com.br",
    "shopee.com.br", "aliexpress.com",
    # Brasil — Bancos
    "itau.com.br", "bradesco.com.br", "bb.com.br", "santander.com.br",
    "nubank.com.br", "inter.co", "c6bank.com.br", "caixa.gov.br",
    # Brasil — Serviços
    "ifood.com.br", "rappi.com.br", "99app.com", "uber.com",
    "olx.com.br", "webmotors.com.br",
}

# Sufixos institucionais bloqueados (governo, educação, militar, judiciário…).
BLOCKED_PATTERNS = [
    ".gov.br", ".edu.br", ".mil.br", ".jus.br", ".leg.br", ".mp.br",
    ".gov.com", ".edu", ".gov", ".mil",
]

_MESSAGES = {
    "public_domain": "Este é um domínio público conhecido. O monitoramento da Klarim é "
                     "para o site da sua empresa.",
    "public_subdomain": "Este domínio pertence a uma grande empresa. O monitoramento da "
                        "Klarim é para o site da sua empresa.",
    "institutional_domain": "Domínios governamentais e institucionais não podem ser "
                            "monitorados por terceiros.",
}


def _normalize(domain: str) -> str:
    """host de uma URL/domínio, lowercase, sem `www.` e sem barra/porta."""
    d = (domain or "").strip().lower()
    if "://" in d:
        from urllib.parse import urlparse
        d = urlparse(d).hostname or d
    d = d.split("/")[0].split(":")[0].strip()
    if d.startswith("www."):
        d = d[4:]
    return d


def is_blocked_domain(domain: str) -> Tuple[bool, Optional[str]]:
    """(True, motivo) se o domínio NÃO pode ser monitorado/reivindicado; (False, None) se OK.

    Motivos: ``public_domain`` (exato), ``public_subdomain`` (subdomínio de bloqueado),
    ``institutional_domain`` (sufixo .gov/.edu/…).
    """
    d = _normalize(domain)
    if not d:
        return False, None
    if d in BLOCKED_DOMAINS:
        return True, "public_domain"
    parts = d.split(".")
    for i in range(1, len(parts)):
        if ".".join(parts[i:]) in BLOCKED_DOMAINS:
            return True, "public_subdomain"
    for pattern in BLOCKED_PATTERNS:
        if d.endswith(pattern):
            return True, "institutional_domain"
    return False, None


def get_block_message(reason: Optional[str]) -> str:
    """Mensagem amigável/educativa para o frontend."""
    return _MESSAGES.get(reason or "", "Este domínio não pode ser monitorado.")
