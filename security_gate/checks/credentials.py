"""KL-141 — check 4 do KL-139: CREDENCIAIS expostas no HTML e no JavaScript público.

O check mais sensível do Gate. **Princípio inviolável:** NUNCA armazena, loga ou transmite o
VALOR de uma credencial encontrada — o output é só `tipo + localização + severidade`. Uma key do
Stripe que vaze num log de CI seria dano irreparável.

Cobertura (comentário 5 do card — completa, zero escopo reduzido):
  • HTML da homepage + até 9 páginas internas linkadas (crawl mesma-origem, 10 páginas no total);
  • TODOS os `<script src>` da MESMA origem (sem limite), com dedup;
  • inline scripts / meta / data-* (tudo dentro do HTML já é scaneado como texto).

Detecção = ~50 patterns fixos (regex, primária) + análise de entropia (reforço, só p/ atribuições
a variáveis com nome de segredo). Anti-falso-positivo: placeholders, `<code>`/`<pre>`/doc, valores
curtos."""
from __future__ import annotations

import math
import re
from typing import Dict, List, Tuple

import httpx

from ..models import Result, Severity, Status

# --------------------------------------------------------------------------- #
# Patterns de credenciais (lista exaustiva, por categoria)
# --------------------------------------------------------------------------- #
CREDENTIAL_PATTERNS: Dict[str, List[Tuple[str, str, Severity]]] = {
    "payment": [
        (r'sk_live_[a-zA-Z0-9]{24,}', "Stripe Secret Key (live)", Severity.CRITICAL),
        (r'sk_test_[a-zA-Z0-9]{24,}', "Stripe Secret Key (test)", Severity.HIGH),
        (r'pk_live_[a-zA-Z0-9]{24,}', "Stripe Publishable Key (live)", Severity.MEDIUM),
        (r'pk_test_[a-zA-Z0-9]{24,}', "Stripe Publishable Key (test)", Severity.LOW),
        (r'EAAG[a-zA-Z0-9]{20,}', "Facebook/Meta Access Token", Severity.CRITICAL),
    ],
    "cloud": [
        (r'AKIA[A-Z0-9]{16}', "AWS Access Key ID", Severity.CRITICAL),
        (r'aws_secret_access_key\s*[=:]\s*["\']?[A-Za-z0-9/+=]{40}', "AWS Secret Access Key", Severity.CRITICAL),
        (r'AWS_SESSION_TOKEN\s*[=:]\s*["\']?[A-Za-z0-9/+=]{100,}', "AWS Session Token", Severity.CRITICAL),
        (r'AIza[A-Za-z0-9_-]{35}', "Google API Key", Severity.HIGH),
        (r'GOOG[\w]{10,}', "Google OAuth Token", Severity.HIGH),
        (r'AccountKey=[A-Za-z0-9/+=]{44,}', "Azure Storage Account Key", Severity.CRITICAL),
        (r'AZURE_[A-Z_]+=\s*["\']?[A-Za-z0-9-]{32,}', "Azure Credential", Severity.HIGH),
    ],
    "baas_database": [
        (r'eyJ[A-Za-z0-9_-]{100,}\.eyJ[A-Za-z0-9_-]+', "Supabase/JWT anon key (long JWT)", Severity.HIGH),
        (r'SUPABASE_KEY\s*[=:]\s*["\']?[A-Za-z0-9._-]{50,}', "Supabase Key", Severity.CRITICAL),
        (r'SUPABASE_URL\s*[=:]\s*["\']?https?://[a-z0-9]+\.supabase\.co', "Supabase URL", Severity.MEDIUM),
        (r'firebaseConfig\s*=\s*\{', "Firebase Config Object", Severity.HIGH),
        (r'FIREBASE_CONFIG\s*[=:]', "Firebase Config", Severity.HIGH),
        (r'mongodb(\+srv)?://[^\s"\'<]{10,}', "MongoDB Connection String", Severity.CRITICAL),
        (r'postgres(ql)?://[^\s"\'<]{10,}', "PostgreSQL Connection String", Severity.CRITICAL),
        (r'mysql://[^\s"\'<]{10,}', "MySQL Connection String", Severity.CRITICAL),
        (r'redis://[^\s"\'<]{10,}', "Redis Connection String", Severity.HIGH),
        (r'DATABASE_URL\s*[=:]\s*["\']?[a-z]+://[^\s"\'<]+', "Database URL", Severity.CRITICAL),
        (r'DB_PASSWORD\s*[=:]\s*["\']?[^\s"\'<]{4,}', "Database Password", Severity.CRITICAL),
    ],
    "ai_ml": [
        (r'sk-[a-zA-Z0-9]{48,}', "OpenAI API Key", Severity.CRITICAL),
        (r'sk-ant-[a-zA-Z0-9-]{80,}', "Anthropic API Key", Severity.CRITICAL),
        (r'ANTHROPIC_API_KEY\s*[=:]', "Anthropic Key Reference", Severity.HIGH),
        (r'hf_[a-zA-Z0-9]{34,}', "Hugging Face Token", Severity.HIGH),
        (r'HF_TOKEN\s*[=:]', "Hugging Face Token Reference", Severity.HIGH),
    ],
    "auth_identity": [
        (r'NEXTAUTH_SECRET\s*[=:]\s*["\']?[^\s"\'<]{16,}', "NextAuth Secret", Severity.CRITICAL),
        (r'JWT_SECRET\s*[=:]\s*["\']?[^\s"\'<]{16,}', "JWT Secret", Severity.CRITICAL),
        (r'SESSION_SECRET\s*[=:]\s*["\']?[^\s"\'<]{16,}', "Session Secret", Severity.CRITICAL),
        (r'OAUTH_CLIENT_SECRET\s*[=:]', "OAuth Client Secret", Severity.CRITICAL),
        (r'AUTH0_[A-Z_]+=\s*["\']?[^\s"\'<]{10,}', "Auth0 Credential", Severity.HIGH),
        (r'CLERK_[A-Z_]+=\s*["\']?[^\s"\'<]{10,}', "Clerk Credential", Severity.HIGH),
    ],
    "communication": [
        (r'SG\.[a-zA-Z0-9_-]{22}\.[a-zA-Z0-9_-]{43}', "SendGrid API Key", Severity.CRITICAL),
        (r'xoxb-[0-9]{10,}-[0-9]{10,}-[a-zA-Z0-9]{24}', "Slack Bot Token", Severity.CRITICAL),
        (r'xoxp-[0-9]{10,}-[0-9]{10,}-[a-zA-Z0-9]{24,}', "Slack User Token", Severity.CRITICAL),
        (r'xapp-[0-9]+-[A-Za-z0-9]{10,}', "Slack App Token", Severity.HIGH),
        (r'AC[a-f0-9]{32}', "Twilio Account SID", Severity.HIGH),
        (r'TWILIO_AUTH_TOKEN\s*[=:]', "Twilio Auth Token", Severity.CRITICAL),
        (r'key-[a-zA-Z0-9]{32}', "Mailgun API Key", Severity.CRITICAL),
    ],
    "generic": [
        (r'-----BEGIN (RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----', "Private Key", Severity.CRITICAL),
        (r'ghp_[a-zA-Z0-9]{36}', "GitHub Personal Access Token", Severity.CRITICAL),
        (r'gho_[a-zA-Z0-9]{36}', "GitHub OAuth Token", Severity.CRITICAL),
        (r'ghu_[a-zA-Z0-9]{36}', "GitHub User Token", Severity.CRITICAL),
        (r'ghs_[a-zA-Z0-9]{36}', "GitHub Server Token", Severity.CRITICAL),
        (r'glpat-[a-zA-Z0-9_-]{20,}', "GitLab Personal Access Token", Severity.CRITICAL),
        (r'npm_[a-zA-Z0-9]{36}', "npm Token", Severity.CRITICAL),
        (r'pypi-[a-zA-Z0-9_-]{16,}', "PyPI Token", Severity.CRITICAL),
        (r'(?:_SECRET|_TOKEN|_KEY|_PASSWORD|_CREDENTIAL)\s*[=:]\s*["\']?'
         r'(?!YOUR_|xxx|placeholder|changeme|TODO)[A-Za-z0-9/+=_-]{16,}',
         "Generic Secret Assignment", Severity.HIGH),
    ],
}

