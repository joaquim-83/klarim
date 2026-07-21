"""Check 30 — Componentes com vulnerabilidades conhecidas (KL-33).

Detecta **versões** de bibliotecas JS e CMS a partir do HTML servido (passivo:
`<script src>`, conteúdo inline, meta generator, `?ver=`) e dos headers (PHP,
servidor), e cruza com a base **Retire.js** (`scanner.cve_db`) para listar CVEs
conhecidos. É o achado mais acionável do scanner — "jQuery 2.1.4 com 12
vulnerabilidades conhecidas" é concreto, ao contrário de "falta um header".

**Severidade dinâmica** pelo maior CVSS/severidade dos CVEs encontrados. Status:
FAIL se algum componente tem CVE; PASS se detectou componente(s) cobertos pela base
e nenhum é vulnerável; INCONCLUSO se não deu para avaliar (nenhuma versão detectada,
ou só componentes que a base não cobre — ex.: WordPress com NVD desligado).

100% passivo: só um GET na homepage (o mesmo que os outros checks fazem). Nenhum
payload, nenhuma versão é "sondada" ativamente — tudo vem do que o site já entrega.
"""

from __future__ import annotations

import re

import httpx

from .base import (
    CheckResult,
    Status,
    Severity,
    fetch,
    with_scheme,
    extract_script_refs,
    content_guard,
)
from ..cve_db import get_cve_db, severity_from_cves, max_cvss, NVD_ENABLED

ORDER = 30
CHECK_ID = "check_30_vulnerable_components"
NAME = "Componentes com vulnerabilidades conhecidas"

# Classificação de compliance (KL-34/35) — também no classifications.py.
OWASP = "A06:2025 Vulnerable and Outdated Components"
CWE = "CWE-1104"
LGPD = "Art. 46"

# Só os primeiros 50KB do HTML para banners de versão inline (spec KL-33).
_INLINE_LIMIT = 50_000


def _c(patterns):
    return [re.compile(p, re.IGNORECASE) for p in patterns]


# Bibliotecas JS: padrões em `<script src>` e conteúdo inline.
VERSION_PATTERNS = {
    "jquery": _c([
        r"jquery[.-](\d+\.\d+\.\d+)(?:\.min)?\.js",
        r"jquery/(\d+\.\d+\.\d+)/jquery",
        r"jQuery\s+v?(\d+\.\d+\.\d+)",
        r"jquery(?:\.min)?\.js\?ver=(\d+\.\d+\.\d+)",
    ]),
    "bootstrap": _c([
        r"bootstrap[.-](\d+\.\d+\.\d+)(?:\.min)?\.(?:js|css)",
        r"bootstrap/(\d+\.\d+\.\d+)/",
        r"Bootstrap\s+v?(\d+\.\d+\.\d+)",
    ]),
    "angular": _c([
        r"angular[.-](\d+\.\d+\.\d+)(?:\.min)?\.js",
        r"angular/(\d+\.\d+\.\d+)/",
    ]),
    "angularjs": _c([
        r"angular\.js/(\d+\.\d+\.\d+)",
        r"AngularJS\s+v(\d+\.\d+\.\d+)",
    ]),
    "react": _c([
        r"react(?:\.production)?[.-](\d+\.\d+\.\d+)(?:\.min)?\.js",
        r"react/(\d+\.\d+\.\d+)/",
    ]),
    "vue": _c([
        r"vue[.-](\d+\.\d+\.\d+)(?:\.min)?\.js",
        r"vue/(\d+\.\d+\.\d+)/",
        r"Vue\.js\s+v(\d+\.\d+\.\d+)",
    ]),
    "lodash": _c([
        r"lodash[.-](\d+\.\d+\.\d+)(?:\.min)?\.js",
        r"lodash/(\d+\.\d+\.\d+)/",
    ]),
    "moment": _c([
        r"moment[.-](\d+\.\d+\.\d+)(?:\.min)?\.js",
        r"moment/(\d+\.\d+\.\d+)/",
    ]),
    "handlebars": _c([
        r"handlebars[.-](\d+\.\d+\.\d+)(?:\.min)?\.js",
    ]),
    "underscore": _c([
        r"underscore[.-](\d+\.\d+\.\d+)(?:\.min)?\.js",
    ]),
}

