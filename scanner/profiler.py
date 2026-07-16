"""Perfil comercial de um site (KL-50, camada 2) — extração passiva de dados de
negócio do HTML já coletado, sem dependências externas (regex + json.loads).

Os *parsers* são **puros** (recebem HTML/headers, não tocam a rede) e testáveis
offline. `crawl_contact_pages` (multi-page, camada 1) e `build_profile`
(orquestrador) usam o `fetch` do scanner (rate limit 1 req/s por domínio).

Nada aqui afeta o score de segurança — é dado comercial para perfis públicos,
notificações e aquisição orgânica.
"""

from __future__ import annotations

import json
import re
from typing import Dict, List, Optional
from urllib.parse import urljoin

import httpx

from scanner.checks.base import fetch, base_url, registrable_domain, domain_of
from discovery.contact import _collect_emails, _ranked_emails

# Páginas internas de contato (camada 1). A homepage já vem no crawl.
CONTACT_PATHS = [
    "contato", "contact", "sobre", "about",
    "quem-somos", "sobre-nos", "fale-conosco", "atendimento",
]

_HREF_RE = re.compile(r"""href\s*=\s*['"]([^'"]+)['"]""", re.IGNORECASE)
_SCRIPT_SRC_RE = re.compile(r"""<script[^>]+src\s*=\s*['"]([^'"]+)['"]""", re.IGNORECASE)
_LDJSON_RE = re.compile(
    r"""<script[^>]+type\s*=\s*['"]application/ld\+json['"][^>]*>(.*?)</script>""",
    re.IGNORECASE | re.DOTALL,
)
_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
# Remove script/style (senão o regex de telefone/endereço casa dentro do JSON-LD).
_SCRIPT_STYLE_RE = re.compile(r"<(script|style)\b[^>]*>.*?</\1>", re.DOTALL | re.IGNORECASE)


# --------------------------------------------------------------------------- #
# A. Contatos
# --------------------------------------------------------------------------- #

# Telefone BR no texto: DDD opcional + separador interno OBRIGATÓRIO (hífen/ponto)
# entre as duas metades. Sem o separador, corridas de dígitos (IDs, timestamps,
# números de rastreio) viravam "telefone" — o separador é o que marca um número
# realmente exibido para o cliente.
_PHONE_RE = re.compile(r"(?:\(?\d{2}\)?[\s.\-]+)?\d{4,5}[.\-]\d{4}")
_TEL_RE = re.compile(r"""tel:\s*\+?([\d\s().\-]{8,})""", re.IGNORECASE)
_WA_RE = re.compile(
    r"""(?:wa\.me/|api\.whatsapp\.com/send\?phone=|web\.whatsapp\.com/send\?phone=)"""
    r"""\+?(55\d{10,11})""",
    re.IGNORECASE,
)
_WA_DATA_RE = re.compile(r"""data-phone\s*=\s*['"]\+?(55\d{10,11})['"]""", re.IGNORECASE)
_CNPJ_RE = re.compile(r"\b(\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2})\b")
# Endereço BR: "Rua/Av/Avenida/Travessa ... , 123 ... UF"
_ADDR_RE = re.compile(
    r"((?:Rua|Av\.?|Avenida|Travessa|Rodovia|Alameda|Praça|Estrada)[^<>\n]{6,90}?"
    r"\b(?:AC|AL|AP|AM|BA|CE|DF|ES|GO|MA|MT|MS|MG|PA|PB|PR|PE|PI|RJ|RN|RS|RO|RR|SC|SP|SE|TO)\b)",
    re.IGNORECASE,
)


def validate_cnpj(cnpj: str) -> bool:
    """Valida os dígitos verificadores de um CNPJ (aceita com ou sem máscara)."""
    nums = re.sub(r"\D", "", cnpj or "")
    if len(nums) != 14 or nums == nums[0] * 14:
        return False

    def _dv(digs: str, weights: List[int]) -> int:
        s = sum(int(d) * w for d, w in zip(digs, weights))
        r = s % 11
        return 0 if r < 2 else 11 - r

    d1 = _dv(nums[:12], [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2])
    d2 = _dv(nums[:13], [6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2])
    return nums[12] == str(d1) and nums[13] == str(d2)


def _fmt_phone(digs: str) -> str:
    """Formata dígitos BR (DD + 8/9) como (DD) 9999-9999."""
    dd, rest = digs[:2], digs[2:]
    if len(rest) == 9:
        return f"({dd}) {rest[:5]}-{rest[5:]}"
    return f"({dd}) {rest[:4]}-{rest[4:]}"