# Pré-compila uma vez (perf: os patterns rodam por página × por JS).
_COMPILED: Dict[str, List[Tuple[re.Pattern, str, Severity]]] = {
    cat: [(re.compile(p), label, sev) for p, label, sev in pats]
    for cat, pats in CREDENTIAL_PATTERNS.items()
}

# --------------------------------------------------------------------------- #
# Anti-falso-positivo
# --------------------------------------------------------------------------- #
PLACEHOLDER_PATTERNS = re.compile(
    r'YOUR_|_HERE|xxx|placeholder|changeme|TODO|REPLACE|example|test_key|demo|sample|\.\.\.|\*{3,}',
    re.IGNORECASE)


def _is_placeholder(value: str) -> bool:
    return bool(PLACEHOLDER_PATTERNS.search(value))


def _is_in_code_example(html_context: str) -> bool:
    """True se o match parece estar dentro de `<code>`/`<pre>` ou de documentação (heurística:
    os 200 chars ANTES do match contêm um desses marcadores)."""
    lower = html_context[-200:].lower()
    return any(tag in lower for tag in ("<code", "<pre", "exemplo", "example", "documentação", "docs"))


# --------------------------------------------------------------------------- #
# Análise de entropia (reforço — só p/ atribuições a variáveis "de segredo")
# --------------------------------------------------------------------------- #
_ENTROPY_MIN = 4.5
_ENTROPY_MINLEN = 20
_SECRETISH_LHS = re.compile(
    r'(secret|api[_-]?key|apikey|token|password|passwd|pwd|credential|private[_-]?key|auth[_-]?key)',
    re.IGNORECASE)
