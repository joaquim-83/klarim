"""KL-134 (Prompt 1/2) — Micro-ferramentas SEO: 5 ferramentas gratuitas de aquisição.

Cada ferramenta é um endpoint PÚBLICO (sem auth) que reusa checks/analisadores JÁ existentes
da engine (nunca reimplementa nem altera um check) e devolve JSON simplificado em PT-BR para
o visitante leigo. A única proteção é o rate limit (10/min por IP).

Fontes reusadas (só CHAMADAS, nunca alteradas):
  - SSL      → ``scanner.tls_analyzer.get_tls_info`` (o mesmo handshake dos checks 41–44)
  - Headers  → ``scanner.checks.base.fetch`` (headers da resposta HTTP)
  - LGPD     → ``scanner.privacy_checks.scan_privacy`` (os 8 indicadores técnicos de privacidade)
  - Tech     → ``scanner.tech_detector.detect_tech_stack`` (função pura, KL-75)
  - E-mail   → ``scanner.checks.dns_util`` + os seletores DKIM de ``check_22_dkim`` (SPF/DKIM/DMARC/MX)
  - Stats    → ``store.dashboard_summary``/``privacy_indicator_stats``/``get_tech_adoption`` (cache 24h)

Arquitetura testável: os ``build_*_response`` são funções PURAS (recebem o dado já buscado,
não tocam a rede); os endpoints fazem o I/O (com timeout) e delegam a montagem ao builder.
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException, Query, Request

from scanner.checks import base as _base
from scanner.checks import dns_util as _dns
from scanner.checks.check_22_dkim import DKIM_SELECTORS
from scanner import privacy_checks as _privacy
from scanner import tech_detector as _tech
from scanner.tls_analyzer import get_tls_info, WEAK_PROTOCOLS

router = APIRouter(prefix="/tools", tags=["tools"])
logger = logging.getLogger("tools")

# Rate limit (a única proteção destes endpoints públicos): 10 requests/min por IP.
TOOLS_RATE_LIMIT = 10
TOOLS_RATE_WINDOW = 60
# Timeout por request externo (não do endpoint) — o site alvo pode ser lento.
CHECK_TIMEOUT = 15.0
# TTL do cache de estatísticas agregadas.
_STATS_TTL = 86400


class ToolTimeout(Exception):
    """O site alvo não respondeu dentro do orçamento de tempo."""


# --------------------------------------------------------------------------- #
# Validação de URL / domínio
# --------------------------------------------------------------------------- #

# Um hostname de domínio válido (labels alfanuméricas + hífen, ao menos um ponto, TLD ≥ 2).
_DOMAIN_RE = re.compile(
    r"^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")


def _extract_host(url_or_domain: str) -> str:
    """Extrai o hostname (minúsculo, sem porta) de uma URL ou domínio cru."""
    raw = (url_or_domain or "").strip()
    if not raw:
        raise ValueError("empty URL")
    candidate = raw if raw.startswith(("http://", "https://")) else "https://" + raw
    host = (urlparse(candidate).hostname or "").lower().strip(".")
    return host


def validate_tool_url(url_or_domain: str) -> str:
    """Aceita ``example.com`` / ``https://example.com`` / ``http://example.com/path`` e devolve
    a URL normalizada com scheme (https:// se ausente). Levanta ``ValueError`` se inválido."""
    raw = (url_or_domain or "").strip()
    if not raw:
        raise ValueError("empty URL")
    host = _extract_host(raw)
    if not _DOMAIN_RE.match(host):
        raise ValueError(f"invalid domain: {url_or_domain!r}")
    if not raw.startswith(("http://", "https://")):
        return "https://" + raw
    return raw


def validate_tool_domain(value: str) -> str:
    """Como ``validate_tool_url`` mas devolve o DOMÍNIO cru (o tool de e-mail opera sobre DNS,
    não HTTP). Aceita domínio ou URL; levanta ``ValueError`` se inválido."""
    host = _extract_host(value)
    if not _DOMAIN_RE.match(host):
        raise ValueError(f"invalid domain: {value!r}")
    return host


# --------------------------------------------------------------------------- #
# Rate limiter (Redis; fail-open sem Redis) + timeout wrapper
# --------------------------------------------------------------------------- #

async def check_tools_rate_limit(redis, ip: str) -> Optional[int]:
    """INCR ``tools:rl:{ip}`` (EXPIRE 60 na 1ª). Devolve ``retry_after`` (s) se estourou o teto,
    senão ``None``. **Fail-open**: sem Redis (ou erro) → ``None`` (nunca bloqueia)."""
    if redis is None:
        return None
    key = f"tools:rl:{ip}"
    try:
        n = int(await redis.incr(key))
        if n == 1:
            await redis.expire(key, TOOLS_RATE_WINDOW)
            return None
        if n > TOOLS_RATE_LIMIT:
            ttl = await redis.ttl(key)
            return int(ttl) if ttl and int(ttl) > 0 else TOOLS_RATE_WINDOW
        return None
    except Exception:  # noqa: BLE001 — Redis instável nunca derruba o endpoint
        return None


async def run_check_with_timeout(check_fn, *args, timeout: float = CHECK_TIMEOUT):
    """Executa ``check_fn(*args)`` (coroutine) com timeout. Estouro → ``ToolTimeout`` com
    mensagem amigável."""
    try:
        return await asyncio.wait_for(check_fn(*args), timeout=timeout)
    except asyncio.TimeoutError:
        raise ToolTimeout(f"O site não respondeu em {int(timeout)} segundos.")


def _redis():
    """Cliente Redis do app (``api.main._cache.redis``) ou ``None`` (import deferido — o
    ``api.main`` importa este módulo no fim, então não pode ser importado no topo)."""
    try:
        import api.main as _m
        return _m._cache.redis if _m._cache is not None else None
    except Exception:  # noqa: BLE001
        return None


def _client_ip(request: Request) -> str:
    try:
        import api.main as _m
        return _m._client_ip(request)
    except Exception:  # noqa: BLE001
        return request.client.host if request.client else "unknown"


async def _enforce_rate_limit(request: Request) -> None:
    retry = await check_tools_rate_limit(_redis(), _client_ip(request))
    if retry is not None:
        raise HTTPException(
            status_code=429,
            detail="Limite de consultas excedido. Tente novamente em 1 minuto.",
            headers={"Retry-After": str(int(retry))})


# --------------------------------------------------------------------------- #
# Contexto (números REAIS/verificados da base Klarim — copy de aquisição)
# --------------------------------------------------------------------------- #

_CONTEXT: Dict[str, Dict[str, Any]] = {
    "ssl": {
        "stat": "30,8% dos sites brasileiros usam Cloudflare como CDN",
        "source": "Base Klarim com 115.849 sites",
    },
    "headers": {
        "stat": "Apenas 25,5% dos sites brasileiros têm todos os headers de segurança",
        "source": "Base Klarim com 115.849 sites",
    },
    "lgpd": {
        "stats": [
            "74,5% dos sites brasileiros NÃO têm política de privacidade",
            "83,6% NÃO têm banner de cookies",
            "99,1% NÃO têm canal de direitos do titular",
        ],
        "source": "Base Klarim — 19.846 sites analisados",
    },
    "tech": {
        "stats": [
            "11.959 sites brasileiros usam WordPress (20,2%)",
            "18.210 usam Cloudflare (30,8%)",
            "5.256 usam Google Analytics 4 (8,9%)",
        ],
        "source": "Base Klarim — 59.095 sites com dados de tecnologia",
    },
}


# --------------------------------------------------------------------------- #
# SSL — builder puro
# --------------------------------------------------------------------------- #

# Intermediários da Let's Encrypt (o cert só carrega o CN do emissor, não a organização).
_LE_INTERMEDIATES = {"R3", "R10", "R11", "R12", "R13", "R14",
                     "E1", "E5", "E6", "E7", "E8", "E9"}
_ISSUER_TOKENS = (
    ("let's encrypt", "Let's Encrypt"), ("google trust", "Google Trust Services"),
    ("gts ", "Google Trust Services"), ("digicert", "DigiCert"), ("sectigo", "Sectigo"),
    ("comodo", "Sectigo"), ("cloudflare", "Cloudflare"), ("amazon", "Amazon"),
    ("globalsign", "GlobalSign"), ("zerossl", "ZeroSSL"), ("microsoft", "Microsoft"),
    ("godaddy", "GoDaddy"), ("entrust", "Entrust"),
)


# Intermediários da Google Trust Services (o cert traz só o CN curto, ex.: "WE1"/"WR2").
_GTS_INTERMEDIATE_RE = re.compile(r"^w[er]\d+$", re.IGNORECASE)


def _friendly_issuer(issuer_cn: Optional[str]) -> str:
    if not issuer_cn:
        return "Desconhecido"
    if issuer_cn in _LE_INTERMEDIATES:
        return "Let's Encrypt"
    if _GTS_INTERMEDIATE_RE.match(issuer_cn):
        return "Google Trust Services"
    low = issuer_cn.lower()
    for token, label in _ISSUER_TOKENS:
        if token in low:
            return label
    return issuer_cn


def _ssl_grade(verified: bool, protocol: Optional[str], weak_cipher: Optional[str],
               days_remaining: Optional[int], self_signed: bool) -> str:
    if (not verified) or self_signed or (days_remaining is not None and days_remaining < 0):
        return "F"
    if protocol in WEAK_PROTOCOLS or weak_cipher:
        return "C"
    if protocol == "TLSv1.3":
        return "A"
    return "B"


def _pt_date(dt: datetime) -> str:
    return dt.strftime("%d/%m/%Y")


def build_ssl_response(domain: str, info: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Monta a resposta do tool SSL a partir do dict de ``get_tls_info`` (puro)."""
    if not info or not info.get("ok"):
        return {
            "domain": domain, "valid": False,
            "error": "Não foi possível estabelecer uma conexão segura (HTTPS) com o site.",
            "checks": [{"name": "Conexão TLS", "status": "fail",
                        "detail": "O site não respondeu a uma conexão HTTPS."}],
        }

    cert = info.get("cert") or {}
    verified = bool(info.get("verified"))
    self_signed = bool(cert.get("self_signed"))
    protocol = info.get("protocol")
    weak_cipher = info.get("weak_cipher")

    not_after = cert.get("not_after")
    days_remaining: Optional[int] = None
    expires_at: Optional[str] = None
    expiry_pt: Optional[str] = None
    if isinstance(not_after, datetime):
        na = not_after if not_after.tzinfo else not_after.replace(tzinfo=timezone.utc)
        days_remaining = (na - datetime.now(timezone.utc)).days
        expires_at = na.date().isoformat()
        expiry_pt = _pt_date(na)

    expired = days_remaining is not None and days_remaining < 0
    valid = verified and not expired and not self_signed

    # Checks legíveis (status "pass"/"fail").
    cert_ok = verified and not expired and not self_signed
    if expired:
        cert_detail = f"Expirou em {expiry_pt}" if expiry_pt else "Certificado expirado"
    elif self_signed:
        cert_detail = "Certificado autoassinado (não emitido por autoridade confiável)"
    elif cert_ok:
        cert_detail = f"Válido até {expiry_pt}" if expiry_pt else "Certificado válido"
    else:
        cert_detail = info.get("verify_error") or "Certificado não confiável"

    if protocol == "TLSv1.3":
        proto_detail = "TLSv1.3 (recomendado)"
    elif protocol == "TLSv1.2":
        proto_detail = "TLSv1.2 (aceitável)"
    elif protocol:
        proto_detail = f"{protocol} (obsoleto)"
    else:
        proto_detail = "Protocolo TLS não identificado"

    checks = [
        {"name": "Certificado válido", "status": "pass" if cert_ok else "fail",
         "detail": cert_detail},
        {"name": "Protocolo TLS",
         "status": "pass" if protocol not in WEAK_PROTOCOLS and protocol else "fail",
         "detail": proto_detail},
        {"name": "Cadeia completa", "status": "pass" if verified else "fail",
         "detail": "Cadeia de certificados válida" if verified
                   else "Cadeia incompleta ou não confiável"},
    ]

    if not valid:
        if expired:
            error = f"Certificado expirado há {abs(days_remaining)} dia(s)"
        elif self_signed:
            error = "Certificado autoassinado (não emitido por autoridade confiável)"
        elif not verified:
            error = info.get("verify_error") or "Certificado inválido ou não confiável"
        else:
            error = "Certificado inválido"
        return {
            "domain": domain, "valid": False, "error": error,
            "days_remaining": days_remaining, "expires_at": expires_at,
            "issuer": _friendly_issuer(cert.get("issuer_cn")), "protocol": protocol,
            "checks": checks, "context": _CONTEXT["ssl"],
        }

    return {
        "domain": domain, "valid": True, "days_remaining": days_remaining,
        "issuer": _friendly_issuer(cert.get("issuer_cn")), "protocol": protocol,
        "expires_at": expires_at,
        "grade": _ssl_grade(verified, protocol, weak_cipher, days_remaining, self_signed),
        "checks": checks, "context": _CONTEXT["ssl"],
    }


# --------------------------------------------------------------------------- #
# Headers — builder puro
# --------------------------------------------------------------------------- #

# (nome canônico, chave lowercase, importância, explicação em PT simples, informational).
# KL-164: `informational=True` NÃO conta no score (header legado/deprecado — X-XSS-Protection foi
# substituído pelo CSP e sua ausência não é uma falha). É exibido como informativo, não como FAIL.
_SECURITY_HEADERS: Tuple[Tuple[str, str, str, str, bool], ...] = (
    ("Content-Security-Policy", "content-security-policy", "alta",
     "Define quais recursos podem ser carregados. Previne XSS e injeção de código.", False),
    ("Strict-Transport-Security", "strict-transport-security", "alta",
     "Força HTTPS. Protege contra ataques de downgrade.", False),
    ("X-Content-Type-Options", "x-content-type-options", "média",
     "Impede que o navegador interprete arquivos com tipo diferente do declarado.", False),
    ("X-Frame-Options", "x-frame-options", "alta",
     "Impede que seu site seja embutido em iframes de terceiros (clickjacking).", False),
    ("Referrer-Policy", "referrer-policy", "média",
     "Controla quais informações de origem são enviadas ao navegar para outros sites.", False),
    ("Permissions-Policy", "permissions-policy", "média",
     "Restringe o acesso a recursos do navegador (câmera, microfone, geolocalização).", False),
    ("X-XSS-Protection", "x-xss-protection", "informativo",
     "Header legado, deprecado e substituído pelo Content-Security-Policy nos navegadores "
     "modernos. Sua ausência NÃO conta como falha.", True),
)


def build_headers_response(domain: str, headers: Dict[str, str]) -> Dict[str, Any]:
    h = {str(k).lower(): v for k, v in (headers or {}).items()}
    out: List[Dict[str, Any]] = []
    present_count = 0
    scored_total = 0
    for name, key, importance, explanation, informational in _SECURITY_HEADERS:
        value = h.get(key)
        present = value is not None
        if not informational:                      # KL-164: header informativo não pontua
            scored_total += 1
            if present:
                present_count += 1
        entry: Dict[str, Any] = {"name": name, "present": present,
                                 "importance": importance, "explanation": explanation}
        if informational:
            entry["informational"] = True
        if present:
            entry["value"] = str(value)
        out.append(entry)
    return {"domain": domain, "score": f"{present_count}/{scored_total}",
            "headers": out, "context": _CONTEXT["headers"]}


# --------------------------------------------------------------------------- #
# LGPD — builder puro (8 indicadores reais de scanner.privacy_checks)
# --------------------------------------------------------------------------- #

_LGPD_STATUS_MAP = {"PASS": "pass", "FAIL": "fail", "INCONCLUSO": "warn"}


def _lgpd_grade(score: int, total: int) -> str:
    if total <= 0:
        return "Indeterminado"
    if score >= total:
        return "Adequado"
    if score >= math.ceil(total * 0.7):
        return "Parcialmente adequado"
    if score >= math.ceil(total * 0.35):
        return "Atenção necessária"
    return "Inadequado"


def build_lgpd_response(domain: str, result: Dict[str, Any]) -> Dict[str, Any]:
    checks = (result or {}).get("checks") or []
    indicators = [
        {"name": c.get("name"), "status": _LGPD_STATUS_MAP.get(c.get("status"), "warn"),
         "explanation": c.get("evidence", "")}
        for c in checks
    ]
    total = int((result or {}).get("total") or len(checks) or 8)
    score = int((result or {}).get("score") or 0)
    return {
        "domain": domain, "score": f"{score}/{total}", "grade": _lgpd_grade(score, total),
        "indicators": indicators, "disclaimer": (result or {}).get("disclaimer"),
        "context": _CONTEXT["lgpd"],
    }


# --------------------------------------------------------------------------- #
# Tech — builder puro (nomes/categorias amigáveis)
# --------------------------------------------------------------------------- #

_TECH_NAME_MAP = {
    "nginx": "Nginx", "apache": "Apache", "litespeed": "LiteSpeed", "iis": "IIS",
    "openresty": "OpenResty", "cloudflare": "Cloudflare", "cloudflare_cdn": "Cloudflare",
    "cloudflare_ssl": "Cloudflare", "cloudflare_turnstile": "Cloudflare Turnstile",
    "aws_cloudfront": "AWS CloudFront", "fastly": "Fastly", "cdnjs": "cdnjs",
    "php": "PHP", "asp.net": "ASP.NET", "express": "Express", "nextjs": "Next.js",
    "laravel": "Laravel", "java": "Java", "wordpress": "WordPress", "shopify": "Shopify",
    "wix": "Wix", "google_analytics_4": "Google Analytics 4",
    "google_analytics_ua": "Google Analytics (Universal)", "google_analytics": "Google Analytics",
    "google_tag_manager": "Google Tag Manager", "facebook_pixel": "Meta Pixel",
    "hotjar": "Hotjar", "mercado_pago": "Mercado Pago", "stripe": "Stripe",
    "pagseguro": "PagSeguro", "recaptcha": "reCAPTCHA",
}

_CATEGORY_MAP = {
    "cms": "CMS", "cdn": "CDN", "analytics": "Analytics", "ecommerce": "E-commerce",
    "pagamento": "Pagamento", "chat": "Chat", "seguranca": "Segurança", "email": "E-mail",
    "dns": "DNS", "social": "Social", "marketing": "Marketing", "infra": "Infraestrutura",
}
_HOSTING_SUB = {"webserver": "Servidor", "backend": "Linguagem", "framework": "Framework"}


def _display_tech_name(slug: str) -> str:
    if not slug:
        return "Desconhecido"
    if slug in _TECH_NAME_MAP:
        return _TECH_NAME_MAP[slug]
    return slug.replace("_", " ").replace("-", " ").title()


def _display_category(category: Optional[str], subcategory: Optional[str]) -> str:
    if category == "hosting":
        return _HOSTING_SUB.get(subcategory or "", "Hospedagem")
    if category in _CATEGORY_MAP:
        return _CATEGORY_MAP[category]
    return (category or "Outro").replace("_", " ").title()


def build_tech_response(domain: str, result: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    techs = (result or {}).get("technologies") or []
    out: List[Dict[str, Any]] = []
    for t in techs:
        entry: Dict[str, Any] = {
            "name": _display_tech_name(t.get("name", "")),
            "category": _display_category(t.get("category"), t.get("subcategory")),
        }
        version = t.get("version")
        if version:
            entry["version"] = version
        out.append(entry)
    resp: Dict[str, Any] = {"domain": domain, "technologies": out, "context": _CONTEXT["tech"]}
    if not out:
        resp["message"] = ("Nenhuma tecnologia identificada. O site pode usar tecnologias "
                           "que não deixam sinais detectáveis.")
    return resp


# --------------------------------------------------------------------------- #
# E-mail — builder puro (mesma lógica dos checks 21/22/23, sobre DNS já resolvido)
# --------------------------------------------------------------------------- #

def _spf_record(txt_records: Optional[List[str]]) -> Optional[str]:
    for r in txt_records or []:
        if r.lower().startswith("v=spf1"):
            return r
    return None


def _dmarc_records(txt_records: Optional[List[str]]) -> List[str]:
    return [r.strip() for r in (txt_records or []) if r.strip().lower().startswith("v=dmarc1")]


def build_email_response(domain: str, txt_records: Optional[List[str]],
                         dkim_selector: Optional[str], dmarc_txt: Optional[List[str]],
                         mx_records: Optional[List[str]]) -> Dict[str, Any]:
    records: List[Dict[str, Any]] = []

    # SPF (check 21) — v=spf1; +all = tão ruim quanto ausente; -all/~all = ok.
    spf = _spf_record(txt_records)
    if spf and "+all" not in spf.lower():
        spf_status = "pass" if ("-all" in spf.lower() or "~all" in spf.lower()) else "fail"
    else:
        spf_status = "fail"
    records.append({
        "name": "SPF", "status": spf_status, "value": spf,
        "explanation": "Define quais servidores podem enviar email pelo seu domínio",
    })

    # DKIM (check 22) — algum seletor comum com registro → pass.
    dkim = {
        "name": "DKIM", "status": "pass" if dkim_selector else "fail",
        "explanation": "Assina digitalmente os emails para provar que são legítimos",
    }
    if dkim_selector:
        dkim["detail"] = f"Seletor '{dkim_selector}' encontrado"
    records.append(dkim)

    # DMARC (check 23) — único registro com p=quarantine/reject → pass.
    dmarc_list = _dmarc_records(dmarc_txt)
    dmarc_value = dmarc_list[0] if dmarc_list else None
    if len(dmarc_list) > 1:
        dmarc_status = "fail"
    elif dmarc_value:
        m = re.search(r"p\s*=\s*(none|quarantine|reject)", dmarc_value, re.IGNORECASE)
        policy = (m.group(1).lower() if m else "none")
        dmarc_status = "pass" if policy in ("quarantine", "reject") else "fail"
    else:
        dmarc_status = "fail"
    dmarc_entry: Dict[str, Any] = {
        "name": "DMARC", "status": dmarc_status, "value": dmarc_value,
        "explanation": ("Define o que fazer com emails que falham SPF/DKIM. "
                        "Sem DMARC, seus emails podem ir para spam."),
    }
    if dmarc_status == "fail":
        dmarc_entry["recommendation"] = (
            f"Adicione um registro DNS TXT: _dmarc.{domain} com "
            f"v=DMARC1; p=quarantine; rua=mailto:dmarc@{domain}")
    records.append(dmarc_entry)

    # MX — presença de servidores de recebimento.
    mx_ok = bool(mx_records)
    records.append({
        "name": "MX Records", "status": "pass" if mx_ok else "fail",
        "value": list(mx_records) if mx_records else None,
        "explanation": "Servidores que recebem email para este domínio",
    })

    score = sum(1 for r in records if r["status"] == "pass")
    return {"domain": domain, "score": f"{score}/{len(records)}", "records": records}


# --------------------------------------------------------------------------- #
# I/O helpers (com timeout) — os endpoints delegam a montagem aos builders puros
# --------------------------------------------------------------------------- #

async def _probe_dkim(domain: str) -> Optional[str]:
    """Sonda os seletores DKIM comuns em paralelo; devolve o 1º (na ordem da lista) com registro."""
    async def _one(selector: str) -> Optional[str]:
        recs = await asyncio.to_thread(_dns.resolve_txt, f"{selector}._domainkey.{domain}", 4.0)
        for r in recs or []:
            low = r.lower()
            if "v=dkim1" in low or "p=" in low:
                return selector
        return None

    results = await asyncio.gather(*[_one(s) for s in DKIM_SELECTORS])
    return next((r for r in results if r), None)


async def _tech_io(norm_url: str, host: str) -> Dict[str, Any]:
    resp = await _base.fetch(norm_url, method="GET")
    html = resp.text if _base.looks_like_html(resp) else ""
    headers = dict(resp.headers)
    mx = await asyncio.to_thread(_dns.resolve_mx, host, 5.0)
    ns = await asyncio.to_thread(_dns.resolve_ns, host, 5.0)
    txt = await asyncio.to_thread(_dns.resolve_txt, host, 5.0)
    dns = {"mx": mx or [], "ns": ns or [], "txt": txt or []}
    # KL-165: passa o host p/ validar a evidência de plataforma como same-origin.
    return _tech.detect_tech_stack(headers, html, dns, {}, domain=host)


async def _email_io(domain: str):
    txt = await asyncio.to_thread(_dns.resolve_txt, domain, 5.0)
    dmarc = await asyncio.to_thread(_dns.resolve_txt, f"_dmarc.{domain}", 5.0)
    mx = await asyncio.to_thread(_dns.resolve_mx, domain, 5.0)
    selector = await _probe_dkim(domain)
    return txt, selector, dmarc, mx


# --------------------------------------------------------------------------- #
# Stats agregadas (cache Redis 24h)
# --------------------------------------------------------------------------- #

def _fail_pct(indicators: Dict[str, Any], key: str) -> float:
    d = (indicators or {}).get(key) or {}
    p = int(d.get("pass", 0))
    f = int(d.get("fail", 0))
    tot = p + f
    return round(f * 100 / tot, 1) if tot else 0.0


def _adoption_pct(a: Optional[Dict[str, Any]]) -> float:
    return round(float((a or {}).get("adoption_rate", 0) or 0) * 100, 1)


async def _compute_stats() -> Dict[str, Any]:
    from discovery.store import get_target_store
    store = get_target_store()
    summary = await store.dashboard_summary()
    priv = await store.privacy_indicator_stats()
    wp = await store.get_tech_adoption("wordpress")
    cf = await store.get_tech_adoption("cloudflare")
    ga = await store.get_tech_adoption("google_analytics_4")
    indicators = (priv or {}).get("indicators") or {}
    return {
        "total_sites": int((summary.get("targets") or {}).get("total", 0)),
        "total_profiles": int((summary.get("profiles") or {}).get("total", 0)),
        "total_scans": int((summary.get("scans") or {}).get("total", 0)),
        "privacy": {
            "scanned": int((priv or {}).get("scanned", 0)),
            "privacy_policy_fail_pct": _fail_pct(indicators, "privacy_policy"),
            "cookie_consent_fail_pct": _fail_pct(indicators, "cookie_consent"),
            "dsar_fail_pct": _fail_pct(indicators, "dsar_channel"),
            "dpo_fail_pct": _fail_pct(indicators, "dpo_info"),
            "cookie_policy_fail_pct": _fail_pct(indicators, "cookie_policy"),
        },
        "tech": {
            "wordpress_count": int((wp or {}).get("sites_with_tech", 0)),
            "wordpress_pct": _adoption_pct(wp),
            "cloudflare_count": int((cf or {}).get("sites_with_tech", 0)),
            "cloudflare_pct": _adoption_pct(cf),
            "ga4_count": int((ga or {}).get("sites_with_tech", 0)),
            "ga4_pct": _adoption_pct(ga),
            "tech_base": int((wp or {}).get("total_sites", 0)),
        },
        "cached_at": datetime.now(timezone.utc).isoformat(),
    }


# --------------------------------------------------------------------------- #
# Endpoints
# --------------------------------------------------------------------------- #

_INVALID_URL_MSG = "URL inválida. Informe um domínio como 'example.com'."


@router.get("/ssl")
async def tool_ssl(request: Request, url: str = Query(None)):
    await _enforce_rate_limit(request)
    if not url:
        raise HTTPException(400, "Informe a URL do site (ex.: ?url=example.com).")
    try:
        norm = validate_tool_url(url)
    except ValueError:
        raise HTTPException(400, _INVALID_URL_MSG)
    host = _base.domain_of(norm)
    try:
        info = await run_check_with_timeout(get_tls_info, host)
    except ToolTimeout as exc:
        raise HTTPException(504, str(exc))
    return build_ssl_response(host, info)


@router.get("/headers")
async def tool_headers(request: Request, url: str = Query(None)):
    await _enforce_rate_limit(request)
    if not url:
        raise HTTPException(400, "Informe a URL do site (ex.: ?url=example.com).")
    try:
        norm = validate_tool_url(url)
    except ValueError:
        raise HTTPException(400, _INVALID_URL_MSG)
    host = _base.domain_of(norm)

    async def _io():
        resp = await _base.fetch(norm, method="GET")
        return dict(resp.headers)

    try:
        headers = await run_check_with_timeout(_io)
    except ToolTimeout as exc:
        raise HTTPException(504, str(exc))
    except Exception:  # noqa: BLE001 — falha de conexão vira erro amigável
        raise HTTPException(502, "Não foi possível acessar o site.")
    return build_headers_response(host, headers)


@router.get("/lgpd")
async def tool_lgpd(request: Request, url: str = Query(None)):
    await _enforce_rate_limit(request)
    if not url:
        raise HTTPException(400, "Informe a URL do site (ex.: ?url=example.com).")
    try:
        norm = validate_tool_url(url)
    except ValueError:
        raise HTTPException(400, _INVALID_URL_MSG)
    host = _base.domain_of(norm)
    try:
        result = await run_check_with_timeout(_privacy.scan_privacy, norm)
    except ToolTimeout as exc:
        raise HTTPException(504, str(exc))
    if not result:
        raise HTTPException(502, "Não foi possível acessar o site.")
    return build_lgpd_response(host, result)


@router.get("/tech")
async def tool_tech(request: Request, url: str = Query(None)):
    await _enforce_rate_limit(request)
    if not url:
        raise HTTPException(400, "Informe a URL do site (ex.: ?url=example.com).")
    try:
        norm = validate_tool_url(url)
    except ValueError:
        raise HTTPException(400, _INVALID_URL_MSG)
    host = _base.domain_of(norm)
    try:
        result = await run_check_with_timeout(_tech_io, norm, host)
    except ToolTimeout as exc:
        raise HTTPException(504, str(exc))
    except Exception:  # noqa: BLE001
        raise HTTPException(502, "Não foi possível acessar o site.")
    return build_tech_response(host, result)


@router.get("/email")
async def tool_email(request: Request, domain: str = Query(None)):
    await _enforce_rate_limit(request)
    if not domain:
        raise HTTPException(400, "Informe o domínio (ex.: ?domain=example.com).")
    try:
        dom = validate_tool_domain(domain)
    except ValueError:
        raise HTTPException(400, "Domínio inválido. Informe algo como 'example.com'.")
    try:
        txt, selector, dmarc, mx = await run_check_with_timeout(_email_io, dom)
    except ToolTimeout as exc:
        raise HTTPException(504, str(exc))
    return build_email_response(dom, txt, selector, dmarc, mx)


@router.get("/stats")
async def tool_stats(request: Request):
    await _enforce_rate_limit(request)
    redis = _redis()
    if redis is not None:
        try:
            cached = await redis.get("tools:stats")
            if cached:
                return json.loads(cached)
        except Exception:  # noqa: BLE001
            pass
    data = await _compute_stats()
    if redis is not None:
        try:
            await redis.set("tools:stats", json.dumps(data), ex=_STATS_TTL)
        except Exception:  # noqa: BLE001
            pass
    return data
