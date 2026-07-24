"""KL-44 P5 — indicadores técnicos de privacidade + selo + benchmark. Offline.

Cobre: os 8 checks puros de privacidade (PASS/FAIL + disclaimer), o score separado, o
endpoint do selo (público, CORS, cache), o benchmark rico (distribuição) e o admin
privacy-stats. Nunca usa termos de conformidade ("certificado"/"compliant")."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import api.main as m
from scanner import privacy_checks as pc


# --------------------------------------------------------------------------- #
# A) Os 8 indicadores (puros)
# --------------------------------------------------------------------------- #

_FULL_HTML = """<html><head>
<script src="https://cdn.cookieyes.com/client_data/x.js"></script>
</head><body>
<form action="/contato"><input name="email"></form>
<a href="/politica-de-privacidade">Política de Privacidade</a>
<a href="/politica-de-cookies">Política de Cookies</a>
<a href="/direitos">Exercer seus direitos</a>
<p>Nosso Encarregado (DPO) responde em dpo@x.com.br</p>
</body></html>"""

_SEC_HEADERS = {"Strict-Transport-Security": "max-age=1",
                "Content-Security-Policy": "default-src 'self'",
                "X-Content-Type-Options": "nosniff"}


def test_analyze_full_site_scores_8():
    r = pc.analyze(_FULL_HTML, _SEC_HEADERS, [], "https://x.com.br")
    assert r["score"] == 8 and r["total"] == 8
    assert all(c["status"] == "PASS" for c in r["checks"])
    assert r["disclaimer"] and "não constitui assessoria" in r["disclaimer"].lower()


def test_analyze_empty_site_scores_low():
    # sem nada: policy/consent/dsar/dpo/cookie_policy FAIL; sem form → https/headers PASS;
    # sem cookies de rastreio → third_party PASS. Score = 3.
    r = pc.analyze("<html><body>oi</body></html>", {}, [], "https://x.com.br")
    ids_pass = {c["id"] for c in r["checks"] if c["status"] == "PASS"}
    assert ids_pass == {"third_party_cookies", "https_forms", "form_security_headers"}
    assert r["score"] == 3


def test_privacy_policy_detected_by_text():
    links = pc.extract_links('<a href="/x">LGPD e proteção de dados</a>')
    assert pc.check_privacy_policy("", links)["status"] == "PASS"


def test_cookie_consent_by_cmp_and_class_and_text():
    assert pc.check_cookie_consent('<script src="onetrust-x"></script>')["status"] == "PASS"
    assert pc.check_cookie_consent('<div class="cookie-banner">x</div>')["status"] == "PASS"
    assert pc.check_cookie_consent("<p>Usamos cookies neste site</p>")["status"] == "PASS"
    assert pc.check_cookie_consent("<p>nada</p>")["status"] == "FAIL"


def test_third_party_cookies_negative_check():
    bad = pc.check_third_party_cookies(["_ga=GA1.2; Path=/", "sess=abc"])
    assert bad["status"] == "FAIL" and "_ga" in bad["evidence"]
    good = pc.check_third_party_cookies(["sess=abc; HttpOnly"])
    assert good["status"] == "PASS"


def test_https_forms_fails_without_https():
    assert pc.check_https_forms("<form></form>", "http://x.com.br")["status"] == "FAIL"
    assert pc.check_https_forms("<form></form>", "https://x.com.br")["status"] == "PASS"
    assert pc.check_https_forms("<p>sem form</p>", "http://x.com.br")["status"] == "PASS"


def test_form_security_headers():
    assert pc.check_form_security_headers("<form></form>", _SEC_HEADERS)["status"] == "PASS"
    assert pc.check_form_security_headers("<form></form>", {})["status"] == "FAIL"
    assert pc.check_form_security_headers("<p>sem form</p>", {})["status"] == "PASS"


def test_every_check_has_lgpd_ref_and_severity():
    r = pc.analyze(_FULL_HTML, _SEC_HEADERS, ["_ga=x"], "https://x.com.br")
    for c in r["checks"]:
        assert c["lgpd_ref"] and c["severity"] in ("high", "medium", "low")
        assert c["status"] in ("PASS", "FAIL")


def test_no_compliance_language():
    # regra inviolável: nunca "certificado"/"compliant"/"conformidade" nos textos dos checks
    r = pc.analyze(_FULL_HTML, _SEC_HEADERS, [], "https://x.com.br")
    blob = " ".join(c["name"] + " " + c["evidence"] for c in r["checks"]).lower()
    for banned in ("certificado", "compliant", "aprovado", "em conformidade"):
        assert banned not in blob


# --------------------------------------------------------------------------- #
# B) Endpoints — selo / benchmark / admin privacy-stats
# --------------------------------------------------------------------------- #

class FakeStore:
    def __init__(self):
        self.targets = {"x.com.br": {"id": 1, "domain": "x.com.br", "url": "https://x.com.br",
                                     "last_scan_score": 73, "last_semaphore": "amarelo"}}
        self.scan = {"id": 9, "score": 73, "semaphore": "amarelo", "fail_count": 2,
                     "checks_json": {"results": [], "score": {"score": 73},
                                     "privacy": {"score": 5, "total": 8, "checks": []}},
                     "scanned_at": None}

    async def get_target_by_domain(self, d):
        return self.targets.get(d.lower().strip())

    async def get_latest_scan_full(self, tid):
        return self.scan if tid == 1 else None

    async def get_site_profile(self, tid):  # KL-98: selo por-site (enabled/style)
        return {"seal_enabled": True, "seal_style": "badge"}

    async def sector_benchmark(self, sector, min_count=10):
        if sector == "tecnologia":
            return {"sector": sector, "count": 42, "avg_score": 71, "median": 74,
                    "min_score": 20, "max_score": 98,
                    "distribution": {"green_pct": 8, "yellow_pct": 85, "red_pct": 7}}
        return None

    async def all_sector_benchmarks(self, min_count=10):
        return [{"sector": "tecnologia", "count": 42, "avg_score": 71, "median": 74}]

    async def global_avg_score(self):
        return {"avg_score": 60, "count": 8000}

    async def privacy_indicator_stats(self):
        return {"scanned": 100, "avg_privacy_score": 4.2,
                "indicators": {"cookie_consent": {"pass": 22, "fail": 78, "name": "Banner de Cookies"}}}


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("ADMIN_USER", "admin")
    monkeypatch.setenv("ADMIN_PASSWORD", "s3nha-forte")
    monkeypatch.setenv("JWT_SECRET", "x" * 64)
    store = FakeStore()
    monkeypatch.setattr(m, "get_target_store", lambda: store)
    for b in (m._seal_attempts,):
        b.clear()
    return TestClient(m.app, raise_server_exceptions=False)


def _admin(client):
    tok = client.post("/auth/login", json={"username": "admin", "password": "s3nha-forte"}).json()["token"]
    return {"Authorization": f"Bearer {tok}"}


def test_seal_endpoint_public_and_cors(client):
    r = client.get("/seal/x.com.br")
    assert r.status_code == 200
    assert r.headers.get("access-control-allow-origin") == "*"
    j = r.json()
    assert j["seal_type"] == "monitored" and j["found"] is True
    assert j["score"] == 73 and j["privacy_score"] == 5 and j["privacy_total"] == 8
    assert j["profile_url"].endswith("/site/x.com.br")
    # nunca "certificado"/"aprovado"
    assert "certif" not in str(j).lower() and "aprovad" not in str(j).lower()


def test_seal_unknown_domain(client):
    j = client.get("/seal/naoexiste.com.br").json()
    assert j["found"] is False and j["seal_type"] == "monitored"


def test_benchmark_sector_rich(client):
    j = client.get("/benchmark/tecnologia").json()
    assert j["scope"] == "sector" and j["median"] == 74
    assert j["distribution"]["yellow_pct"] == 85 and j["count"] == 42


def test_benchmark_sector_fallback_global(client):
    j = client.get("/benchmark/setorpequeno").json()
    assert j["scope"] == "global" and j["avg_score"] == 60


def test_benchmark_all(client):
    j = client.get("/benchmark/all").json()
    assert j["count"] == 1 and j["sectors"][0]["sector"] == "tecnologia"


def test_admin_privacy_stats_requires_auth(client):
    assert client.get("/admin/privacy-stats").status_code == 401


def test_admin_privacy_stats(client):
    j = client.get("/admin/privacy-stats", headers=_admin(client)).json()
    assert j["scanned"] == 100 and "cookie_consent" in j["indicators"]


def test_seal_protected_prefix_not_matched():
    # /seal/{domain} é público (não cai nos prefixos protegidos)
    assert m._is_protected("/seal/x.com.br") is False