_ASSIGN_RE = re.compile(r'''([A-Za-z_$][\w$.]*)\s*[=:]\s*["']([^"'\s]{20,})["']''')


def _entropy(s: str) -> float:
    """Entropia de Shannon. Strings aleatórias (keys/tokens) ficam acima de ~4.5."""
    if len(s) < 8:
        return 0.0
    total = len(s)
    freq: Dict[str, int] = {}
    for c in s:
        freq[c] = freq.get(c, 0) + 1
    return -sum((n / total) * math.log2(n / total) for n in freq.values())


def _scan_entropy(text: str, source: str, source_type: str) -> List[Result]:
    """Reforço: atribuição a uma variável de nome "de segredo" cujo valor tem entropia alta
    (>4.5) e comprimento >20 → provável credencial que escapou dos patterns fixos. NÃO inclui o
    valor (só o NOME da variável, que é metadado, e a linha). Cap de 5/source contra flood em JS
    minificado."""
    findings: List[Result] = []
    for m in _ASSIGN_RE.finditer(text):
        lhs, value = m.group(1), m.group(2)
        if not _SECRETISH_LHS.search(lhs):
            continue                       # só variáveis com cara de segredo (evita flood)
        if _is_placeholder(value) or len(value) <= _ENTROPY_MINLEN or _entropy(value) <= _ENTROPY_MIN:
            continue
        line = text[:m.start()].count("\n") + 1
        findings.append(Result(
            check="credential_entropy", category="credentials", path=source,
            status=Status.FAIL, severity=Severity.MEDIUM,
            detail=f"Segredo de alta entropia atribuído a '{lhs[:32]}' em {source_type}:{line}"))
        if len(findings) >= 5:
            break
    return findings


# --------------------------------------------------------------------------- #
# Scan de texto (HTML ou JS) — NUNCA guarda o valor
# --------------------------------------------------------------------------- #
def _scan_text(text: str, source: str, source_type: str) -> List[Result]:
    findings: List[Result] = []
    for category, patterns in _COMPILED.items():
        for pattern, label, severity in patterns:
            for match in pattern.finditer(text):
                if _is_placeholder(match.group(0)):
                    continue
                if source_type == "html":
                    context = text[max(0, match.start() - 200):match.start()]
                    if _is_in_code_example(context):
                        continue
                line = text[:match.start()].count("\n") + 1
                # NUNCA inclui match.group() ou parte do valor — só tipo + localização.
                findings.append(Result(
                    check=f"credential_{category}", category="credentials", path=source,
                    status=Status.FAIL, severity=severity,
                    detail=f"{label} encontrado em {source_type}:{line}"))
                break  # 1 finding por pattern/source já basta
    findings.extend(_scan_entropy(text, source, source_type))
    return findings


