"""KL-92 Prompt 4 (final) — GA4/CSP, allowlist de SRI (check 13), classificação de pre-fetch de
e-mail, anonimização IPv6 (LGPD) e tendência com zeros. Offline."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

import api.bot_classifier as bc

_ROOT = Path(__file__).resolve().parent.parent


# =========================================================================== #
# 1. Item 1 — CSP: GA4 no lugar do Cloudflare Web Analytics
# =========================================================================== #

def _csp_directive():
    for line in (_ROOT / "frontend/nginx/security_headers.conf").read_text().splitlines():
        if "add_header Content-Security-Policy" in line:
            return line
    return ""


def test_csp_swapped_cloudflare_for_ga4():
    csp = _csp_directive()   # só a diretiva (o comentário do arquivo pode citar o nome antigo)
    assert "cloudflareinsights.com" not in csp
    # GA4 presente: host do loader + hash do init inline + hosts de connect/img.
    assert "https://www.googletagmanager.com" in csp
    assert "sha256-qzH7zDtLe593g3bHtjaiMTvw04nqU/2iiMJnv9osNzA=" in csp
    assert "https://www.google-analytics.com" in csp


def test_base_layout_has_ga4_no_cf_beacon():
    base = (_ROOT / "web/src/layouts/Base.astro").read_text()
    # o SCRIPT do Cloudflare foi removido (o comentário pode mencionar "beacon.min.js"):
    assert 'src="https://static.cloudflareinsights.com' not in base
    assert "googletagmanager.com/gtag/js?id=G-7WPZN66JTB" in base
    # o init inline tem que casar EXATAMENTE o conteúdo hasheado na CSP.
    assert ("window.dataLayer=window.dataLayer||[];function gtag(){dataLayer.push(arguments);}"
            "gtag('js',new Date());gtag('config','G-7WPZN66JTB');") in base


def test_ga4_inline_hash_matches_content():
    import base64
    import hashlib
    content = ("window.dataLayer=window.dataLayer||[];function gtag(){dataLayer.push(arguments);}"
               "gtag('js',new Date());gtag('config','G-7WPZN66JTB');")
    h = base64.b64encode(hashlib.sha256(content.encode()).digest()).decode()
    assert h == "qzH7zDtLe593g3bHtjaiMTvw04nqU/2iiMJnv9osNzA="  # o hash na CSP casa o conteúdo


# =========================================================================== #
# 2. Item 1d — check 13 (SRI): allowlist de CDN dinâmico
# =========================================================================== #

def _run_sri(html, monkeypatch):
    from scanner.checks import check_sri as c

    class _R:
        def __init__(self, h):
            self.text, self.url, self.status_code, self.headers = h, "https://klarim.net/", 200, {}

    async def fake(*a, **k):
        return _R(html)
    monkeypatch.setattr(c, "fetch", fake)
    return asyncio.run(c.check("https://klarim.net"))


def test_sri_allowlists_gtag(monkeypatch):
    from scanner.checks.base import Status
    r = _run_sri('<html><head><script async src="https://www.googletagmanager.com/gtag/js?id=G-X">'
                 '</script></head><body>x</body></html>', monkeypatch)
    assert r.status == Status.PASS
    allow = r.details.get("allowlisted_domains") or []
    assert any("googletagmanager.com" in d for d in allow)


def test_sri_still_fails_third_party(monkeypatch):
    from scanner.checks.base import Status
    r = _run_sri('<html><script src="https://evil-cdn.com/a.js"></script></html>', monkeypatch)
    assert r.status == Status.FAIL     # SRI segue valendo p/ scripts de terceiros


def test_sri_helper_allowlist():
    from scanner.checks.check_sri import _sri_allowlisted
    assert _sri_allowlisted("www.googletagmanager.com")
    assert _sri_allowlisted("www.google-analytics.com")
    assert not _sri_allowlisted("evil-cdn.com")


# =========================================================================== #
# 3. Item 2 — pre-fetch de e-mail
# =========================================================================== #

@pytest.mark.parametrize("ip", ["66.102.1.5", "66.249.70.1", "40.94.1.1", "40.92.1.1", "104.47.1.1"])
def test_email_prefetch_ranges(ip):
    assert bc.is_email_prefetch_ip(ip) is True
    assert bc.classify_bot(ip, "Mozilla", "US", "/site/x.com") == (True, "email_prefetch")


def test_email_prefetch_not_br():
    assert bc.is_email_prefetch_ip("189.28.100.42") is False


def test_email_prefetch_distinct_domains_rule():
    # >20 domínios distintos numa hora → email_prefetch (pega prefetcher desconhecido)
    assert bc.classify_bot("189.28.1.1", "Mozilla", "BR", "/site/x",
                           distinct_domains_last_hour=21) == (True, "email_prefetch")
    # <=20 não dispara
    assert bc.classify_bot("189.28.1.1", "Mozilla", "BR", "/scan",
                           distinct_domains_last_hour=5, request_count_last_hour=2,
                           has_other_requests=True) == (False, None)


def test_email_prefetch_in_simple_classifier():
    assert bc.classify_bot_simple("104.47.9.9", "Mozilla", "BR") == (True, "email_prefetch")


# =========================================================================== #
# 4. Item 4 (LGPD IPv6) + Item 5 (tendência com zeros) — contrato/derivação
# =========================================================================== #

def test_anonymize_handles_ipv6():
    # o SQL trata IPv4 (/24) e IPv6 (/48). Validado contra Postgres 16; aqui garante o contrato.
    import inspect
    from discovery.store import TargetStore
    src = inspect.getsource(TargetStore.anonymize_old_access_logs)
    assert "family(ip_address) = 4" in src and "set_masklen(ip_address::cidr, 24)" in src
    assert "family(ip_address) = 6" in src and "set_masklen(ip_address::cidr, 48)" in src


def test_daily_series_fills_zeros():
    # Item 5: dias sem dado viram 0 (7 pontos p/ "7 dias"). Já entregue no Prompt 2.
    import api.admin_analytics as aa
    days = ["2026-07-15", "2026-07-16", "2026-07-17"]
    out = aa.assemble_daily_series([{"day": "2026-07-16", "visitors_br": 9, "scans": 2, "accounts": 1}], days)
    assert out["dates"] == days
    assert out["visitors_br"] == [0, 9, 0] and out["scans"] == [0, 2, 0]