def _first_phone(html_with_scripts: str, visible: str) -> Optional[str]:
    # tel: tem prioridade (marcado pelo dono) — extrai só os dígitos e formata.
    for raw in _TEL_RE.findall(html_with_scripts or ""):
        digs = re.sub(r"\D", "", raw)
        if digs.startswith("55") and len(digs) in (12, 13):
            digs = digs[2:]  # tira o código do país
        if 10 <= len(digs) <= 11:
            return _fmt_phone(digs)
    # Fallback: texto visível (sem script/style/comentário). Normaliza igual ao
    # tel: — sem isso um match sairia cru e inconsistente (ex.: "55119444494").
    m = _PHONE_RE.search(visible or "")
    if not m:
        return None
    digs = re.sub(r"\D", "", m.group(0))
    if digs.startswith("55") and len(digs) in (12, 13):
        digs = digs[2:]  # tira o código do país
    if 10 <= len(digs) <= 11:
        return _fmt_phone(digs)
    if 8 <= len(digs) <= 9:          # número local sem DDD — devolve como veio
        return m.group(0).strip()
    return None


# --------------------------------------------------------------------------- #
# Validações de qualidade (KL-67) — filtros REGEX/heurística na camada de extração.
# Regra inviolável: são filtros (rejeitam lixo → NULL), NUNCA sobrescrita por IA. A IA
# pode preencher um campo NULL depois, mas não substitui um dado que passou na validação.
# --------------------------------------------------------------------------- #

# DDDs brasileiros válidos (2026). Fora desta lista → telefone rejeitado.
VALID_DDDS = {11, 12, 13, 14, 15, 16, 17, 18, 19, 21, 22, 24, 27, 28, 31, 32, 33, 34, 35,
              37, 38, 41, 42, 43, 44, 45, 46, 47, 48, 49, 51, 53, 54, 55, 61, 62, 63, 64,
              65, 66, 67, 68, 69, 71, 73, 74, 75, 77, 79, 81, 82, 83, 84, 85, 86, 87, 88,
              89, 91, 92, 93, 94, 95, 96, 97, 98, 99}


# Números especiais BR (sem DDD) — legítimos de empresas. Não podem ser rejeitados.
_SPECIAL_PHONE_PREFIXES = ("0800", "0300", "0500", "0900")           # 11-12 dígitos
_SHARED_PHONE_PREFIXES = ("3003", "4003", "4004", "4007", "4020", "4062", "4090")  # 8 dígitos


def validate_phone(raw: Optional[str]) -> Optional[str]:
    """Dígitos limpos do telefone (DDD+8/9) ou None se inválido (DDD inexistente,
    formato errado, celular sem 9). Aceita e remove o código do país (55). Números
    especiais BR (0800/0300/0500/0900 e 3003/4004/… de custo compartilhado) são válidos."""
    digits = re.sub(r"\D", "", raw or "")
    # Especiais primeiro (não têm DDD): 0800 + 7/8 dígitos; 4004/3003 + 4 dígitos.
    if any(digits.startswith(p) for p in _SPECIAL_PHONE_PREFIXES) and len(digits) in (11, 12):
        return digits
    if len(digits) == 8 and digits[:4] in _SHARED_PHONE_PREFIXES:
        return digits
    if digits.startswith("55") and len(digits) >= 12:
        digits = digits[2:]
    if len(digits) not in (10, 11):
        return None
    if int(digits[:2]) not in VALID_DDDS:
        return None
    if len(digits) == 11 and digits[2] != "9":   # celular tem que começar com 9
        return None
    return digits


# Handles genéricos / de template (não são o perfil do site).
SOCIAL_REJECTS = {
    "people", "share", "sharer", "sharer.php", "intent", "dialog", "pages", "groups",
    "channel", "hashtag", "search", "explore", "settings", "login", "signup", "help",
    "about", "privacy", "terms", "policy", "undefined", "null", "home", "profile.php",
}
SHARE_PATTERNS = ["share", "sharer.php", "intent/tweet", "pin/create", "dialog/"]