# --------------------------------------------------------------------------- #
# Extração de URLs (JS mesma-origem + links internos)
# --------------------------------------------------------------------------- #
_SCRIPT_SRC_RE = re.compile(r'<script[^>]+src=["\']([^"\']+)["\']', re.IGNORECASE)
_ANCHOR_RE = re.compile(r'<a[^>]+href=["\']([^"\'#]+)["\']', re.IGNORECASE)


def _origin(base_url: str) -> str:
    return "/".join(base_url.split("/")[:3])   # https://host


def _host(base_url: str) -> str:
    parts = base_url.split("/")
    return parts[2] if len(parts) > 2 else ""


def _extract_js_urls(html: str, base_url: str) -> List[str]:
    """URLs de TODOS os `<script src>` da MESMA origem (CDN de terceiros é ignorado)."""
    host, origin = _host(base_url), _origin(base_url)
    urls: List[str] = []
    for m in _SCRIPT_SRC_RE.finditer(html):
        src = m.group(1)
        if src.startswith("//"):
            src = "https:" + src
        elif src.startswith("/"):
            src = origin + src
        elif not src.startswith("http"):
            src = origin + "/" + src
        if host and host in src.split("/")[2] and src not in urls:
            urls.append(src)
    return urls


def _extract_internal_links(html: str, base_url: str) -> List[str]:
    """Links internos (mesma origem) do HTML, deduplicados (teto 20)."""
    origin = _origin(base_url)
    links: List[str] = []
    for m in _ANCHOR_RE.finditer(html):
        href = m.group(1)
        full = None
        if href.startswith("/") and not href.startswith("//"):
            full = origin + href
        elif href.startswith(origin):
            full = href
        if full and full not in links:
            links.append(full)
    return links[:20]


# --------------------------------------------------------------------------- #
# Check principal
# --------------------------------------------------------------------------- #
async def check_credentials(client: httpx.AsyncClient, base_url: str, config=None) -> List[Result]:
    """Crawla homepage + até 9 páginas internas (10 no total), scaneia HTML + TODOS os JS da
    mesma origem (dedup) por credenciais. NUNCA armazena/loga/transmite o VALOR encontrado.
    (`config` reservado p/ `credential_extra_patterns` em prompt futuro — não usado aqui.)"""
    results: List[Result] = []
    checked_js: set = set()
    pages_to_check: List[str] = [base_url]
    pages_checked: set = set()

    while pages_to_check and len(pages_checked) < 10:
        page_url = pages_to_check.pop(0)
        if page_url in pages_checked:
            continue
        pages_checked.add(page_url)

        try:
            r = await client.get(page_url)
        except (httpx.TimeoutException, httpx.HTTPError):
            continue
        if r.status_code != 200 or "text/html" not in r.headers.get("content-type", ""):
            continue
        html = r.text

        # HTML (inline scripts, meta, data-*, forms) é scaneado como texto.
        results.extend(_scan_text(html, source=page_url, source_type="html"))

        # TODOS os JS da mesma origem (dedup global).
        for js_url in _extract_js_urls(html, base_url):
            if js_url in checked_js:
                continue
            checked_js.add(js_url)
            try:
                r_js = await client.get(js_url)
            except (httpx.TimeoutException, httpx.HTTPError):
                continue
            if r_js.status_code == 200:
                results.extend(_scan_text(r_js.text, source=js_url, source_type="js"))

        # Links internos p/ o crawl — só a partir da homepage (primeira página).
        if len(pages_checked) == 1:
            pages_to_check.extend(_extract_internal_links(html, base_url)[:9])

    if not results:
        results.append(Result(
            check="credentials_scan", category="credentials", path="/",
            status=Status.PASS, severity=Severity.CRITICAL,
            detail="Nenhuma credencial exposta encontrada"))
    return results
