"""Extração do melhor e-mail de contato a partir do site.

Regra de negócio: sem e-mail extraível, o alvo não vale o custo de scan.
"""

from __future__ import annotations

import asyncio
import re
from functools import lru_cache
from typing import List, Optional
from urllib.parse import unquote, urljoin

import httpx

from scanner.checks.base import fetch, base_url, registrable_domain, domain_of


def _clean_email(raw: str) -> str:
    """Limpa um e-mail extraído do HTML antes de validar/usar.

    URL-decode (`%20`→espaço, `%40`→@) + remove espaços/tabs/quebras/nbsp +
    lowercase. Corrige o lixo tipo ``%20contato@x.com.br`` que passava pelo regex
    (o `%` é permitido no local-part) e envenenava o batch do Resend — 1 e-mail
    inválido faz o Batch API rejeitar TODOS os 50 (`422: Invalid 'to' field`).
    """
    email = unquote((raw or "").strip())
    for ch in (" ", "\t", "\n", "\r", "\xa0"):
        email = email.replace(ch, "")
    return email.lower()

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

# Páginas internas de contato (KL-50 camada 1): o e-mail costuma estar em /contato,
# /sobre, /quem-somos — não só na homepage. Tira alvos de 'sem_contato'.
_CONTACT_PATHS = ["contato", "contact", "sobre", "about",
                  "quem-somos", "sobre-nos", "fale-conosco", "atendimento"]

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


# --- validação de MX (KL-24) ----------------------------------------------- #
# Bounce rate alto (10,67%) porque extraímos e-mails sintaticamente válidos cujo
# domínio não recebe e-mail (staging, CDN, domínio parqueado). Antes de aceitar,
# checamos se o domínio tem registro MX. Tri-estado para não descartar em falha
# transitória de DNS: "ok" (tem MX) | "no_mx" (definitivamente não recebe) |
# "unknown" (timeout/sem lib — fail-open, não rejeita).


def _mx_status(domain: str) -> str:
    """Estado do MX do domínio: 'ok' | 'no_mx' | 'unknown'. Nunca levanta."""
    try:
        import dns.resolver  # dnspython
    except ImportError:
        return "unknown"  # sem a lib não dá pra checar — não bloqueia (fail-open)
    try:
        answers = dns.resolver.resolve(domain, "MX", lifetime=5)
        return "ok" if len(answers) > 0 else "no_mx"
    except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer):
        return "no_mx"  # domínio não existe / não tem MX -> e-mail bounca
    except Exception:  # noqa: BLE001 - timeout, NoNameservers, etc. -> incerto
        return "unknown"


@lru_cache(maxsize=10000)
def _mx_status_cached(domain: str) -> str:
    """Cache em memória por domínio (evita DNS repetido para o mesmo domínio)."""
    return _mx_status(domain)


def email_mx_status(email: str) -> str:
    domain = (email or "").rsplit("@", 1)[-1].strip().lower()
    if not domain:
        return "no_mx"
    return _mx_status_cached(domain)


def email_has_mx(email: str) -> bool:
    """True se o domínio pode receber e-mail. Só rejeita MX definitivamente ausente
    ('no_mx'); 'unknown' (timeout/sem lib) passa para não descartar por engano."""
    return email_mx_status(email) != "no_mx"


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
    # limpa (URL-decode + tira espaços/lixo) e deduplica preservando a ordem.
    seen, out = set(), []
    for e in found:
        e = _clean_email(e)
        if e and e not in seen:
            seen.add(e)
            out.append(e)
    return out


def _ranked_emails(emails: List[str], site_domain: str) -> List[str]:
    """Candidatos válidos (sintaxe + não-junk), com os do mesmo domínio na frente."""
    candidates = [e for e in emails if _is_valid_email(e) and not _is_junk(e)]
    same = [e for e in candidates if registrable_domain(e.split("@", 1)[1]) == site_domain]
    rest = [e for e in candidates if e not in same]
    return same + rest


def _best_email(emails: List[str], site_domain: str) -> Optional[str]:
    """Melhor candidato **sintático** (sem checar MX — quem faz isso é o extract)."""
    ranked = _ranked_emails(emails, site_domain)
    return ranked[0] if ranked else None


async def _pick_with_mx(candidates: List[str], validate_mx: bool) -> Optional[str]:
    """Primeiro candidato cujo domínio tem MX (KL-24). DNS roda fora do event loop."""
    for e in candidates:
        if not validate_mx:
            return e
        if await asyncio.to_thread(email_has_mx, e):
            return e
        print(f"[contact] rejeitado {e} — domínio sem MX", flush=True)
    return None


async def extract_email(html: str, url: str, validate_mx: bool = True) -> Optional[str]:
    """Extrai o melhor e-mail de contato. Tenta a página e, se preciso, /contato.

    Com ``validate_mx`` (padrão), só aceita e-mail cujo domínio tem registro MX —
    corta a maior fonte de bounce (domínios que não recebem e-mail, KL-24).
    """
    site_domain = registrable_domain(domain_of(url))

    best = await _pick_with_mx(_ranked_emails(_collect_emails(html), site_domain), validate_mx)
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
            best = await _pick_with_mx(
                _ranked_emails(_collect_emails(resp.text), site_domain), validate_mx)
            if best:
                return best
    return None