def validate_social_handle(platform: str, url_or_handle: str) -> Optional[str]:
    """Handle limpo ou None. Rejeita genéricos (people/share/…), URLs de compartilhamento
    e handles curtos/longos demais. Aceita tanto um handle quanto uma URL completa."""
    s = (url_or_handle or "").strip()
    if not s:
        return None
    low = s.lower()
    for pat in SHARE_PATTERNS:
        if pat in low:
            return None
    handle = s
    if "/" in s:   # é uma URL → pega o último segmento significativo
        segs = [p for p in s.split("?")[0].rstrip("/").split("/") if p]
        handle = segs[-1] if segs else ""
    handle = handle.lstrip("@").strip("/")
    hl = handle.lower()
    if not hl or hl in SOCIAL_REJECTS:
        return None
    if len(hl) < 2 or len(hl) > 50:
        return None
    return handle


def handle_matches_domain(handle: str, domain: str) -> bool:
    """Heurística fuzzy: o handle tem relação razoável com o domínio? (evita o Instagram
    de uma hamburgueria no perfil de uma contabilidade). Sem Levenshtein: substring 4+."""
    domain_name = (domain or "").split(".")[0].lower()
    hc = (handle or "").lower().strip("@")
    if not domain_name or not hc:
        return False
    if domain_name in hc or hc in domain_name:
        return True
    for i in range(len(domain_name) - 3):
        if domain_name[i:i + 4] in hc:
            return True
    return False


# Padrões que denunciam CSS/HTML raspado por engano (não é um endereço).
ADDRESS_REJECTS = [r"navbar", r"nav-", r"d-flex", r"container", r"col-", r"\brow\b",
                   r"class=", r"style=", r"<div", r"<span", r"\bpx\b", r"\brem\b",
                   r"display:", r"margin:", r"padding:"]
_ADDR_INDICATORS = [r"\brua\b", r"\bav\b", r"av\.", r"avenida", r"travessa", r"alameda",
                    r"rodovia", r"estrada", r"pra[çc]a", r"largo", r"\bcep\b",
                    r"bairro", r"\d{5}-?\d{3}"]


def validate_address(raw: Optional[str]) -> Optional[str]:
    """Endereço limpo ou None. Rejeita CSS raspado, tamanhos absurdos e strings sem
    nenhum indicador de endereço brasileiro (rua/av/cep/…)."""
    if not raw or len(raw) < 15 or len(raw) > 300:
        return None
    low = raw.lower()
    for pat in ADDRESS_REJECTS:
        if re.search(pat, low):
            return None
    if not any(re.search(ind, low) for ind in _ADDR_INDICATORS):
        return None
    return raw.strip()


# Descrições genéricas de template (YouTube/WordPress/lorem…).
DESCRIPTION_REJECTS = [r"you need to enable javascript", r"share your videos with friends",
                       r"wordpress starter theme", r"just another wordpress site",
                       r"this is a default description", r"lorem ipsum", r"sample page",
                       r"hello world", r"website powered by", r"built with"]
# Palavras distintamente PT-BR (removidas as ambíguas com inglês: a/o/e/as/no/na, que
# davam falso-positivo em descrições em inglês, ex.: "We are **a** company…").
_PT_WORDS = {"de", "do", "da", "dos", "das", "em", "para", "com", "uma", "que", "por",
             "seu", "sua", "são", "está", "não", "mais", "nossa", "nosso", "pela", "pelo",
             "você", "aos", "às", "ão"}


def validate_description(raw: Optional[str], domain: str = "") -> Optional[str]:
    """Descrição limpa ou None. Rejeita templates genéricos e texto que quase certamente
    não é português (heurística de palavras comuns PT-BR)."""
    if not raw or len(raw) < 20:
        return None
    if len(raw) > 500:
        raw = raw[:500]
    low = raw.lower()
    for pat in DESCRIPTION_REJECTS:
        if re.search(pat, low):
            return None
    words = low.split()
    if len(words) > 10:
        pt_ratio = sum(1 for w in words if w.strip(".,;:!?") in _PT_WORDS) / max(len(words), 1)
        if pt_ratio < 0.05:   # provavelmente inglês/outro idioma
            return None
    return raw.strip()


# Redes sociais sujeitas ao flag de baixa confiança (não batem o domínio).
_SOCIAL_FIELDS = ("instagram", "facebook", "linkedin", "youtube", "tiktok")


