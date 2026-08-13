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
from urllib.parse import urljoin, urlparse

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
# KL-164 — DSAR/DPO em MÚLTIPLAS páginas (não só a homepage)
# --------------------------------------------------------------------------- #
# Muitos sites (incluindo o klarim.net) têm o canal de direitos e o Encarregado em
# páginas dedicadas (/privacidade, /lgpd), não na homepage. Os dois indicadores passam
# a procurar também nessas páginas — buscadas SÓ quando a homepage falha, com teto e
# early-exit (passivo, rate-limited pela `base.fetch`; fail-open).

# Ordenadas por valor (privacidade/lgpd primeiro → o klarim.net resolve em 1-2 fetches).
_DSAR_EXTRA_PATHS = ("privacidade", "lgpd", "politica-de-privacidade", "contato", "direitos")
_DPO_EXTRA_PATHS = ("privacidade", "politica-de-privacidade", "sobre", "contato")
# Vocabulário de links (href/texto) da homepage que apontam para um canal de direitos/DPO.
_RIGHTS_LINK_HINTS = ("direitos", "lgpd", "titular", "dsar", "exercer", "encarregado",
                      "proteção de dados", "protecao de dados", "privacidade")
# Teto de páginas extras buscadas por scan (bounded — não estoura o tempo do scan).
_PRIVACY_EXTRA_MAX = 6
# Teto de links do footer (evidência específica do site) considerados por scan.
_PRIVACY_FOOTER_MAX = 3


def _dsar_signal(html: str, links, path: str = "") -> bool:
    """True se a página evidencia um canal de direitos do titular. Sinais: link/texto de
    direitos (`_DSAR_*`), e-mail de privacidade/DPO, ou `<form>` numa página DEDICADA a
    direitos (evita falso-positivo de um form genérico de /contato)."""
    if _link_hit(links, _DSAR_PATHS, _DSAR_TEXTS):
        return True
    low = (html or "").lower()
    if any(t in low for t in _DSAR_TEXTS):
        return True
    if re.search(r'mailto:[^"\'>\s]*(dpo|privacidade|lgpd|encarregado|protecao)', low):
        return True
    p = (path or "").lower().strip("/")
    if "<form" in low and any(k in p for k in ("lgpd", "direitos", "dsar", "titular")):
        return True
    return False


def _dpo_signal(html: str) -> bool:
    """True se a página menciona o Encarregado/DPO (mesmo vocabulário do check homepage)."""
    low = (html or "").lower()
    return any(t in low for t in _DPO_TEXTS)


def _privacy_candidate_urls(base_url: str, links, need_dsar: bool, need_dpo: bool,
                            max_pages: int = _PRIVACY_EXTRA_MAX) -> List[str]:
    """URLs internas a sondar quando a homepage falha DSAR/DPO (puro/testável). Une os paths
    fixos com os links da homepage cujo href/texto sugere direitos/DPO; só mesma origem;
    dedupe preservando ordem; teto `max_pages`."""
    origin = urlparse(base_url or "")
    urls: List[str] = []
    seen = set()

    # Links do footer que apontam para direitos/DPO vêm PRIMEIRO — são a evidência específica
    # do próprio site (mais provável de acertar que um path adivinhado), só mesma origem.
    footer = 0
    for href, text in links or []:
        if footer >= _PRIVACY_FOOTER_MAX:
            break
        hay = f"{href} {text}".lower()
        if not any(h in hay for h in _RIGHTS_LINK_HINTS):
            continue
        pu = urlparse(urljoin(base_url, href))
        if pu.scheme not in ("http", "https") or pu.netloc != origin.netloc:
            continue
        u = pu._replace(fragment="", query="").geturl()
        if u not in seen:
            seen.add(u)
            urls.append(u)
            footer += 1

    # Depois, os paths fixos de maior valor (privacidade/lgpd primeiro).
    paths: List[str] = []
    if need_dsar:
        paths += list(_DSAR_EXTRA_PATHS)
    if need_dpo:
        paths += list(_DPO_EXTRA_PATHS)
    for p in paths:
        u = urljoin(base_url, p)
        if u not in seen:
            seen.add(u)
            urls.append(u)
    return urls[:max_pages]


