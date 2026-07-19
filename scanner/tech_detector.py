"""Detecção de tech stack a partir do response bruto de scan (KL-75, Prompt 1).

O scanner já coleta headers HTTP, HTML da homepage, DNS (MX/NS/TXT) e o certificado
SSL em cada scan — e descartava 90% depois de extrair os 48 checks. Este módulo
**parseia o que já está em memória** (nenhum request HTTP extra) e extrai inteligência
tecnográfica: servidor/backend, analytics, marketing, pagamento, chat, e-commerce, CMS,
provedor de e-mail/DNS, domínios relacionados (SSL SAN) e status do site.

`detect_tech_stack` é uma função **pura** (sem DB, sem I/O): recebe dados, devolve um
dict. Totalmente testável offline. O scan worker chama após o enrich e grava o resultado
(`scanner/main.py`); o backfill reprocessa responses arquivados no GCS (KL-77 Fase 2).

⚠️ Dados técnicos (headers HTTP, certificados) são **públicos**; o valor está na
**agregação**. O stack detalhado é reservado à API autenticada/admin — o público vê só
badges booleanos (`GET /public/tech-summary/{domain}`).
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional

# --------------------------------------------------------------------------- #
# Grupo 1 — Headers HTTP
# --------------------------------------------------------------------------- #
# Cada entrada de um header: (regex, name, category, subcategory). A versão, quando
# houver, sai do grupo 1 do regex (ex.: ``nginx/1.24`` → ``1.24``). O regex roda sobre
# o VALOR daquele header (case-insensitive).

HEADER_PATTERNS: Dict[str, List[tuple]] = {
    "server": [
        (r"nginx(?:/(\S+))?", "nginx", "hosting", "webserver"),
        (r"apache(?:/(\S+))?", "apache", "hosting", "webserver"),
        (r"litespeed", "litespeed", "hosting", "webserver"),
        (r"microsoft-iis(?:/(\S+))?", "iis", "hosting", "webserver"),
        (r"cloudflare", "cloudflare", "hosting", "webserver"),
        (r"openresty", "openresty", "hosting", "webserver"),
    ],
    "x-powered-by": [
        (r"php(?:/(\S+))?", "php", "hosting", "backend"),
        (r"asp\.?net", "asp.net", "hosting", "backend"),
        (r"express", "express", "hosting", "backend"),
        (r"next\.?js", "nextjs", "hosting", "framework"),
    ],
    # CDN — a mera presença do header já identifica o provedor.
    "cf-ray": [(r".", "cloudflare_cdn", "cdn", None)],
    "x-served-by": [(r"cache-", "fastly", "cdn", None)],
    "x-amz-cf-id": [(r".", "aws_cloudfront", "cdn", None)],
    # Fingerprint de plataforma.
    "x-shopify-stage": [(r".", "shopify", "ecommerce", "platform")],
    "x-wix-request-id": [(r".", "wix", "cms", "platform")],
}

# Cookies (header ``set-cookie``): a presença do nome do cookie denuncia a stack.
COOKIE_PATTERNS: List[tuple] = [
    (r"PHPSESSID", "php", "hosting", "backend"),
    (r"_shopify_s", "shopify", "ecommerce", "platform"),
    (r"wp_settings", "wordpress", "cms", "platform"),
    (r"laravel_session", "laravel", "hosting", "framework"),
    (r"connect\.sid", "express", "hosting", "backend"),
    (r"JSESSIONID", "java", "hosting", "backend"),
    (r"ASP\.NET_SessionId", "asp.net", "hosting", "backend"),
]

# --------------------------------------------------------------------------- #
# Grupo 2 — Scripts / marcadores no HTML
# --------------------------------------------------------------------------- #
# Cada pattern: (regex, name, category, subcategory, version_group). ``version_group``
# não-None → a versão é o grupo 1 do regex (ex.: GA4 ``G-XXXX``).

SCRIPT_PATTERNS: List[tuple] = [
    # Analytics
    (r"gtag/js\?id=(G-\w+)", "google_analytics_4", "analytics", None, r"\1"),
    (r"gtag/js\?id=(UA-[\w-]+)", "google_analytics_ua", "analytics", None, r"\1"),
    (r"analytics\.js|ga\.js", "google_analytics", "analytics", None, None),
    (r"plausible\.io", "plausible", "analytics", None, None),
    (r"umami\.(is|js)", "umami", "analytics", None, None),
    (r"matomo\.(js|php)", "matomo", "analytics", None, None),
    (r"clarity\.ms", "microsoft_clarity", "analytics", "heatmap", None),
    (r"hotjar\.com|static\.hotjar", "hotjar", "analytics", "heatmap", None),
    # Marketing
    (r"fbevents\.js|facebook\.net[^\"']*fbevents", "meta_pixel", "marketing", None, None),
    (r"googleads\.g\.doubleclick|adsbygoogle", "google_ads", "marketing", None, None),
    (r"cdn\.rdstation\.com", "rd_station", "marketing", "crm", None),
    (r"js\.hs-scripts\.com|js\.hsforms\.net", "hubspot", "marketing", "crm", None),
    (r"mailchimp\.com|chimpstatic\.com", "mailchimp", "email_marketing", None, None),
    (r"convertkit\.com", "convertkit", "email_marketing", None, None),
    # Pagamento
    (r"sdk\.mercadopago\.com", "mercado_pago", "pagamento", None, None),
    (r"stc\.pagseguro\.uol", "pagseguro", "pagamento", None, None),
    (r"js\.stripe\.com", "stripe", "pagamento", None, None),
    (r"paypal\.com/sdk", "paypal", "pagamento", None, None),
    (r"pagar\.me|pagarme", "pagarme", "pagamento", None, None),
    (r"asaas\.com", "asaas", "pagamento", None, None),
    # Chat / Atendimento
    (r"tawk\.to", "tawk_to", "chat", None, None),
    (r"jivochat\.com", "jivochat", "chat", None, None),
    (r"crisp\.chat", "crisp", "chat", None, None),
    (r"zendesk\.com", "zendesk", "chat", "helpdesk", None),
    (r"intercom\.io", "intercom", "chat", None, None),
    (r"api\.whatsapp\.com|wa\.me", "whatsapp_widget", "chat", None, None),
    # E-commerce
    (r"cdn\.shopify\.com", "shopify", "ecommerce", "platform", None),
    (r"nuvemshop\.com\.br|lojaintegrada", "nuvemshop", "ecommerce", "platform", None),
    (r"vtex\.(com|io)", "vtex", "ecommerce", "platform", None),
    (r"woocommerce|wc-ajax", "woocommerce", "ecommerce", "plugin", None),
    # CMS
    (r"wp-content|wp-includes", "wordpress", "cms", "platform", None),
    (r"joomla", "joomla", "cms", "platform", None),
    (r"webflow\.com", "webflow", "cms", "platform", None),
    # Segurança
    (r"recaptcha/api\.js", "recaptcha", "seguranca", None, None),
    (r"hcaptcha\.com", "hcaptcha", "seguranca", None, None),
    (r"challenges\.cloudflare\.com", "cloudflare_turnstile", "seguranca", None, None),
    # Mídia / CDN
    (r"cloudinary\.com", "cloudinary", "midia", None, None),
    (r"youtube\.com/embed", "youtube_embed", "midia", "video", None),
    (r"player\.vimeo\.com", "vimeo_embed", "midia", "video", None),
    (r"maps\.googleapis\.com", "google_maps", "midia", "mapas", None),
    # Social
    (r"accounts\.google\.com/gsi", "google_signin", "social", "auth", None),
    (r"connect\.facebook\.net[^\"']*/sdk", "facebook_sdk", "social", None, None),
    (r"appleid\.apple\.com", "apple_signin", "social", "auth", None),
    # Infra / assets
    (r"cdn\.jsdelivr\.net", "jsdelivr", "cdn", "assets", None),
    (r"cdnjs\.cloudflare\.com", "cdnjs", "cdn", "assets", None),
    (r"unpkg\.com", "unpkg", "cdn", "assets", None),
    # Busca
    (r"algolia\.net|algoliasearch", "algolia", "busca", None, None),
    # Autenticação
    (r"auth0\.com", "auth0", "autenticacao", None, None),
    (r"firebase[^\"']*auth|firebaseapp", "firebase_auth", "autenticacao", None, None),
]

# --------------------------------------------------------------------------- #
# Grupo 3 — Meta tags / Schema.org
# --------------------------------------------------------------------------- #
# (key, regex, name, category, subcategory). ``name=None`` → tratado à parte
# (ex.: generator extrai o CMS do atributo content).

META_EXTRACTIONS: List[tuple] = [
    ("og_tags", r'<meta\s+property=["\']og:', "open_graph", "social", None),
    ("google_verification",
     r'<meta\s+name=["\']google-site-verification["\']', "google_search_console",
     "marketing", "seo"),
    ("fb_verification",
     r'<meta\s+name=["\']facebook-domain-verification["\']', "facebook_verified",
     "marketing", None),
    ("generator", r'<meta\s+name=["\']generator["\']\s+content=["\']([^"\']+)["\']',
     None, "cms", None),
    ("rss", r'<link\s+[^>]*type=["\']application/rss\+xml["\']', "rss_feed",
     "marketing", "conteudo"),
]

# --------------------------------------------------------------------------- #
# Grupo 4 — DNS (provedores de e-mail / DNS / plataformas verificadas)
# --------------------------------------------------------------------------- #
# Substring do hostname MX → provedor de e-mail. Ordem importa (mais específico antes).
EMAIL_PROVIDERS: List[tuple] = [
    ("aspmx.l.google.com", "google_workspace"),
    ("googlemail.com", "google_workspace"),
    ("google.com", "google_workspace"),
    ("protection.outlook.com", "microsoft_365"),
    ("outlook", "microsoft_365"),
    ("locaweb", "locaweb"),
    ("hostinger", "hostinger"),
    ("zoho", "zoho"),
    ("titan", "titan"),
    ("secureserver.net", "godaddy"),
    ("registro.br", "registro_br"),
    ("umbler", "umbler"),
    ("kinghost", "kinghost"),
]

# Substring do hostname NS → provedor de DNS.
DNS_PROVIDERS: List[tuple] = [
    ("cloudflare", "cloudflare"),
    ("awsdns", "aws_route53"),
    ("azure-dns", "azure_dns"),
    ("googledomains", "google_dns"),
    ("google.com", "google_dns"),
    ("registro.br", "registro_br"),
    ("hostinger", "hostinger"),
    ("locaweb", "locaweb"),
]

# Prefixo de um record TXT → plataforma verificada. ``None`` = não é verificação de
# plataforma (SPF é config de e-mail, não prova de conta em plataforma).
VERIFIED_PLATFORMS: List[tuple] = [
    ("google-site-verification", "google"),
    ("facebook-domain-verification", "facebook"),
    ("pinterest-site-verification", "pinterest"),
    ("ms=", "microsoft"),
    ("v=spf1", None),
]

# CA do certificado SSL (issuer) → tecnologia de segurança.
SSL_ISSUERS: List[tuple] = [
    ("let's encrypt", "lets_encrypt"),
    ("r3", "lets_encrypt"),
    ("e1", "lets_encrypt"),
    ("digicert", "digicert"),
    ("sectigo", "sectigo"),
    ("comodo", "sectigo"),
    ("cpanel", "cpanel"),
    ("zerossl", "zerossl"),
    ("cloudflare", "cloudflare_ssl"),
    ("google trust", "google_trust"),
    ("gts", "google_trust"),
    ("amazon", "amazon_ssl"),
    ("godaddy", "godaddy_ssl"),
]

# --------------------------------------------------------------------------- #
# Grupo 6 — Status do site (parking / abandono)
# --------------------------------------------------------------------------- #
PARKING_PATTERNS: List[str] = [
    r"this domain is for sale",
    r"domain is parked",
    r"este dom[ií]nio est[aá] [àa] venda",
    r"em constru[çc][aã]o",
    r"coming soon",
    r"under construction",
    r"p[aá]gina padr[aã]o",
    r"default web page",
    r"hospedagem de sites",
    r"site em manuten[çc][aã]o",
    r"hostinger[^<]*default",
    r"godaddy[^<]*parked",
    r"namecheap[^<]*parked",
]
_PARKING_RE = [re.compile(p, re.I) for p in PARKING_PATTERNS]

# Suffixes de dois rótulos (aproximação dependency-free da Public Suffix List) — para
# resolver o domínio registrável do SAN (mesma lógica de ``scanner.checks.base``).
_TWO_LABEL_SUFFIXES = {
    "com.br", "net.br", "org.br", "gov.br", "edu.br", "art.br", "blog.br",
    "co.uk", "org.uk", "gov.uk", "ac.uk", "com.au", "net.au", "org.au",
    "co.jp", "com.mx", "com.ar", "com.co", "co.in", "com.pt", "co.za", "github.io",
}


def _registrable_domain(host: str) -> str:
    """Domínio registrável (eTLD+1). ``www.hotel.com.br`` → ``hotel.com.br``."""
    host = (host or "").lower().strip(".")
    parts = host.split(".")
    if len(parts) <= 2:
        return host
    last2 = ".".join(parts[-2:])
    if last2 in _TWO_LABEL_SUFFIXES:
        return ".".join(parts[-3:])
    return last2


# --------------------------------------------------------------------------- #
# Helpers puros
# --------------------------------------------------------------------------- #

def _lower_headers(headers: dict) -> Dict[str, str]:
    """Normaliza as chaves para minúsculas (headers HTTP são case-insensitive).
    Valores viram string (um header repetido pode vir como lista)."""
    out: Dict[str, str] = {}
    for k, v in (headers or {}).items():
        if v is None:
            continue
        key = str(k).lower()
        val = " ".join(str(x) for x in v) if isinstance(v, (list, tuple)) else str(v)
        # Se o mesmo header aparecer 2x, concatena (não perde o segundo valor).
        out[key] = (out[key] + " " + val) if key in out else val
    return out


def _add_tech(acc: Dict[str, dict], name: str, category: str,
              subcategory: Optional[str], version: Optional[str], source: str,
              confidence: float = 1.0) -> None:
    """Acumula uma detecção deduplicando por ``name``. Se a tech já existe sem versão
    e a nova traz versão, a versão preenche (a 1ª detecção manda no resto dos campos)."""
    if not name:
        return
    version = (version or None)
    existing = acc.get(name)
    if existing is None:
        acc[name] = {"name": name, "category": category, "subcategory": subcategory,
                     "version": version, "source": source,
                     "confidence": round(float(confidence), 2)}
        return
    if existing.get("version") is None and version is not None:
        existing["version"] = version


def _detect_headers(headers: Dict[str, str], acc: Dict[str, dict]) -> None:
    for header, patterns in HEADER_PATTERNS.items():
        value = headers.get(header)
        if not value:
            continue
        for regex, name, category, subcategory in patterns:
            m = re.search(regex, value, re.I)
            if not m:
                continue
            version = None
            if m.groups():
                version = m.group(1)
            _add_tech(acc, name, category, subcategory, version, "header")


def _detect_cookies(headers: Dict[str, str], acc: Dict[str, dict]) -> None:
    cookies = headers.get("set-cookie") or ""
    if not cookies:
        return
    for regex, name, category, subcategory in COOKIE_PATTERNS:
        if re.search(regex, cookies, re.I):
            _add_tech(acc, name, category, subcategory, None, "cookie")


def _detect_scripts(html: str, acc: Dict[str, dict]) -> None:
    for regex, name, category, subcategory, version_group in SCRIPT_PATTERNS:
        m = re.search(regex, html, re.I)
        if not m:
            continue
        # version_group não-None → a versão é o grupo 1 (ex.: GA4 ``G-XXXX``).
        version = m.group(1) if (version_group and m.lastindex) else None
        _add_tech(acc, name, category, subcategory, version, "script")


def _parse_generator(content: str) -> tuple:
    """``WordPress 6.4`` → (``wordpress``, ``6.4``). Normaliza o 1º token para slug."""
    content = (content or "").strip()
    if not content:
        return None, None
    token = re.split(r"[\s!/]+", content, 1)[0].lower()
    slug = re.sub(r"[^a-z0-9]+", "_", token).strip("_")
    mver = re.search(r"(\d+(?:\.\d+)+)", content)
    return (slug or None), (mver.group(1) if mver else None)


def _detect_meta(html: str, acc: Dict[str, dict], platforms: set) -> None:
    for key, regex, name, category, subcategory in META_EXTRACTIONS:
        m = re.search(regex, html, re.I)
        if not m:
            continue
        if key == "generator":
            slug, version = _parse_generator(m.group(1))
            _add_tech(acc, slug, category or "cms", "platform", version, "meta", 0.9)
            continue
        _add_tech(acc, name, category, subcategory, None, "meta", 0.9)
        if key == "google_verification":
            platforms.add("google")
        elif key == "fb_verification":
            platforms.add("facebook")


def _match_substring(value: str, table: List[tuple]) -> Optional[str]:
    """Primeira entrada de ``table`` cuja chave é substring de ``value`` (lower)."""
    v = (value or "").lower()
    for needle, result in table:
        if needle in v:
            return result
    return None


def _detect_dns(dns: dict, acc: Dict[str, dict], platforms: set) -> tuple:
    """Retorna (email_provider, dns_provider). Também grava ambos como tecnologias e
    coleta plataformas verificadas dos records TXT."""
    dns = dns or {}
    email_provider = None
    for host in dns.get("mx") or []:
        email_provider = _match_substring(host, EMAIL_PROVIDERS)
        if email_provider:
            break
    if email_provider:
        _add_tech(acc, email_provider, "email", "provider", None, "dns")

    dns_provider = None
    for host in dns.get("ns") or []:
        dns_provider = _match_substring(host, DNS_PROVIDERS)
        if dns_provider:
            break
    if dns_provider:
        _add_tech(acc, dns_provider, "dns", "provider", None, "dns")

    for record in dns.get("txt") or []:
        r = (record or "").lower().strip().strip('"')
        for prefix, platform in VERIFIED_PLATFORMS:
            if r.startswith(prefix) and platform:
                platforms.add(platform)
    return email_provider, dns_provider


def _detect_ssl(ssl: dict, acc: Dict[str, dict]) -> tuple:
    """Retorna (related_domains, company_name). Grava a CA como tecnologia.

    - ``related_domains``: SANs do mesmo domínio registrável (wildcards viram a base;
      certificados são registros públicos — dado público).
    - ``company_name``: organização do certificado (OV/EV) — nome legal da empresa.
    """
    ssl = ssl or {}
    cert = ssl.get("cert") or {}

    # CA (issuer).
    issuer = cert.get("issuer_cn") or ssl.get("issuer") or ""
    ca = _match_substring(issuer, SSL_ISSUERS)
    if ca:
        _add_tech(acc, ca, "seguranca", "certificado", None, "ssl")

    # Domínios relacionados via SAN.
    san = cert.get("san") or ssl.get("san") or []
    subject = cert.get("subject_cn") or ""
    names = []
    for entry in san:
        e = (entry or "").lower().strip().lstrip("*.")
        if e and "." in e:
            names.append(e)
    base = _registrable_domain(subject) if subject else None
    if not base and names:
        # Sem subject: usa o domínio registrável mais comum entre os SANs como base.
        regs = [_registrable_domain(n) for n in names]
        base = max(set(regs), key=regs.count) if regs else None
    related: List[str] = []
    seen = set()
    for n in names:
        if base and _registrable_domain(n) != base:
            continue
        if n not in seen:
            seen.add(n)
            related.append(n)
    related.sort()

    company_name = (cert.get("subject_o") or cert.get("organization")
                    or ssl.get("organization") or None)
    company_name = company_name.strip() if isinstance(company_name, str) else None
    return related, (company_name or None)


def classify_site_status(http_status, html: str, response_time_ms=None,
                         has_scripts: bool = False) -> str:
    """Classifica o estado do site a partir do status HTTP e do HTML.

    ``dominio_inativo`` (sem resposta) · ``fora_do_ar`` (5xx) · ``parked`` (200 +
    padrão de estacionamento) · ``abandonado`` (200 + HTML mínimo sem scripts) ·
    ``bloqueado`` (403 — pode ser challenge de WAF) · ``ativo`` (default conservador).
    """
    if http_status is None or http_status == 0:
        return "dominio_inativo"
    if http_status >= 500:
        return "fora_do_ar"
    if http_status == 200:
        if any(rx.search(html or "") for rx in _PARKING_RE):
            return "parked"
        if len(html or "") < 500 and not has_scripts:
            return "abandonado"
        return "ativo"
    if http_status in (301, 302, 307, 308):
        return "ativo"
    if http_status == 403:
        return "bloqueado"
    return "ativo"


# Extração de @type do JSON-LD (Schema.org) — confirma o tipo de negócio (setor).
_JSONLD_TYPE_RE = re.compile(r'"@type"\s*:\s*"([^"]+)"', re.I)


def _schema_types(html: str) -> List[str]:
    types = []
    for m in _JSONLD_TYPE_RE.finditer(html or ""):
        t = m.group(1).strip()
        if t and t not in types:
            types.append(t)
    return types[:10]


# --------------------------------------------------------------------------- #
# API pública do módulo
# --------------------------------------------------------------------------- #

def detect_tech_stack(headers: dict, html: str, dns: dict, ssl: dict) -> dict:
    """Detecta o tech stack a partir do response bruto (função pura, sem I/O).

    Retorna ``{technologies, email_provider, dns_provider, related_domains,
    site_status, verified_platforms, company_name, schema_types}``. ``technologies`` é
    uma lista de dicts ``{name, category, subcategory, version, source, confidence}``
    deduplicada por ``name``. Nunca levanta: entradas ausentes/malformadas viram vazio.

    ``site_status`` aqui é derivado só do CONTEÚDO (parked/abandonado/ativo) — os estados
    que dependem do código HTTP (``dominio_inativo``/``fora_do_ar``/``bloqueado``) o
    chamador resolve com :func:`classify_site_status` (ele tem o ``http_status`` real);
    o scan worker persiste esse valor autoritativo.
    """
    headers = _lower_headers(headers or {})
    html = html or ""
    acc: Dict[str, dict] = {}
    platforms: set = set()

    _detect_headers(headers, acc)
    _detect_cookies(headers, acc)
    _detect_scripts(html, acc)
    _detect_meta(html, acc, platforms)
    email_provider, dns_provider = _detect_dns(dns or {}, acc, platforms)
    related_domains, company_name = _detect_ssl(ssl or {}, acc)

    # Status por conteúdo: assume que a homepage respondeu (o worker refina com o
    # http_status real). Sem http_status, os estados de código HTTP não se aplicam.
    has_scripts = bool(re.search(r"<script", html, re.I))
    site_status = classify_site_status(200, html, None, has_scripts)

    return {
        "technologies": list(acc.values()),
        "email_provider": email_provider,
        "dns_provider": dns_provider,
        "related_domains": related_domains,
        "site_status": site_status,
        "verified_platforms": sorted(platforms),
        "company_name": company_name,
        "schema_types": _schema_types(html),
    }