def apply_quality_filters(profile: dict, domain: str = "") -> dict:
    """KL-67 — aplica os validadores ao perfil montado (in-place) e popula
    `low_confidence_fields`. Filtro puro: rejeita lixo (→ None), nunca substitui por IA."""
    low_conf: List[str] = []
    if profile.get("phone"):
        # Valida (mantém o valor original quando OK; não reformata — não mangla 0800/4004).
        if not validate_phone(profile["phone"]):
            profile["phone"] = None
    if profile.get("address"):
        profile["address"] = validate_address(profile["address"])
    if profile.get("description"):
        profile["description"] = validate_description(profile["description"], domain)
    for net in _SOCIAL_FIELDS:
        val = profile.get(net)
        if not val:
            continue
        clean = validate_social_handle(net, val)
        profile[net] = clean
        if clean and domain and not handle_matches_domain(clean, domain):
            low_conf.append(net)   # mantém o valor, mas sinaliza suspeita
    profile["low_confidence_fields"] = low_conf
    return profile


def extract_contacts(html_pages: Dict[str, str], site_domain: str = "") -> dict:
    """Extrai contatos de várias páginas HTML: e-mail, telefone, whatsapp,
    endereço e CNPJ (validado)."""
    combined = "\n".join(html_pages.values())
    # Texto "visível" (sem script/style/comentário) para telefone e endereço.
    visible = _HTML_COMMENT_RE.sub(" ", _SCRIPT_STYLE_RE.sub(" ", combined))

    # E-mail: reusa a extração/ranking hardened do discovery (KL-19/24).
    emails: List[str] = []
    for html in html_pages.values():
        emails.extend(_collect_emails(html))
    ranked = _ranked_emails(emails, site_domain) if site_domain else _ranked_emails(emails, "")
    email = ranked[0] if ranked else None

    whatsapp = None
    for rx in (_WA_RE, _WA_DATA_RE):
        m = rx.search(combined)  # wa.me pode estar em href/script → usa o combinado
        if m:
            whatsapp = m.group(1)
            break

    cnpj = None
    for c in _CNPJ_RE.findall(visible):
        if validate_cnpj(c):
            cnpj = c
            break

    addr_m = _ADDR_RE.search(visible)
    address = re.sub(r"\s+", " ", addr_m.group(1)).strip() if addr_m else None

    return {
        "email": email,
        "phone": _first_phone(combined, visible),
        "whatsapp": whatsapp,
        "address": address,
        "cnpj": cnpj,
    }


# --------------------------------------------------------------------------- #
# B. Dados estruturados (JSON-LD / Schema.org)
# --------------------------------------------------------------------------- #

# @type do schema.org → setor Klarim (mais confiável que regex de conteúdo).
# KL-54: mapeamento fino para a taxonomia de 48 setores. Chaves em minúsculo.
_SCHEMA_SECTOR = {
    # hospedagem & turismo
    "hotel": "hotel", "lodgingbusiness": "hotel", "resort": "hotel",
    "bedandbreakfast": "hotel", "hostel": "hotel", "motel": "hotel", "accommodation": "hotel",
    "travelagency": "turismo_viagens",
    # alimentação
    "restaurant": "restaurante", "foodestablishment": "restaurante",
    "cafeorcoffeeshop": "bar_lanchonete", "barorpub": "bar_lanchonete", "bakery": "padaria_confeitaria",
    # saúde
    "medicalbusiness": "clinica", "medicalclinic": "clinica", "physician": "clinica",
    "dentist": "odontologia", "pharmacy": "farmacia", "veterinarycare": "veterinaria",
    "hospital": "hospital", "medicallaboratory": "laboratorio", "psychologist": "psicologia",
    # beleza & bem-estar
    "beautysalon": "salao_barbearia", "hairsalon": "salao_barbearia", "barbershop": "salao_barbearia",
    "dayspa": "estetica_spa", "healthandbeautybusiness": "estetica_spa",
    "healthclub": "academia", "sportsactivitylocation": "academia",
    # comércio
    "store": "ecommerce", "onlinestore": "ecommerce",
    "clothingstore": "loja_moda", "shoestore": "loja_moda", "jewelrystore": "loja_moda",
    "furniturestore": "moveis_decoracao", "homegoodsstore": "moveis_decoracao",
    "electronicsstore": "eletronicos", "computerstore": "eletronicos",
    "grocerystore": "supermercado", "supermarket": "supermercado",
    "petstore": "petshop", "hardwarestore": "material_construcao",
    "opticalstore": "otica", "optician": "otica",
    # serviços profissionais
    "legalservice": "juridico", "attorney": "juridico",
    "accountingservice": "contabilidade",
    "financialservice": "seguros_financeiro", "insuranceagency": "seguros_financeiro",
    "employmentagency": "rh_recrutamento", "professionalservice": "consultoria",
    "advertisingagency": "agencia", "marketingagency": "agencia", "printshop": "grafica",
    # imóveis & construção
    "realestateagent": "imobiliaria",
    "generalcontractor": "construtora", "homeandconstructionbusiness": "construtora",
    # automotivo
    "automotivebusiness": "automotivo", "autorepair": "automotivo", "autodealer": "automotivo",
    # educação
    "school": "escola", "educationalorganization": "escola", "elementaryschool": "escola",
    "preschool": "escola", "highschool": "escola",
    "collegeoruniversity": "faculdade", "languageschool": "curso_idiomas",
    # eventos & entretenimento
    "eventvenue": "eventos_buffet", "caterer": "eventos_buffet",
    "photographyservice": "fotografia", "photographer": "fotografia",
    # institucional
    "church": "religioso", "placeofworship": "religioso", "ngo": "ong_associacao",
    "governmentorganization": "governo", "governmentoffice": "governo",
}