# CMS: meta generator + assets versionados (?ver=).
CMS_VERSION_PATTERNS = {
    "wordpress": _c([
        r'<meta\s+name="generator"\s+content="WordPress\s+(\d+\.\d+(?:\.\d+)?)"\s*/?>',
        r"wp-includes/js/wp-emoji-release\.min\.js\?ver=(\d+\.\d+(?:\.\d+)?)",
        r"/wp-includes/css/dist/block-library/style\.min\.css\?ver=(\d+\.\d+(?:\.\d+)?)",
    ]),
    "joomla": _c([
        r'<meta\s+name="generator"\s+content="Joomla!\s+(\d+\.\d+(?:\.\d+)?)"\s*/?>',
    ]),
    "drupal": _c([
        r'<meta\s+name="generator"\s+content="Drupal\s+(\d+(?:\.\d+)*)"\s*/?>',
    ]),
}

# Headers: PHP e servidor (quando expõem versão).
_PHP_RE = re.compile(r"PHP/(\d+\.\d+(?:\.\d+)?)", re.IGNORECASE)
_SERVER_RE = re.compile(r"\b(nginx|apache|apache2|httpd|iis|litespeed|openresty)[/ ](\d+\.\d+(?:\.\d+)?)",
                        re.IGNORECASE)
# Nomes "canônicos" para casar produto no NVD.
_SERVER_ALIAS = {"apache2": "apache", "httpd": "apache", "iis": "iis"}


def detect_versions(html: str, headers: dict, script_urls) -> list[dict]:
    """Detecta ``[{library, version, source, kind, url}]`` do HTML/headers (puro).

    ``kind``: ``js`` (Retire.js cobre), ``cms``/``php``/``server`` (só via NVD).
    Dedup por (library, version).
    """
    html = html or ""
    inline = html[:_INLINE_LIMIT]
    hdrs = {str(k).lower(): v for k, v in (headers or {}).items()}
    found: dict = {}

    def add(library, version, source, kind, url=None):
        if not version:
            return
        key = (library, version)
        found.setdefault(key, {"library": library, "version": version,
                               "source": source, "kind": kind, "url": url})

    # 1) JS via <script src>
    for su in script_urls or []:
        for lib, pats in VERSION_PATTERNS.items():
            for pat in pats:
                m = pat.search(su or "")
                if m:
                    add(lib, m.group(1), "script_src", "js", su)
                    break

    # 2) JS via conteúdo inline (banners/comentários nos primeiros 50KB)
    for lib, pats in VERSION_PATTERNS.items():
        if any(k[0] == lib for k in found):
            continue  # já achou por src
        for pat in pats:
            m = pat.search(inline)
            if m:
                add(lib, m.group(1), "inline", "js")
                break

    # 3) CMS (meta generator, ?ver=)
    for cms, pats in CMS_VERSION_PATTERNS.items():
        for pat in pats:
            m = pat.search(html)
            if m:
                add(cms, m.group(1), "html", "cms")
                break

    # 4) PHP (header X-Powered-By)
    m = _PHP_RE.search(hdrs.get("x-powered-by", "") or "")
    if m:
        add("php", m.group(1), "header", "php")

    # 5) Servidor (header Server)
    m = _SERVER_RE.search(hdrs.get("server", "") or "")
    if m:
        name = m.group(1).lower()
        add(_SERVER_ALIAS.get(name, name), m.group(2), "header", "server")

    return list(found.values())


_DISPLAY = {"jquery": "jQuery", "bootstrap": "Bootstrap", "angular": "Angular",
            "angularjs": "AngularJS", "react": "React", "vue": "Vue",
            "lodash": "Lodash", "moment": "Moment.js", "handlebars": "Handlebars",
            "underscore": "Underscore", "wordpress": "WordPress", "joomla": "Joomla",
            "drupal": "Drupal", "php": "PHP", "nginx": "nginx", "apache": "Apache"}


def _display(lib: str) -> str:
    return _DISPLAY.get(lib, lib)


def _component_summary(comp: dict) -> str:
    n = len(comp["cves"])
    top = max_cvss(comp["cves"])
    if top is not None:
        metric = f"máx CVSS {top:g}"
    else:
        sev = _top_label(comp["cves"])
        metric = f"severidade máx {sev}" if sev else "sem CVSS"
    return f"{_display(comp['library'])} {comp['version']} ({n} CVE{'s' if n != 1 else ''}, {metric})"


