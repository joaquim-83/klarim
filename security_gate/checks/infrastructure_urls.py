"""KL-149 — check de URLs de INFRAESTRUTURA hardcoded no HTML/JS público. Passivo: scaneia a
homepage + os `<script src>` da mesma origem procurando URLs de backend/PaaS (Cloud Run, Heroku,
Lambda…), túneis de dev (ngrok/localtunnel), localhost, IPs privados e endpoints internos de
Kubernetes. Vazar essas URLs revela a topologia e cria alvo direto (o WAF/edge é contornado).

NUNCA alerta se a URL contém o PRÓPRIO domínio do alvo. Reusa `_extract_js_urls` de `..utils`."""
from __future__ import annotations

import re
from typing import List

import httpx

from ..models import Result, Severity, Status
from ..utils import _extract_js_urls, _host

# (regex, rótulo, severidade). O prefixo `[a-z0-9.-]+` cobre subdomínios MULTI-label — ex.: o Cloud
# Run novo é `SERVICE-PROJNUM.REGION.run.app`, não só `service.run.app` (1 por pattern/source).
INFRA_PATTERNS = [
    (r'https?://[a-z0-9.-]+\.run\.app\b', "Google Cloud Run", Severity.HIGH),
    (r'https?://[a-z0-9.-]+\.herokuapp\.com\b', "Heroku", Severity.HIGH),
    (r'https?://[a-z0-9.-]+\.azurewebsites\.net\b', "Azure App Service", Severity.HIGH),
    (r'https?://[a-z0-9.-]+\.elasticbeanstalk\.com\b', "AWS Elastic Beanstalk", Severity.HIGH),
    (r'https?://[a-z0-9.-]+\.railway\.app\b', "Railway", Severity.HIGH),
    (r'https?://[a-z0-9.-]+\.fly\.dev\b', "Fly.io", Severity.HIGH),
    (r'https?://[a-z0-9.-]+\.onrender\.com\b', "Render", Severity.HIGH),
    (r'https?://[a-z0-9.-]+\.appspot\.com\b', "Google App Engine", Severity.HIGH),
    (r'https?://[a-z0-9.-]+\.cloudfunctions\.net\b', "Google Cloud Functions", Severity.HIGH),
    (r'https?://[a-z0-9.-]+\.lambda-url\.[a-z0-9-]+\.on\.aws\b', "AWS Lambda URL", Severity.HIGH),
    (r'https?://[a-z0-9.-]+\.netlify\.app\b', "Netlify", Severity.MEDIUM),
    (r'https?://[a-z0-9.-]+\.vercel\.app\b', "Vercel", Severity.MEDIUM),
    (r'https?://[a-z0-9.-]+\.ngrok(?:-free)?\.(?:io|app)\b', "ngrok (túnel de dev)", Severity.HIGH),
    (r'https?://[a-z0-9.-]+\.loca\.lt\b', "localtunnel", Severity.HIGH),
    (r'https?://localhost[:/]', "localhost", Severity.MEDIUM),
    (r'https?://127\.0\.0\.1[:/]', "127.0.0.1 (localhost)", Severity.MEDIUM),
    (r'https?://0\.0\.0\.0[:/]', "0.0.0.0", Severity.MEDIUM),
    (r'https?://10\.\d{1,3}\.\d{1,3}\.\d{1,3}', "IP privado (10.x)", Severity.HIGH),
    (r'https?://172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}', "IP privado (172.16-31.x)", Severity.HIGH),
    (r'https?://192\.168\.\d{1,3}\.\d{1,3}', "IP privado (192.168.x)", Severity.HIGH),
    (r'https?://[a-z0-9.-]+\.svc\.cluster\.local\b', "Kubernetes interno", Severity.CRITICAL),
]
_COMPILED_INFRA = [(re.compile(p, re.IGNORECASE), label, sev) for p, label, sev in INFRA_PATTERNS]

# Headers de dev/túnel que não deveriam aparecer em produção.
DEV_HEADERS = ("ngrok-skip-browser-warning", "x-debug", "x-debug-token", "x-debug-token-link")


def _scan_for_infra(text: str, source: str, own_host: str, results: List[Result],
                    source_type: str) -> None:
    for pattern, service, severity in _COMPILED_INFRA:
        for match in pattern.finditer(text):
            url = match.group(0)
            if own_host and own_host in url:
                continue   # o próprio domínio nunca é "URL de infra exposta"
            slug = re.sub(r'[^a-z0-9]+', "_", service.lower()).strip("_")
            results.append(Result(f"infra_{slug}", "infrastructure", source, Status.FAIL,
                                  severity, f"URL de {service} exposta em {source_type}: {url[:80]}"))
            break   # 1 finding por pattern/source


async def check_infrastructure_urls(client: httpx.AsyncClient, base_url: str, config=None) -> List[Result]:
    own_host = _host(base_url).split(":")[0]
    results: List[Result] = []

    try:
        r = await client.get(base_url)
    except httpx.HTTPError as exc:
        return [Result("infra_error", "infrastructure", "/", Status.ERROR, Severity.INFO,
                       f"Não foi possível verificar URLs de infraestrutura: {exc!r}")]

    _scan_for_infra(r.text, base_url, own_host, results, "html")

    checked: set = set()
    for js_url in _extract_js_urls(r.text, base_url):
        if js_url in checked:
            continue
        checked.add(js_url)
        try:
            r_js = await client.get(js_url)
        except httpx.HTTPError:
            continue
        if r_js.status_code == 200:
            _scan_for_infra(r_js.text, js_url, own_host, results, "js")

    # Headers de dev na resposta da homepage.
    present = {h.lower() for h in r.headers}
    for header in DEV_HEADERS:
        if header in present:
            results.append(Result("infra_dev_header", "infrastructure", "/", Status.FAIL,
                                  Severity.MEDIUM, f"Header de desenvolvimento '{header}' em produção"))

    if not results:
        results.append(Result("infra_urls_ok", "infrastructure", "/", Status.PASS, Severity.HIGH,
                              "Nenhuma URL de infraestrutura/backend exposta no HTML/JS"))
    return results