def _iter_ld_objects(html: str):
    for block in _LDJSON_RE.findall(html or ""):
        try:
            data = json.loads(block.strip())
        except (json.JSONDecodeError, ValueError):
            continue
        stack = data.get("@graph", data) if isinstance(data, dict) else data
        for obj in (stack if isinstance(stack, list) else [stack]):
            if isinstance(obj, dict):
                yield obj


def extract_structured_data(html: str) -> dict:
    """Parse dos <script type="application/ld+json">. Extrai name/telephone/email/
    address/openingHours/sameAs/logo/description + setor pelo @type."""
    out: dict = {"same_as": []}
    for obj in _iter_ld_objects(html):
        types = obj.get("@type", "")
        types = [types] if isinstance(types, str) else (types or [])
        for t in types:
            sec = _SCHEMA_SECTOR.get(str(t).lower())
            if sec and not out.get("sector"):
                out["sector"] = sec
        for key, field in (("name", "company_name"), ("telephone", "phone"),
                           ("email", "email"), ("description", "description")):
            if obj.get(key) and not out.get(field):
                out[field] = str(obj[key]).strip()
        logo = obj.get("logo")
        if logo and not out.get("logo_url"):
            out["logo_url"] = logo.get("url") if isinstance(logo, dict) else str(logo)
        addr = obj.get("address")
        if isinstance(addr, dict) and not out.get("address"):
            parts = [addr.get(k) for k in ("streetAddress", "addressLocality",
                                           "addressRegion", "postalCode")]
            joined = ", ".join(str(p) for p in parts if p)
            if joined:
                out["address"] = joined
        hours = obj.get("openingHours")
        if hours and not out.get("business_hours"):
            out["business_hours"] = ", ".join(hours) if isinstance(hours, list) else str(hours)
        same = obj.get("sameAs")
        if same:
            out["same_as"].extend(same if isinstance(same, list) else [same])
    return out


# --------------------------------------------------------------------------- #
# C. Presença digital (redes sociais)
# --------------------------------------------------------------------------- #

# rede -> (regex do handle, segmentos reservados a ignorar)
_SOCIAL = {
    "instagram": re.compile(r"instagram\.com/([A-Za-z0-9._]+)", re.IGNORECASE),
    "facebook": re.compile(r"facebook\.com/([A-Za-z0-9.\-]+)", re.IGNORECASE),
    "linkedin": re.compile(r"linkedin\.com/(?:company|in)/([A-Za-z0-9._\-]+)", re.IGNORECASE),
    "youtube": re.compile(r"youtube\.com/(channel/[A-Za-z0-9_\-]+|@[A-Za-z0-9._\-]+|c/[A-Za-z0-9._\-]+|user/[A-Za-z0-9._\-]+)", re.IGNORECASE),
    "tiktok": re.compile(r"tiktok\.com/@([A-Za-z0-9._]+)", re.IGNORECASE),
}
_SOCIAL_RESERVED = {
    "sharer", "share", "sharer.php", "share.php", "plugins", "tr", "p", "reel",
    "reels", "login", "dialog", "profile.php", "events", "groups", "watch",
    "pages", "story.php", "permalink.php", "embed", "intent", "hashtag", "explore",
    "policies", "help", "about", "home", "results",
}
_MAPS_RE = re.compile(r"(maps\.google\.[a-z.]+|goo\.gl/maps|google\.[a-z.]+/maps|maps\.app\.goo\.gl)[^\s'\"<>]*",
                      re.IGNORECASE)