_LABEL_PT = {"critical": "crítica", "high": "alta", "medium": "média", "low": "baixa"}
_LABEL_ORDER = {"critical": 4, "high": 3, "medium": 2, "low": 1}


def _top_label(cves) -> str:
    best = max((_LABEL_ORDER.get((c.get("severity") or "").lower(), 0) for c in cves),
               default=0)
    inv = {4: "critical", 3: "high", 2: "medium", 1: "low"}.get(best)
    return _LABEL_PT.get(inv, "") if inv else ""


async def check(url: str) -> CheckResult:
    https_url = with_scheme(url, "https")
    try:
        resp = await fetch(https_url, method="GET", follow_redirects=True)
    except (httpx.HTTPError, OSError) as exc:
        return CheckResult(
            name=NAME, status=Status.INCONCLUSO, severity=Severity.MEDIA,
            evidence=f"Falha ao obter o HTML da página: {exc!r}",
        )

    guard = content_guard(resp, NAME, Severity.MEDIA)
    if guard:
        return guard

    html = resp.text or ""
    script_urls = [s.src for s in extract_script_refs(html, str(resp.url))]
    detected = detect_versions(html, dict(resp.headers), script_urls)

    if not detected:
        return CheckResult(
            name=NAME, status=Status.INCONCLUSO, severity=Severity.MEDIA,
            evidence="Nenhuma versão de componente pôde ser identificada no HTML servido.",
            details={"components": []},
        )

    db = get_cve_db()
    try:
        await db.ensure_loaded()
    except Exception:  # noqa: BLE001 - fail-open: base indisponível
        pass

    components = []
    assessable = False
    for d in detected:
        if d["kind"] == "js":
            cves = db.lookup_js(d["library"], d["version"])
            if db.covers(d["library"]):
                assessable = True
            rec = db.recommended_upgrade(d["library"], d["version"])
            recommendation = (f"Atualizar {_display(d['library'])} para {rec}+"
                              if rec else f"Atualizar {_display(d['library'])} para a versão mais recente.")
        else:
            cves = await db.lookup_nvd(d["library"], d["version"])
            if NVD_ENABLED:
                assessable = True
            recommendation = f"Atualizar {_display(d['library'])} para a versão suportada mais recente."
        components.append({
            "library": d["library"], "version": d["version"], "source": d["source"],
            "kind": d["kind"], "cves": cves, "recommendation": recommendation,
        })

    vulnerable = [c for c in components if c["cves"]]
    all_cves = [cve for c in components for cve in c["cves"]]
    total_cves = len({cve["id"] for cve in all_cves if cve["id"] != "(advisory)"}) + \
        sum(1 for cve in all_cves if cve["id"] == "(advisory)")

    details = {
        "components": components,
        "detected": [{"library": c["library"], "version": c["version"],
                      "source": c["source"], "kind": c["kind"]} for c in components],
        "total_cves": total_cves,
        "max_cvss": max_cvss(all_cves),
    }

    if vulnerable:
        severity = severity_from_cves(all_cves)
        top = max_cvss(all_cves)
        cvss_txt = f", CVSS máx {top:g}" if top is not None else ""
        parts = ", ".join(_component_summary(c) for c in vulnerable)
        n = len(vulnerable)
        evidence = (f"{n} componente{'s' if n != 1 else ''} com vulnerabilidades "
                    f"conhecidas{cvss_txt}: {parts}.")
        return CheckResult(name=NAME, status=Status.FAIL, severity=severity,
                           evidence=evidence, details=details)

    if assessable:
        checked = ", ".join(f"{_display(c['library'])} {c['version']}"
                            for c in components if c["kind"] == "js")
        return CheckResult(
            name=NAME, status=Status.PASS, severity=Severity.BAIXA,
            evidence=f"Componentes detectados sem vulnerabilidade conhecida: {checked}.",
            details=details,
        )

    # Detectou versões mas nenhuma é avaliável (só CMS/PHP/servidor com NVD off).
    listed = ", ".join(f"{_display(c['library'])} {c['version']}" for c in components)
    return CheckResult(
        name=NAME, status=Status.INCONCLUSO, severity=Severity.MEDIA,
        evidence=f"Versões detectadas, mas sem base de CVE aplicável para avaliá-las: {listed}.",
        details=details,
    )
