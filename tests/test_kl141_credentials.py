"""KL-141 Prompt 2 — check de credenciais no HTML/JS. Detecção por categoria, anti-falso-
positivo, crawl (10 páginas + todos os JS mesma-origem, dedup), entropia, e a REGRA INVIOLÁVEL:
o VALOR da credencial NUNCA aparece no `Result`. Offline (httpx.MockTransport)."""
from __future__ import annotations

import asyncio

import httpx
import pytest

from security_gate import engine as sge
from security_gate.checks.credentials import (
    check_credentials, _entropy, _is_placeholder, _is_in_code_example, _scan_text,
    _extract_js_urls, _extract_internal_links,
)
from security_gate.models import Severity, Status


# --------------------------------------------------------------------------- #
# Fixtures de credenciais VÁLIDAS (comprimento casando os patterns).
# ⚠️ Construídas por CONCATENAÇÃO de propósito: o literal contíguo do "segredo" (falso) NÃO pode
# aparecer no arquivo, senão o secret-scanning/push-protection do GitHub bloqueia o push. O valor
# em runtime é completo (o check do Gate testa a string montada) — só o SOURCE fica fragmentado.
# --------------------------------------------------------------------------- #
def _mk(*parts: str) -> str:
    return "".join(parts)


STRIPE_LIVE = _mk("sk_", "live_", "ABCdef0123456789ABCdef01")   # 24 chars após o prefixo
STRIPE_TEST = _mk("sk_", "test_", "ABCdef0123456789ABCdef01")
STRIPE_PK_TEST = _mk("pk_", "test_", "ABCdef0123456789ABCdef01")
AWS_KEY = _mk("AK", "IA", "1234567890ABCDEF")                  # AKIA + 16
MONGO = _mk("mongo", "db://", "user:pass@cluster0.mongodb.net/db")
OPENAI = _mk("sk-", "a" * 48)
GITHUB_PAT = _mk("ghp_", "a" * 36)
SENDGRID = _mk("SG.", "a" * 22, ".", "b" * 43)
SLACK_BOT = _mk("xoxb", "-1234567890-0987654321-abcdefghijklmnopqrstuvwx")
PRIVATE_KEY = _mk("-----BEGIN ", "RSA ", "PRIVATE KEY-----")
FIREBASE = "firebaseConfig = {"
_RND = _mk("aB3xY9zK2mN8", "qR4tV6wL0pS5", "dF7gH1jC")          # 32 chars, entropia ~5.0


def _scan_html(text):
    return _scan_text(text, "https://x.test/", "html")


def _scan_js(text):
    return _scan_text(text, "https://x.test/app.js", "js")


def _has(results, label_part, severity=None):
    for r in results:
        if label_part in r.detail and (severity is None or r.severity == severity):
            return True
    return False


def _no_value_leak(results, secret):
    """A REGRA: nenhum pedaço do valor da credencial pode estar no detail/path do Result."""
    frag = secret[-16:]   # a parte "aleatória" do segredo
    for r in results:
        assert frag not in r.detail, f"VALOR vazou no detail: {r.detail}"
        assert frag not in (r.path or ""), f"VALOR vazou no path: {r.path}"


# =========================================================================== #
# Detecção por categoria
# =========================================================================== #

def test_stripe_live_fail_critical():
    res = _scan_html(f'<script>var k="{STRIPE_LIVE}";</script>')
    assert _has(res, "Stripe Secret Key (live)", Severity.CRITICAL)
    _no_value_leak(res, STRIPE_LIVE)


def test_aws_key_fail_critical():
    res = _scan_js(f'const id="{AWS_KEY}";')
    assert _has(res, "AWS Access Key ID", Severity.CRITICAL)
    _no_value_leak(res, AWS_KEY)


def test_mongodb_conn_fail_critical():
    res = _scan_js(f'const uri="{MONGO}";')
    assert _has(res, "MongoDB Connection String", Severity.CRITICAL)
    _no_value_leak(res, MONGO)


def test_openai_key_fail_critical():
    res = _scan_js(f'const k="{OPENAI}";')
    assert _has(res, "OpenAI API Key", Severity.CRITICAL)
    _no_value_leak(res, OPENAI)