_RSS_RE = re.compile(r"""<link[^>]+type\s*=\s*['"]application/rss\+xml['"]""", re.IGNORECASE)


def extract_social_links(html_pages: Dict[str, str]) -> dict:
    """Extrai handles de redes sociais + Google Maps + has_blog/has_app dos <a href>."""
    out: dict = {"has_blog": False, "has_app": False}
    hrefs: List[str] = []
    combined = ""
    for html in html_pages.values():
        clean = _HTML_COMMENT_RE.sub(" ", html or "")
        combined += clean
        hrefs.extend(_HREF_RE.findall(clean))

    for net, rx in _SOCIAL.items():
        for href in hrefs:
            m = rx.search(href)
            if not m:
                continue
            handle = m.group(1).strip("/").lower()
            first = handle.split("/")[0]
            if not handle or first in _SOCIAL_RESERVED:
                continue
            out[net] = handle
            break

    maps = _MAPS_RE.search(combined)
    if maps:
        out["google_maps_url"] = maps.group(0)

    out["has_blog"] = bool(_RSS_RE.search(combined)) or any(
        "/blog" in h.lower() for h in hrefs)
    out["has_app"] = any(("apps.apple.com" in h.lower() or "play.google.com/store" in h.lower())
                         for h in hrefs)
    return out


# --------------------------------------------------------------------------- #
# D. Stack comercial (tecnologias)
# --------------------------------------------------------------------------- #

TECH_FINGERPRINTS = {
    # Analytics
    "ga4": [r"gtag\.js", r"G-[A-Z0-9]{6,}"],
    "google_tag_manager": [r"gtm\.js", r"GTM-[A-Z0-9]+"],
    "hotjar": [r"static\.hotjar\.com"],
    "clarity": [r"clarity\.ms"],
    # Chat
    "jivochat": [r"code\.jivosite\.com", r"code\.jivochat\.com"],
    "tidio": [r"code\.tidio\.co"],
    "zendesk": [r"static\.zdassets\.com"],
    "intercom": [r"widget\.intercom\.io"],
    # Pagamento
    "pagseguro": [r"stc\.pagseguro\.uol\.com\.br", r"pagseguro"],
    "mercado_pago": [r"sdk\.mercadopago\.com", r"mercadopago"],
    "stripe": [r"js\.stripe\.com"],
    "cielo": [r"cieloecommerce\.cielo\.com\.br"],
    "picpay": [r"picpay\.com"],
    # E-commerce
    "woocommerce": [r"wp-content/plugins/woocommerce"],
    "nuvemshop": [r"staticnuvem\.com", r"nuvemshop", r"tiendanube"],
    "vtex": [r"vtexcommercestable", r"vtexassets"],
    "tray": [r"traycorp\.com\.br"],
    "magento": [r"/static/version\d+/frontend", r"Magento_"],
    # Marketing
    "rd_station": [r"d335luupugsy2\.cloudfront\.net", r"rdstation"],
    "facebook_pixel": [r"fbevents\.js", r"fbq\s*\(\s*['\"]init['\"]"],
    "google_ads": [r"googleads\.g\.doubleclick\.net", r"googleadservices\.com"],
    "tiktok_pixel": [r"analytics\.tiktok\.com"],
    # Booking
    "omnibees": [r"omnibees\.com"],
    # Cookie consent
    "cookiebot": [r"cookiebot\.com"],
    "onetrust": [r"onetrust\.com", r"cookielaw\.org"],
    # Outros
    "recaptcha": [r"recaptcha/api\.js"],
    "wordpress": [r"wp-content/", r"wp-includes/"],
}

# tech -> categoria (para o JSONB agrupado)
_TECH_CATEGORY = {
    "ga4": "analytics", "google_tag_manager": "analytics", "hotjar": "analytics", "clarity": "analytics",
    "jivochat": "chat", "tidio": "chat", "zendesk": "chat", "intercom": "chat",
    "pagseguro": "payment", "mercado_pago": "payment", "stripe": "payment", "cielo": "payment", "picpay": "payment",
    "woocommerce": "ecommerce", "nuvemshop": "ecommerce", "vtex": "ecommerce", "tray": "ecommerce", "magento": "ecommerce",
    "rd_station": "marketing", "facebook_pixel": "marketing", "google_ads": "ads", "tiktok_pixel": "marketing",
    "omnibees": "booking",
    "cookiebot": "cookie_consent", "onetrust": "cookie_consent",
    "recaptcha": "security", "wordpress": "cms",
}