def augment_privacy_checks(checks: List[Dict[str, Any]],
                           pages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Reavalia `dsar_channel`/`dpo_info` contra páginas extras JÁ buscadas (puro/testável).
    `pages`: lista de ``{path, html, links?}``. Muta `checks` in-place (só faz upgrade
    FAIL→PASS; nunca rebaixa PASS→FAIL) e devolve a lista. O chamador recomputa o score."""
    idx = {c["id"]: c for c in checks}
    dsar = idx.get("dsar_channel")
    dpo = idx.get("dpo_info")
    for pg in pages:
        html = pg.get("html") or ""
        links = pg.get("links")
        if links is None:
            links = extract_links(html)
        path = pg.get("path") or ""
        where = path or "página interna"
        if dsar is not None and dsar["status"] != "PASS" and _dsar_signal(html, links, path):
            dsar["status"] = "PASS"
            dsar["evidence"] = f"Canal de direitos encontrado em {where}."
        if dpo is not None and dpo["status"] != "PASS" and _dpo_signal(html):
            dpo["status"] = "PASS"
            dpo["evidence"] = f"Encarregado (DPO) mencionado em {where}."
    return checks


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
    """GET passivo da homepage → 8 indicadores. KL-164: se DSAR/DPO falham na homepage,
    sonda páginas internas (/privacidade, /lgpd, /contato, /sobre, /direitos + links do
    footer) — bounded (`_PRIVACY_EXTRA_MAX`), com early-exit e fail-open. Tudo rate-limited
    pela `base.fetch`. None se a homepage falhar (privacidade nunca derruba o scan)."""
    from .checks import base
    try:
        resp = await base.fetch(url, method="GET")
        html = resp.text if base.looks_like_html(resp) else ""
        try:
            set_cookies = resp.headers.get_list("set-cookie")  # httpx multi-valor
        except Exception:  # noqa: BLE001
            sc = resp.headers.get("set-cookie")
            set_cookies = [sc] if sc else []
        base_url = str(resp.url) or url
        result = analyze(html, dict(resp.headers), set_cookies, base_url)
    except Exception as exc:  # noqa: BLE001 - privacidade é best-effort
        print(f"[privacy] análise falhou {url}: {exc!r}", flush=True)
        return None

    # KL-164 — DSAR/DPO em páginas internas (só quando a homepage falhou).
    checks = result["checks"]
    idx = {c["id"]: c for c in checks}
    need_dsar = idx.get("dsar_channel", {}).get("status") != "PASS"
    need_dpo = idx.get("dpo_info", {}).get("status") != "PASS"
    if need_dsar or need_dpo:
        try:
            links = extract_links(html)
            for u in _privacy_candidate_urls(base_url, links, need_dsar, need_dpo):
                if not (need_dsar or need_dpo):
                    break
                try:
                    r2 = await base.fetch(u, method="GET")
                except Exception:  # noqa: BLE001 - página extra é best-effort
                    continue
                if r2.status_code >= 400 or not base.looks_like_html(r2):
                    continue
                page = {"path": urlparse(str(r2.url)).path or u, "html": r2.text}
                augment_privacy_checks(checks, [page])
                need_dsar = idx["dsar_channel"]["status"] != "PASS"
                need_dpo = idx["dpo_info"]["status"] != "PASS"
            result["score"] = sum(1 for c in checks if c["status"] == "PASS")
        except Exception as exc:  # noqa: BLE001 - augmentação nunca derruba o resultado base
            print(f"[privacy] multipágina falhou {url}: {exc!r}", flush=True)
    return result