def test_github_pat_fail_critical():
    res = _scan_html(f'<!-- {GITHUB_PAT} -->')
    assert _has(res, "GitHub Personal Access Token", Severity.CRITICAL)


def test_sendgrid_fail_critical():
    res = _scan_js(f'k="{SENDGRID}"')
    assert _has(res, "SendGrid API Key", Severity.CRITICAL)


def test_private_key_fail_critical():
    res = _scan_html(f'<script>const p=`{PRIVATE_KEY}`;</script>')
    assert _has(res, "Private Key", Severity.CRITICAL)


def test_slack_bot_fail_critical():
    res = _scan_js(f'const t="{SLACK_BOT}";')
    assert _has(res, "Slack Bot Token", Severity.CRITICAL)


def test_firebase_config_fail_high():
    res = _scan_js(f'const {FIREBASE} apiKey: "x" }};')
    assert _has(res, "Firebase Config", Severity.HIGH)


# =========================================================================== #
# Falsos positivos
# =========================================================================== #

def test_placeholder_your_key_not_detected():
    res = _scan_html(f'<script>var k="{_mk("sk_", "live_", "YOURKEYHERExxxxxxxxxxxxxxxx")}";</script>')
    assert not _has(res, "Stripe Secret Key")   # contém 'xxx' → placeholder


def test_placeholder_changeme_not_detected():
    res = _scan_js(f'const k="{_mk("sk_", "live_", "changeme00000000000000000")}";')
    assert not _has(res, "Stripe Secret Key")   # contém 'changeme' → placeholder


def test_code_example_not_detected():
    # Chave real DENTRO de <code> (documentação) → ignorada (só em HTML).
    res = _scan_html(f'<p>Exemplo:</p><code>{STRIPE_LIVE}</code>')
    assert not _has(res, "Stripe Secret Key")


def test_code_example_but_in_js_still_detected():
    # A supressão de <code> vale só p/ HTML — o mesmo texto num JS ainda é flagado.
    res = _scan_js(f'<code>{STRIPE_LIVE}</code>')
    assert _has(res, "Stripe Secret Key")


def test_stripe_pk_test_is_low():
    res = _scan_html(f'<script>var k="{STRIPE_PK_TEST}";</script>')
    assert _has(res, "Stripe Publishable Key (test)", Severity.LOW)


def test_empty_assignment_not_detected():
    res = _scan_js('const API_KEY = "";')
    assert not _has(res, "Generic Secret")


def test_is_placeholder_helper():
    assert _is_placeholder("YOUR_KEY_HERE") and _is_placeholder("xxxxx") and _is_placeholder("changeme")
    assert not _is_placeholder(_RND)


def test_is_in_code_example_helper():
    assert _is_in_code_example("bla <code>foo")
    assert _is_in_code_example("veja o exemplo a seguir: ")
    assert not _is_in_code_example("<script>const k =")


# =========================================================================== #
# Extração de URLs (JS mesma-origem, links internos)
# =========================================================================== #

def test_extract_js_same_origin_only():
    html = ('<script src="/local.js"></script>'
            '<script src="https://cdn.other.com/lib.js"></script>'
            '<script src="//x.test/proto.js"></script>')
    urls = _extract_js_urls(html, "https://x.test")
    assert "https://x.test/local.js" in urls
    assert "https://x.test/proto.js" in urls
    assert not any("cdn.other.com" in u for u in urls)   # CDN de terceiro ignorado


def test_extract_internal_links_same_origin():
    html = ('<a href="/login">l</a><a href="/painel">p</a>'
            '<a href="https://x.test/sobre">s</a>'
            '<a href="https://evil.com/x">e</a><a href="#frag">f</a>')
    links = _extract_internal_links(html, "https://x.test")
    assert "https://x.test/login" in links and "https://x.test/sobre" in links
    assert not any("evil.com" in l for l in links)


# =========================================================================== #
# Crawl (10 páginas + JS dedup)
# =========================================================================== #

class _Site:
    """MockTransport: mapa path→(content_type, body). Registra os GET p/ asserts de crawl/dedup."""
    def __init__(self, pages):
        self.pages = pages
        self.gets = []

    def __call__(self, request):
        path = request.url.path or "/"
        self.gets.append(path)
        if path in self.pages:
            ct, body = self.pages[path]
            return httpx.Response(200, text=body, headers={"content-type": ct})
        return httpx.Response(404)