_TECH_COMPILED = {name: [re.compile(p, re.IGNORECASE) for p in pats]
                  for name, pats in TECH_FINGERPRINTS.items()}


def extract_technologies(html_pages: Dict[str, str], headers: Optional[dict] = None) -> dict:
    """Detecta tecnologias via fingerprints (case-insensitive) no HTML + headers,
    agrupadas por categoria: {'analytics': ['ga4'], 'payment': ['pagseguro'], ...}."""
    blob = "\n".join(html_pages.values())
    if headers:
        blob += "\n" + "\n".join(f"{k}: {v}" for k, v in headers.items())
    out: Dict[str, List[str]] = {}
    for name, regexes in _TECH_COMPILED.items():
        if any(rx.search(blob) for rx in regexes):
            cat = _TECH_CATEGORY.get(name, "other")
            out.setdefault(cat, []).append(name)
    return out


# --------------------------------------------------------------------------- #
# E. Infraestrutura (headers + DNS já coletados)
# --------------------------------------------------------------------------- #

_MX_PROVIDERS = {
    "google.com": "google_workspace", "googlemail.com": "google_workspace",
    "outlook.com": "microsoft_365", "microsoft.com": "microsoft_365",
    "locaweb.com.br": "locaweb", "titan.email": "titan", "hostinger": "hostinger",
    "zoho.com": "zoho", "zoho.eu": "zoho", "secureserver.net": "godaddy",
    "umbler.com": "umbler", "kinghost": "kinghost", "uol.com.br": "uol_host",
}
_NS_PROVIDERS = {
    "cloudflare.com": "cloudflare", "awsdns": "route53", "registro.br": "registro_br",
    "hostinger": "hostinger", "locaweb": "locaweb", "umbler": "umbler",
    "kinghost": "kinghost", "googledomains.com": "google_domains", "dns.br": "registro_br",
}


def _match_provider(records: List[str], table: dict) -> Optional[str]:
    for rec in records or []:
        low = str(rec).lower()
        for needle, name in table.items():
            if needle in low:
                return name
    return None


def _detect_cdn(headers: dict) -> Optional[str]:
    h = {str(k).lower(): str(v).lower() for k, v in (headers or {}).items()}
    if "cf-ray" in h or "cloudflare" in h.get("server", ""):
        return "cloudflare"
    if "x-amz-cf-id" in h or "cloudfront" in h.get("via", ""):
        return "cloudfront"
    if "x-fastly" in h or "fastly" in h.get("x-served-by", "") or "fastly" in h.get("via", ""):
        return "fastly"
    if "x-akamai-transformed" in h or "akamai" in h.get("server", ""):
        return "akamai"
    return None


def extract_infrastructure(headers: Optional[dict] = None,
                           mx_records: Optional[List[str]] = None,
                           ns_records: Optional[List[str]] = None,
                           certificate_authority: Optional[str] = None) -> dict:
    """Mapeia provedores de e-mail (MX), DNS (NS) e CDN (headers)."""
    return {
        "email_provider": _match_provider(mx_records or [], _MX_PROVIDERS),
        "dns_provider": _match_provider(ns_records or [], _NS_PROVIDERS),
        "cdn": _detect_cdn(headers or {}),
        "certificate_authority": certificate_authority,
    }


# --------------------------------------------------------------------------- #
# F. Score de maturidade digital (0-10)
# --------------------------------------------------------------------------- #

_FREE_EMAIL_DOMAINS = ("gmail.com", "hotmail.com", "outlook.com", "yahoo.com",
                       "yahoo.com.br", "bol.com.br", "uol.com.br", "live.com",
                       "icloud.com", "terra.com.br", "ig.com.br")


def calculate_maturity_score(profile: dict, security_score: Optional[int] = None) -> int:
    """Score de maturidade digital 0-10 a partir dos sinais do perfil."""
    tech = profile.get("technologies") or {}
    social_count = sum(1 for k in ("instagram", "facebook", "linkedin", "youtube", "tiktok")
                       if profile.get(k))
    email = (profile.get("commercial_email") or "").lower()
    email_domain = email.rsplit("@", 1)[-1] if "@" in email else ""

    pts = 0
    pts += 1 if (security_score is not None and security_score >= 80 and profile.get("_hsts")) else 0
    pts += 1 if tech.get("analytics") else 0
    pts += 1 if social_count >= 2 else 0
    pts += 1 if (tech.get("chat") or profile.get("whatsapp")) else 0
    pts += 1 if tech.get("payment") else 0
    pts += 1 if profile.get("has_blog") else 0
    pts += 1 if tech.get("cookie_consent") else 0
    pts += 1 if (email and email_domain and email_domain not in _FREE_EMAIL_DOMAINS) else 0
    pts += 1 if profile.get("_responsive") else 0
    pts += 1 if (security_score is not None and security_score >= 80) else 0
    return min(10, pts)


