"""KL-44 P5 — Indicadores TÉCNICOS de privacidade (varredura passiva).

⚠️ Isto NÃO é avaliação de conformidade LGPD nem certificação. São 8 **fatos técnicos**
observáveis por um único `GET` na página inicial (HTML + headers + links), apresentados
como indicadores/diagnóstico. Cada indicador cita o artigo da LGPD **como referência**,
não como atestado de conformidade.

O `privacy_score` (0–8 = quantos indicadores o site atende) é **INDEPENDENTE** do score de
segurança (0–100) — nunca se combinam: segurança é técnica pura; privacidade tem
componentes legais que a varredura não avalia.

Passivo por construção: um único GET (o mesmo caminho dos checks de segurança), zero
requests extras por indicador, zero payloads. Puro/testável: as funções recebem
`html`/`headers`/`links` e não tocam a rede.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

# Disclaimer legal obrigatório (regra inviolável KL-44 P5) — reexposto pela API/UI.
PRIVACY_DISCLAIMER = (
    "Este é um diagnóstico técnico automatizado baseado em verificações passivas. "
    "Não constitui assessoria jurídica e não substitui a avaliação de um advogado ou "
    "Encarregado de Proteção de Dados (DPO). Para conformidade completa com a LGPD, "
    "consulte um profissional qualificado."
)

_HREF_RE = re.compile(
    r'<a\b[^>]*\bhref\s*=\s*["\']([^"\']+)["\'][^>]*>(.*?)</a>',
    re.IGNORECASE | re.DOTALL)
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def extract_links(html: str) -> List[Tuple[str, str]]:
    """(href, texto-normalizado-lowercase) de cada <a> — heurística leve, sem parser DOM."""
    out: List[Tuple[str, str]] = []
    for m in _HREF_RE.finditer(html or ""):
        href = (m.group(1) or "").strip()
        text = _WS_RE.sub(" ", _TAG_RE.sub(" ", m.group(2) or "")).strip().lower()
        out.append((href, text))
    return out


def _result(cid: str, name: str, status: str, evidence: str, lgpd_ref: str,
            severity: str) -> Dict[str, Any]:
    return {"id": cid, "name": name, "status": status, "evidence": evidence,
            "lgpd_ref": lgpd_ref, "severity": severity}


# --------------------------------------------------------------------------- #
# Vocabulário dos indicadores
# --------------------------------------------------------------------------- #

_PRIVACY_PATHS = ("politica-de-privacidade", "privacy", "politica-privacidade", "lgpd",
                  "privacidade", "privacy-policy", "politica_privacidade", "politicaprivacidade")
_PRIVACY_TEXTS = ("política de privacidade", "politica de privacidade", "privacy policy",
                  "lgpd", "proteção de dados", "protecao de dados", "data protection")

_CMP_SCRIPTS = ("cookieyes", "onetrust", "cookiebot", "termly.io", "iubenda", "osano",
                "quantcast", "trustarc", "complianz", "cookie-consent", "cookieconsent",
                "klaro", "tarteaucitron", "cookiefirst", "usercentrics")
_CONSENT_CLASSES = ("cookie-banner", "cookie-consent", "cookie-notice", "cc-banner",
                    "consent-banner", "gdpr-banner", "lgpd-banner", "cookie-popup",
                    "consent-popup", "cookie-bar", "consent-bar", "cookie-modal")
_CONSENT_TEXTS = ("aceitar cookies", "cookies necessários", "cookies necessarios",
                  "preferências de cookies", "preferencias de cookies", "usamos cookies",
                  "este site utiliza cookies", "utilizamos cookies", "gerenciar cookies")

_TRACKING_COOKIES = ("_ga", "_gid", "_fbp", "_gcl_au", "_gat", "_hjid", "_pin_unauth",
                     "fr", "ide", "_gcl_aw", "_uetsid", "_clck")

_DSAR_PATHS = ("direitos", "dsar", "titular", "meus-dados", "solicitacao", "solicitação",
               "exclusao-de-dados", "portabilidade", "seus-direitos")
_DSAR_TEXTS = ("direitos do titular", "exercer seus direitos", "solicitar dados",
               "exclusão de dados", "exclusao de dados", "seus direitos", "portabilidade de dados",
               "solicitar exclusão", "solicitar exclusao")

_DPO_TEXTS = ("encarregado", "dpo", "data protection officer", "proteção de dados pessoais",
              "protecao de dados pessoais", "encarregado de proteção", "encarregado de protecao")

_COOKIE_PATHS = ("cookies", "politica-de-cookies", "cookie-policy", "politica-de-cookie",
                 "politica_cookies", "cookie-policy")
_COOKIE_TEXTS = ("política de cookies", "politica de cookies", "cookie policy")


def _link_hit(links: List[Tuple[str, str]], paths, texts) -> Optional[str]:
    for href, text in links:
        low = href.lower()
        if any(p in low for p in paths):
            return href[:120]
        if any(t in text for t in texts):
            return href[:120]
    return None


# --------------------------------------------------------------------------- #
# Os 8 indicadores (funções puras de html/headers/links/base_url)
# --------------------------------------------------------------------------- #

def check_privacy_policy(html: str, links) -> Dict[str, Any]:
    hit = _link_hit(links, _PRIVACY_PATHS, _PRIVACY_TEXTS)
    if hit:
        return _result("privacy_policy", "Política de Privacidade", "PASS",
                       f"Link encontrado: {hit}", "Art. 9°", "high")
    return _result("privacy_policy", "Política de Privacidade", "FAIL",
                   "Nenhum link para política de privacidade na página inicial.",
                   "Art. 9°", "high")


def check_cookie_consent(html: str) -> Dict[str, Any]:
    low = (html or "").lower()
    for s in _CMP_SCRIPTS:
        if s in low:
            return _result("cookie_consent", "Banner de Cookies", "PASS",
                           f"CMP detectado: {s}", "Art. 7° e 8°", "high")
    for c in _CONSENT_CLASSES:
        if c in low:
            return _result("cookie_consent", "Banner de Cookies", "PASS",
                           f"Elemento de consentimento: .{c}", "Art. 7° e 8°", "high")
    for t in _CONSENT_TEXTS:
        if t in low:
            return _result("cookie_consent", "Banner de Cookies", "PASS",
                           "Texto de consentimento de cookies presente.", "Art. 7° e 8°", "high")
    return _result("cookie_consent", "Banner de Cookies", "FAIL",
                   "Nenhum banner/CMP de consentimento de cookies detectado.",
                   "Art. 7° e 8°", "high")


def check_third_party_cookies(set_cookies: List[str]) -> Dict[str, Any]:
    """NEGATIVO: cookies de rastreio na resposta inicial (antes de consentimento) → FAIL."""
    found = []
    for raw in set_cookies or []:
        name = (raw.split("=", 1)[0] or "").strip().lower()
        if name in _TRACKING_COOKIES:
            found.append(name)
    if found:
        uniq = sorted(set(found))
        return _result("third_party_cookies", "Cookies de terceiros pré-consentimento",
                       "FAIL", f"Cookies de rastreio antes do consentimento: {', '.join(uniq)}",
                       "Art. 7°", "high")
    return _result("third_party_cookies", "Cookies de terceiros pré-consentimento", "PASS",
                   "Nenhum cookie de rastreio conhecido na resposta inicial.", "Art. 7°", "high")


def check_dsar_channel(html: str, links) -> Dict[str, Any]:
    hit = _link_hit(links, _DSAR_PATHS, _DSAR_TEXTS)
    if hit:
        return _result("dsar_channel", "Canal de direitos do titular", "PASS",
                       f"Canal encontrado: {hit}", "Art. 18°", "medium")
    return _result("dsar_channel", "Canal de direitos do titular", "FAIL",
                   "Nenhum canal visível para exercício de direitos do titular.",
                   "Art. 18°", "medium")


def check_dpo_info(html: str) -> Dict[str, Any]:
    low = (html or "").lower()
    for t in _DPO_TEXTS:
        if t in low:
            return _result("dpo_info", "Identificação do Encarregado (DPO)", "PASS",
                           "Menção a Encarregado/DPO na página.", "Art. 41°", "medium")
    return _result("dpo_info", "Identificação do Encarregado (DPO)", "FAIL",
                   "Sem menção ao Encarregado (DPO) na página inicial.", "Art. 41°", "medium")


def check_cookie_policy(html: str, links) -> Dict[str, Any]:
    hit = _link_hit(links, _COOKIE_PATHS, _COOKIE_TEXTS)
    if hit:
        return _result("cookie_policy", "Política de Cookies", "PASS",
                       f"Página de cookies: {hit}", "Guia ANPD (cookies)", "low")
    return _result("cookie_policy", "Política de Cookies", "FAIL",
                   "Sem política de cookies dedicada.", "Guia ANPD (cookies)", "low")


def check_https_forms(html: str, base_url: str) -> Dict[str, Any]:
    has_form = "<form" in (html or "").lower()
    is_https = (base_url or "").lower().startswith("https://")
    if not has_form:
        return _result("https_forms", "HTTPS em formulários", "PASS",
                       "Página inicial sem formulários de coleta.", "Art. 46°", "high")
    if is_https:
        return _result("https_forms", "HTTPS em formulários", "PASS",
                       "Formulário(s) servido(s) sobre HTTPS.", "Art. 46°", "high")
    return _result("https_forms", "HTTPS em formulários", "FAIL",
                   "Há formulário mas a página não usa HTTPS.", "Art. 46°", "high")


def check_form_security_headers(html: str, headers: Dict[str, str]) -> Dict[str, Any]:
    has_form = "<form" in (html or "").lower()
    h = {k.lower(): v for k, v in (headers or {}).items()}
    present = sum(1 for k in ("strict-transport-security", "content-security-policy",
                              "x-content-type-options") if k in h)
    if not has_form:
        return _result("form_security_headers", "Headers de segurança em formulários",
                       "PASS", "Página inicial sem formulários de coleta.", "Art. 46°", "medium")
    if present >= 2:
        return _result("form_security_headers", "Headers de segurança em formulários",
                       "PASS", f"{present}/3 headers de segurança presentes.", "Art. 46°", "medium")
    return _result("form_security_headers", "Headers de segurança em formulários",
                   "FAIL", f"Só {present}/3 headers de segurança em página com formulário.",
                   "Art. 46°", "medium")


# --------------------------------------------------------------------------- #
# Orquestração
# --------------------------------------------------------------------------- #

def analyze(html: str, headers: Dict[str, str], set_cookies: List[str],
            base_url: str) -> Dict[str, Any]:
    """Roda os 8 indicadores sobre um único snapshot (html/headers/cookies). Puro."""
    links = extract_links(html)
    checks = [
        check_privacy_policy(html, links),
        check_cookie_consent(html),
        check_third_party_cookies(set_cookies),
        check_dsar_channel(html, links),
        check_dpo_info(html),
        check_cookie_policy(html, links),
        check_https_forms(html, base_url),
        check_form_security_headers(html, headers),
    ]
    score = sum(1 for c in checks if c["status"] == "PASS")
    return {"score": score, "total": len(checks), "checks": checks,
            "disclaimer": PRIVACY_DISCLAIMER}


async def scan_privacy(url: str) -> Optional[Dict[str, Any]]:
    """Um único GET passivo (rate-limited pela `base.fetch`) → indicadores. None se falhar
    (fail-open: privacidade nunca derruba o scan de segurança)."""
    from .checks import base
    try:
        resp = await base.fetch(url, method="GET")
        html = resp.text if base.looks_like_html(resp) else ""
        try:
            set_cookies = resp.headers.get_list("set-cookie")  # httpx multi-valor
        except Exception:  # noqa: BLE001
            sc = resp.headers.get("set-cookie")
            set_cookies = [sc] if sc else []
        return analyze(html, dict(resp.headers), set_cookies, str(resp.url) or url)
    except Exception as exc:  # noqa: BLE001 - privacidade é best-effort
        print(f"[privacy] análise falhou {url}: {exc!r}", flush=True)
        return None