def _run_creds(pages, base="https://x.test"):
    site = _Site(pages)

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(site),
                                     follow_redirects=True) as c:
            return await check_credentials(c, base), site
    return asyncio.run(go())


def test_crawl_caps_at_10_pages():
    links = "".join(f'<a href="/p{i}">{i}</a>' for i in range(20))
    pages = {"/": ("text/html", f"<html>{links}</html>")}
    for i in range(20):
        pages[f"/p{i}"] = ("text/html", "<html>vazio</html>")
    _, site = _run_creds(pages)
    html_gets = [p for p in site.gets if p == "/" or p.startswith("/p")]
    assert len(set(html_gets)) <= 10   # homepage + no máx 9 internas


def test_crawl_same_origin_js_scanned_cdn_ignored():
    home = ('<html><script src="/app.js"></script>'
            '<script src="https://cdn.other.com/lib.js"></script></html>')
    pages = {
        "/": ("text/html", home),
        "/app.js": ("application/javascript", f'const k="{OPENAI}";'),
        # a lib do CDN "existe" mas NÃO deve ser buscada
        "/lib.js": ("application/javascript", f'const bad="{STRIPE_LIVE}";'),
    }
    res, site = _run_creds(pages)
    assert _has(res, "OpenAI API Key")            # do app.js mesma-origem
    assert not _has(res, "Stripe Secret Key")     # do CDN → não buscado
    assert not any("cdn.other.com" in p for p in site.gets)


def test_crawl_js_dedup():
    home = '<html><script src="/app.js"></script><script src="/app.js"></script></html>'
    pages = {"/": ("text/html", home), "/app.js": ("application/javascript", "const x=1;")}
    _, site = _run_creds(pages)
    assert site.gets.count("/app.js") == 1   # mesmo JS não é buscado 2x


def test_no_findings_explicit_pass():
    pages = {"/": ("text/html", "<html>site limpo, sem segredos</html>")}
    res, _ = _run_creds(pages)
    assert len(res) == 1 and res[0].status == Status.PASS
    assert res[0].check == "credentials_scan"


# =========================================================================== #
# Integração na engine + regra do não-vazamento (end-to-end)
# =========================================================================== #

def test_run_all_credentials_only(monkeypatch):
    site = _Site({"/": ("text/html", f'<script>var k="{STRIPE_LIVE}";</script>')})
    real_client = httpx.AsyncClient

    def _factory(**kw):
        kw.pop("verify", None)
        return real_client(transport=httpx.MockTransport(site), **kw)
    monkeypatch.setattr(sge.httpx, "AsyncClient", _factory)
    rep = asyncio.run(sge.run_all("https://x.test", checks=["credentials"]))
    assert any(r.category == "credentials" and r.status == Status.FAIL for r in rep.results)
    assert not rep.passed   # Stripe live = CRITICAL → gate reprova


def test_value_never_in_any_result():
    res = _scan_html(f'<script>const a="{STRIPE_LIVE}"; const b="{AWS_KEY}"; '
                     f'const c="{OPENAI}"; const d="{SLACK_BOT}";</script>')
    # nenhum fragmento aleatório de nenhum segredo pode aparecer nos results
    for secret in (STRIPE_LIVE, AWS_KEY, OPENAI, SLACK_BOT):
        _no_value_leak(res, secret)


# =========================================================================== #
# Entropia (reforço)
# =========================================================================== #

def test_entropy_high_secret_assignment_medium():
    res = _scan_js(f'window.SECRET = "{_RND}";')
    assert _has(res, "alta entropia", Severity.MEDIUM)
    _no_value_leak(res, _RND)


def test_entropy_normal_value_not_detected():
    res = _scan_js('window.name = "João da Silva Pereira";')
    assert not _has(res, "alta entropia")


def test_entropy_non_secret_lhs_not_detected():
    # LHS sem cara de segredo (mesmo com valor de alta entropia) → não flaga (evita flood em JS min.).
    res = _scan_js(f'window.buildHash = "{_RND}";')
    assert not _has(res, "alta entropia")


def test_entropy_helper():
    assert _entropy(_RND) > 4.5
    assert _entropy("aaaaaaaa") < 1.0
    assert _entropy("short") == 0.0   # < 8 chars
