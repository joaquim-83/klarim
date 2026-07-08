"""Extração do melhor e-mail de contato a partir do site.

Regra de negócio: sem e-mail extraível, o alvo não vale o custo de scan.
"""

from __future__ import annotations

import re
from typing import List, Optional
from urllib.parse import urljoin

import httpx

from scanner.checks.base import fetch, base_url, registrable_domain, domain_of

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
_MAILTO_RE = re.compile(r"mailto:([^\"'?>\s]+)", re.IGNORECASE)
_META_EMAIL_RE = re.compile(
    r"""<meta[^>]+(?:property|name)\s*=\s*['"](?:og:email|contact:email|email)['"][^>]*content\s*=\s*['"]([^'"]+)['"]""",
    re.IGNORECASE,
)

# Prefixos/locais genéricos que não são contato do dono.
_JUNK_LOCAL = {
    "noreply", "no-reply", "donotreply", "do-not-reply",
    "postmaster", "webmaster", "hostmaster", "abuse", "mailer-daemon",
}
# Domínios de terceiros (plataformas/tracking) — nunca são o dono.
_JUNK_DOMAINS = (
    "duda.co", "dudamobile.com", "wordpress.com", "wordpress.org", "wix.com",
    "wixpress.com", "squarespace.com", "shopify.com", "sentry.io", "example.com",
    "example.org", "godaddy.com", "cloudflare.com", "google.com", "gstatic.com",
    "schema.org", "w3.org", "sentry-next.wixpress.com",
)

_CONTACT_PATHS = ["contato", "contact", "fale-conosco", "fale-conosco/"]

# Extensões de arquivo que aparecem em nomes que "parecem" e-mail (KL-19).
_INVALID_EXTENSIONS = (
    ".css", ".js", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp",
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".zip", ".rar",
    ".woff", ".woff2", ".ttf", ".eot", ".ico", ".mp4", ".mp3", ".json", ".xml",
)
# Placeholders de template (não são contatos reais) — evitam bounce/reputação.
_PLACEHOLDER_PREFIXES = (
    "seuemail@", "youremail@", "email@email", "nome@email", "name@email",
    "exemplo@", "example@", "test@test", "teste@teste", "info@example",
    "your@email", "seu@email", "user@example", "mail@mail", "email@exemplo",
    "contato@seusite", "contato@suaempresa", "email@suaempresa", "seunome@",
)
_PLACEHOLDER_DOMAINS = {
    "example.com", "example.com.br", "email.com", "email.com.br",
    "teste.com", "teste.com.br", "test.com", "seusite.com.br", "suaempresa.com.br",
    "dominio.com.br", "exemplo.com.br", "empresa.com.br",
}
_VALID_EMAIL_RE = re.compile(r"^[a-z0-9._%+\-]+@[a-z0-9.\-]+\.[a-z]{2,}$")


def _is_valid_email(email: str) -> bool:
    """Rejeita 'e-mails' que são nomes de arquivo, placeholders ou lixo (KL-19)."""
    email = (email or "").strip().lower()
    local, _, domain = email.partition("@")
    if not local or not domain:
        return False
    if len(local) < 2:                                   # ex.: "_@astro..."
        return False
    if domain.endswith(_INVALID_EXTENSIONS):             # ex.: "...dwg1vcjs.css"
        return False
    if any(local.endswith(ext.lstrip(".")) for ext in _INVALID_EXTENSIONS):
        return False
    if email.startswith(_PLACEHOLDER_PREFIXES):          # ex.: "seuemail@..."
        return False
    if domain in _PLACEHOLDER_DOMAINS:                   # ex.: "...@email.com.br"
        return False
    if not _VALID_EMAIL_RE.match(email):
        return False
    return True


def _is_junk(email: str) -> bool:
    email = email.lower()
    try:
        local, domain = email.split("@", 1)
    except ValueError:
        return True
    if local in _JUNK_LOCAL:
        return True
    if any(domain == d or domain.endswith("." + d) for d in _JUNK_DOMAINS):
        return True
    # e-mails de imagem/asset acidentais (ex.: com extensão no final)
    if domain.endswith((".png", ".jpg", ".gif", ".svg", ".webp")):
        return True
    return False


def _collect_emails(html: str) -> List[str]:
    found: List[str] = []
    for m in _MAILTO_RE.findall(html or ""):
        addr = m.split("?")[0].strip()
        if _EMAIL_RE.fullmatch(addr):
            found.append(addr)
    for m in _META_EMAIL_RE.findall(html or ""):
        if _EMAIL_RE.fullmatch(m.strip()):
            found.append(m.strip())
    found.extend(_EMAIL_RE.findall(html or ""))
    # dedup preservando ordem, tudo em lowercase
    seen, out = set(), []
    for e in found:
        e = e.lower()
        if e not in seen:
            seen.add(e)
            out.append(e)
    return out


def _best_email(emails: List[str], site_domain: str) -> Optional[str]:
    candidates = [e for e in emails if _is_valid_email(e) and not _is_junk(e)]
    if not candidates:
        return None
    # Prioriza e-mails no mesmo domínio registrável do site.
    same = [e for e in candidates if registrable_domain(e.split("@", 1)[1]) == site_domain]
    return (same or candidates)[0]


async def extract_email(html: str, url: str) -> Optional[str]:
    """Extrai o melhor e-mail de contato. Tenta a página e, se preciso, /contato."""
    site_domain = registrable_domain(domain_of(url))

    best = _best_email(_collect_emails(html), site_domain)
    if best:
        return best

    # Fallback: páginas de contato comuns.
    root = base_url(url) + "/"
    for path in _CONTACT_PATHS:
        try:
            resp = await fetch(urljoin(root, path), method="GET", follow_redirects=True)
        except (httpx.HTTPError, OSError):
            continue
        if resp.status_code == 200:
            best = _best_email(_collect_emails(resp.text), site_domain)
            if best:
                return best
    return None