# --------------------------------------------------------------------------- #
# Camada 1 — Multi-page crawl
# --------------------------------------------------------------------------- #

_VIEWPORT_RE = re.compile(r"""<meta[^>]+name\s*=\s*['"]viewport['"]""", re.IGNORECASE)


async def crawl_contact_pages(url: str, homepage_html: Optional[str] = None,
                              max_pages: int = 8) -> Dict[str, str]:
    """Busca a homepage + páginas internas de contato (HTTP 200). Segue 1 nível de
    redirect (o fetch já faz follow_redirects). Rate limit 1 req/s por domínio."""
    pages: Dict[str, str] = {}
    root = base_url(url) + "/"
    if homepage_html is not None:
        pages["homepage"] = homepage_html
    else:
        try:
            resp = await fetch(root, method="GET", follow_redirects=True)
            if resp.status_code == 200:
                pages["homepage"] = resp.text
        except (httpx.HTTPError, OSError):
            pass

    for path in CONTACT_PATHS[:max_pages]:
        try:
            resp = await fetch(urljoin(root, path), method="GET", follow_redirects=True)
        except (httpx.HTTPError, OSError):
            continue
        if resp.status_code == 200 and resp.text:
            pages[path] = resp.text
    return pages


async def build_profile(url: str, homepage_html: Optional[str] = None,
                        headers: Optional[dict] = None, mx_records: Optional[List[str]] = None,
                        ns_records: Optional[List[str]] = None,
                        certificate_authority: Optional[str] = None,
                        security_score: Optional[int] = None) -> dict:
    """Orquestra o crawl + todos os parsers e devolve o perfil comercial completo
    (pronto para `site_profile`). Nunca levanta — degrada para campos vazios."""
    site_domain = registrable_domain(domain_of(url))
    pages = await crawl_contact_pages(url, homepage_html=homepage_html)
    combined = "\n".join(pages.values())

    contacts = extract_contacts(pages, site_domain)
    structured = extract_structured_data(combined)
    social = extract_social_links(pages)
    tech = extract_technologies(pages, headers)
    infra = extract_infrastructure(headers, mx_records, ns_records, certificate_authority)

    profile = {
        "company_name": structured.get("company_name"),
        "phone": contacts.get("phone") or structured.get("phone"),
        "whatsapp": contacts.get("whatsapp"),
        "address": structured.get("address") or contacts.get("address"),
        "cnpj": contacts.get("cnpj"),
        "commercial_email": contacts.get("email") or structured.get("email"),
        "business_hours": structured.get("business_hours"),
        "description": structured.get("description"),
        "logo_url": structured.get("logo_url"),
        "instagram": social.get("instagram"), "facebook": social.get("facebook"),
        "linkedin": social.get("linkedin"), "youtube": social.get("youtube"),
        "tiktok": social.get("tiktok"), "google_maps_url": social.get("google_maps_url"),
        "has_blog": social.get("has_blog", False), "has_app": social.get("has_app", False),
        "technologies": tech,
        "email_provider": infra.get("email_provider"),
        "cdn": infra.get("cdn"), "dns_provider": infra.get("dns_provider"),
        "certificate_authority": infra.get("certificate_authority"),
        "sector_hint": structured.get("sector"),
        "extraction_sources": sorted(pages.keys()) + (["schema_org"] if structured.get("company_name") else []),
    }
    # KL-67 — filtros de qualidade (telefone/endereço/descrição/redes) ANTES de gravar.
    apply_quality_filters(profile, site_domain)
    # sinais auxiliares para a maturidade
    profile["_hsts"] = bool(headers and any(k.lower() == "strict-transport-security" for k in headers))
    profile["_responsive"] = bool(_VIEWPORT_RE.search(combined))
    profile["maturity_score"] = calculate_maturity_score(profile, security_score)
    profile.pop("_hsts", None)
    profile.pop("_responsive", None)
    return profile
