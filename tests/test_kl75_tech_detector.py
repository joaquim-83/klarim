"""KL-75 — enriquecimento tecnográfico (Prompt 1).

Cobre a função pura `detect_tech_stack` (headers/scripts/meta/dns/ssl/cookies + status),
a integração no scan worker (`persist_tech_detection`, resiliente), o endpoint público de
badges e os helpers de API/backfill. Offline — sem rede, sem Postgres.
"""

from __future__ import annotations

import asyncio
import gzip
import json

import pytest
from fastapi.testclient import TestClient

import api.main as m
from scanner.tech_detector import detect_tech_stack, classify_site_status
from scanner.main import persist_tech_detection


# --------------------------------------------------------------------------- #
# Grupo A — detect_tech_stack (função pura)
# --------------------------------------------------------------------------- #

def _names(result):
    return {t["name"] for t in result["technologies"]}


def _tech(result, name):
    return next((t for t in result["technologies"] if t["name"] == name), None)


def test_nginx_header_detects_webserver_with_version():
    r = detect_tech_stack({"Server": "nginx/1.24.0"}, "", {}, {})
    t = _tech(r, "nginx")
    assert t and t["category"] == "hosting" and t["subcategory"] == "webserver"
    assert t["version"] == "1.24.0" and t["source"] == "header"


def test_apache_without_version():
    r = detect_tech_stack({"Server": "Apache"}, "", {}, {})
    t = _tech(r, "apache")
    assert t and t["version"] is None


def test_gtag_script_detects_ga4_with_measurement_id():
    html = '<script src="https://www.googletagmanager.com/gtag/js?id=G-ABC1234"></script>'
    r = detect_tech_stack({}, html, {}, {})
    t = _tech(r, "google_analytics_4")
    assert t and t["version"] == "G-ABC1234" and t["category"] == "analytics"


def test_ua_analytics_legacy():
    html = '<script src="https://www.googletagmanager.com/gtag/js?id=UA-12345-6"></script>'
    r = detect_tech_stack({}, html, {}, {})
    assert _tech(r, "google_analytics_ua")["version"] == "UA-12345-6"


def test_cookie_phpsessid_detects_php():
    r = detect_tech_stack({"Set-Cookie": "PHPSESSID=abc; path=/; HttpOnly"}, "", {}, {})
    t = _tech(r, "php")
    assert t and t["source"] == "cookie" and t["category"] == "hosting"


def test_mx_google_detects_workspace():
    r = detect_tech_stack({}, "", {"mx": ["aspmx.l.google.com"]}, {})
    assert r["email_provider"] == "google_workspace"
    assert _tech(r, "google_workspace")["category"] == "email"


def test_mx_outlook_detects_microsoft():
    r = detect_tech_stack({}, "", {"mx": ["empresa-com.mail.protection.outlook.com"]}, {})
    assert r["email_provider"] == "microsoft_365"


def test_ns_cloudflare_detects_dns_provider():
    r = detect_tech_stack({}, "", {"ns": ["ana.ns.cloudflare.com"]}, {})
    assert r["dns_provider"] == "cloudflare"


def test_ns_awsdns_detects_route53():
    r = detect_tech_stack({}, "", {"ns": ["ns-1.awsdns-01.org"]}, {})
    assert r["dns_provider"] == "aws_route53"


def test_ssl_san_extracts_related_domains_excludes_wildcard_and_thirdparty():
    ssl = {"cert": {"subject_cn": "hotel.com.br", "san": [
        "hotel.com.br", "www.hotel.com.br", "loja.hotel.com.br",
        "*.hotel.com.br", "cdn.terceiro.com"]}}
    r = detect_tech_stack({}, "", {}, ssl)
    assert r["related_domains"] == ["hotel.com.br", "loja.hotel.com.br", "www.hotel.com.br"]
    assert "cdn.terceiro.com" not in r["related_domains"]


def test_ssl_wildcard_only_records_base_domain():
    ssl = {"cert": {"subject_cn": "clinica.com.br", "san": ["*.clinica.com.br"]}}
    r = detect_tech_stack({}, "", {}, ssl)
    assert r["related_domains"] == ["clinica.com.br"]


def test_ssl_issuer_lets_encrypt_as_tech():
    ssl = {"cert": {"subject_cn": "x.com.br", "issuer_cn": "R3", "san": ["x.com.br"]}}
    r = detect_tech_stack({}, "", {}, ssl)
    assert _tech(r, "lets_encrypt")["subcategory"] == "certificado"


def test_ssl_organization_becomes_company_name():
    ssl = {"cert": {"subject_cn": "x.com.br", "subject_o": "Empresa X LTDA", "san": []}}
    r = detect_tech_stack({}, "", {}, ssl)
    assert r["company_name"] == "Empresa X LTDA"


def test_parking_pattern_status_parked():
    assert classify_site_status(200, "Este domínio está à venda", None, False) == "parked"
    assert classify_site_status(200, "Domain is parked here", None, False) == "parked"


def test_empty_html_status_abandonado():
    assert classify_site_status(200, "oi", None, False) == "abandonado"
    # detect_tech_stack deriva status por conteúdo (assume 200)
    assert detect_tech_stack({}, "", {}, {})["site_status"] == "abandonado"


def test_http_503_status_fora_do_ar():
    assert classify_site_status(503, "<html>...</html>", None, True) == "fora_do_ar"


def test_status_all_branches():
    assert classify_site_status(None, "", None, False) == "dominio_inativo"
    assert classify_site_status(0, "", None, False) == "dominio_inativo"
    assert classify_site_status(403, "", None, False) == "bloqueado"
    assert classify_site_status(301, "", None, False) == "ativo"
    big = "<html>" + "x" * 800 + "<script></script></html>"
    assert classify_site_status(200, big, None, True) == "ativo"


def test_dedup_same_tech_from_header_and_cookie_single_entry_keeps_version():
    # php vem do header (com versão) E do cookie (sem) → 1 entry, com a versão.
    r = detect_tech_stack(
        {"X-Powered-By": "PHP/8.2.1", "Set-Cookie": "PHPSESSID=z"}, "", {}, {})
    phps = [t for t in r["technologies"] if t["name"] == "php"]
    assert len(phps) == 1 and phps[0]["version"] == "8.2.1"


def test_empty_inputs_no_error_empty_list():
    r = detect_tech_stack({}, "", {}, {})
    assert r["technologies"] == []
    assert r["email_provider"] is None and r["dns_provider"] is None
    assert r["related_domains"] == [] and r["verified_platforms"] == []


def test_none_inputs_do_not_raise():
    r = detect_tech_stack(None, None, None, None)
    assert r["technologies"] == [] and r["company_name"] is None


def test_meta_generator_detects_cms_with_version():
    html = '<meta name="generator" content="WordPress 6.4.2">'
    r = detect_tech_stack({}, html, {}, {})
    t = _tech(r, "wordpress")
    assert t and t["version"] == "6.4.2" and t["source"] == "meta"


def test_verified_platforms_from_txt_and_meta_spf_ignored():
    dns = {"txt": ["v=spf1 include:_spf.google.com ~all",
                   "google-site-verification=tok123",
                   "facebook-domain-verification=fb456"]}
    r = detect_tech_stack({}, "", dns, {})
    assert set(r["verified_platforms"]) == {"google", "facebook"}


def test_verified_platform_from_meta_tag():
    html = '<meta name="google-site-verification" content="abc">'
    r = detect_tech_stack({}, html, {}, {})
    assert "google" in r["verified_platforms"]
    assert _tech(r, "google_search_console") is not None


def test_header_case_insensitive():
    lower = detect_tech_stack({"server": "nginx"}, "", {}, {})
    upper = detect_tech_stack({"SERVER": "nginx"}, "", {}, {})
    assert _tech(lower, "nginx") and _tech(upper, "nginx")


def test_cdn_and_platform_header_fingerprints():
    r = detect_tech_stack(
        {"CF-RAY": "7abc", "X-Shopify-Stage": "production"}, "", {}, {})
    assert _tech(r, "cloudflare_cdn")["category"] == "cdn"
    assert _tech(r, "shopify")["category"] == "ecommerce"


def test_payment_chat_ecommerce_security_scripts():
    html = ("sdk.mercadopago.com tawk.to woocommerce recaptcha/api.js")
    r = detect_tech_stack({}, html, {}, {})
    names = _names(r)
    assert {"mercado_pago", "tawk_to", "woocommerce", "recaptcha"} <= names
    assert _tech(r, "mercado_pago")["category"] == "pagamento"
    assert _tech(r, "tawk_to")["category"] == "chat"


def test_schema_types_extracted():
    html = '<script type="application/ld+json">{"@type": "Hotel", "name": "X"}</script>'
    r = detect_tech_stack({}, html, {}, {})
    assert "Hotel" in r["schema_types"]


def test_complex_real_hotel_combination():
    r = detect_tech_stack(
        headers={"Server": "nginx/1.24", "X-Powered-By": "PHP/8.1", "CF-RAY": "x"},
        html='<script src="https://www.googletagmanager.com/gtag/js?id=G-XYZ"></script>'
             '<script src="https://sdk.mercadopago.com/js/v2"></script>',
        dns={"mx": ["aspmx.l.google.com"], "ns": ["ns.cloudflare.com"]},
        ssl={"cert": {"subject_cn": "hotel.com.br", "issuer_cn": "R3",
                      "san": ["hotel.com.br", "www.hotel.com.br"]}})
    names = _names(r)
    assert {"nginx", "php", "cloudflare_cdn", "google_analytics_4", "mercado_pago",
            "google_workspace", "cloudflare", "lets_encrypt"} <= names
    assert r["email_provider"] == "google_workspace"
    assert r["site_status"] == "ativo"


# --------------------------------------------------------------------------- #
# Grupo B — integração no scan worker (persist_tech_detection)
# --------------------------------------------------------------------------- #

class _CapturingStore:
    def __init__(self, fail_on=None):
        self.calls = {}
        self.fail_on = fail_on

    async def save_tech_stack(self, target_id, scan_id, technologies):
        if self.fail_on == "save_tech_stack":
            raise RuntimeError("boom")
        self.calls["save_tech_stack"] = (target_id, scan_id, technologies)
        return len(technologies)

    async def update_target_tech_fields(self, target_id, email_provider, related_domains,
                                        site_type=None):
        self.calls["update_target_tech_fields"] = (target_id, email_provider,
                                                   related_domains, site_type)

    async def fill_empty_company_name(self, target_id, company_name):
        self.calls["fill_empty_company_name"] = (target_id, company_name)
        return True

    async def save_site_status(self, target_id, status, http_code=None, response_time_ms=None):
        self.calls["save_site_status"] = (target_id, status, http_code, response_time_ms)


def test_persist_tech_detection_writes_everything():
    store = _CapturingStore()
    resp = {
        "http_status": 200, "response_time_ms": 90,
        "headers": {"Server": "nginx/1.25"},
        "html": '<script src="/gtag/js?id=G-AAA"></script>',
        "dns": {"mx": ["aspmx.l.google.com"], "ns": ["ns.cloudflare.com"]},
        "ssl": {"cert": {"subject_cn": "x.com.br", "subject_o": "X SA", "san": ["x.com.br"]}},
    }
    result = asyncio.run(persist_tech_detection(store, 7, 42, resp))
    assert result["technologies"]
    assert store.calls["save_tech_stack"][0] == 7 and store.calls["save_tech_stack"][1] == 42
    assert store.calls["update_target_tech_fields"][1] == "google_workspace"
    assert store.calls["fill_empty_company_name"][1] == "X SA"
    # status autoritativo usa o http_status real
    assert store.calls["save_site_status"][1] == "ativo"
    assert store.calls["save_site_status"][2] == 200


def test_persist_tech_detection_resilient_to_store_error():
    store = _CapturingStore(fail_on="save_tech_stack")
    resp = {"http_status": 200, "headers": {"Server": "nginx"}, "html": "",
            "dns": {}, "ssl": {}}
    # Não deve levantar — o scan já está persistido.
    result = asyncio.run(persist_tech_detection(store, 1, 1, resp))
    assert result == {}


def test_persist_tech_detection_empty_response():
    store = _CapturingStore()
    assert asyncio.run(persist_tech_detection(store, 1, 1, {})) == {}
    assert store.calls == {}


def test_persist_tech_detection_status_uses_real_http_code():
    store = _CapturingStore()
    # HTML vazio mas 503 → fora_do_ar (não "abandonado" do conteúdo)
    resp = {"http_status": 503, "headers": {}, "html": "", "dns": {}, "ssl": {}}
    asyncio.run(persist_tech_detection(store, 3, 9, resp))
    assert store.calls["save_site_status"][1] == "fora_do_ar"


# --------------------------------------------------------------------------- #
# Grupo C — endpoint público + helpers de API
# --------------------------------------------------------------------------- #

class FakeStore:
    def __init__(self):
        self.public_visible = True

    async def get_target_by_domain(self, domain):
        if domain == "naoexiste.com.br":
            return None
        return {"id": 5, "domain": domain, "status": "scanned",
                "email_provider": "google_workspace", "related_domains": ["www." + domain]}

    async def get_site_profile(self, target_id):
        return {"public_visible": self.public_visible}

    async def tech_summary_by_domain(self, target_id):
        return {"has_analytics": True, "has_cdn": True, "has_payment": False,
                "has_chat": False, "has_captcha": True, "has_ecommerce": False,
                "tech_count": 6}

    async def get_tech_stack(self, target_id):
        return [{"name": "nginx", "category": "hosting", "subcategory": "webserver",
                 "version": "1.24", "source": "header", "confidence": 1.0}]

    async def get_site_status_history(self, target_id, limit=10):
        return [{"status": "ativo", "http_code": 200, "response_time_ms": 100,
                 "detected_at": None}]

    async def get_tech_adoption(self, tech_name, sector=None):
        return {"total_sites": 200, "sites_with_tech": 144, "adoption_rate": 0.72}

    async def get_target(self, target_id):
        return {"id": target_id, "domain": "hotel.com.br"}


@pytest.fixture
def store(monkeypatch):
    s = FakeStore()
    monkeypatch.setattr(m, "get_target_store", lambda: s)
    monkeypatch.setattr("discovery.store.get_target_store", lambda: s)
    return s


@pytest.fixture
def client(store):
    return TestClient(m.app, raise_server_exceptions=False)


def test_public_tech_summary_returns_badges_not_detail(client):
    r = client.get("/public/tech-summary/hotel.com.br")
    assert r.status_code == 200
    body = r.json()
    assert body["has_analytics"] is True and body["has_cdn"] is True
    assert body["has_payment"] is False and body["email_provider"] == "google_workspace"
    assert body["site_status"] == "ativo" and body["tech_count"] == 6
    # NUNCA expõe o stack detalhado (nomes/versões) no público
    assert "technologies" not in body and "related_domains" not in body


def test_public_tech_summary_unknown_domain_empty(client):
    body = client.get("/public/tech-summary/naoexiste.com.br").json()
    assert body["tech_count"] == 0 and body["has_analytics"] is False
    assert body["email_provider"] is None


def test_public_tech_summary_respects_public_visible(client, store):
    store.public_visible = False
    body = client.get("/public/tech-summary/hotel.com.br").json()
    assert body["tech_count"] == 0 and body["site_status"] is None


def test_public_tech_summary_rate_limited(client):
    headers = {"X-Forwarded-For": "9.9.9.9", "CF-Connecting-IP": "203.0.113.55"}
    codes = [client.get("/public/tech-summary/hotel.com.br", headers=headers).status_code
             for _ in range(33)]
    assert 429 in codes


def test_api_tech_adoption(store):
    res = asyncio.run(m.api_tech_adoption("wordpress", sector="hotel"))
    assert res["sites_with_tech"] == 144 and res["adoption_pct"] == "72.0%"
    assert res["sector"] == "hotel"


def test_api_tech_adoption_requires_tech(store):
    assert asyncio.run(m.api_tech_adoption(""))["status_code"] == 400


def test_api_site_tech_stack(store):
    res = asyncio.run(m.api_site_tech_stack("hotel.com.br"))
    assert res["tech_count"] == 1 and res["technologies"][0]["name"] == "nginx"
    assert res["email_provider"] == "google_workspace"
    assert res["related_domains"] == ["www.hotel.com.br"]
    assert res["site_status"] == "ativo"


def test_api_site_tech_stack_not_found(store):
    assert asyncio.run(m.api_site_tech_stack("naoexiste.com.br"))["status_code"] == 404


def test_api_site_status_history_by_domain(store):
    res = asyncio.run(m.api_site_status_history(domain="hotel.com.br", limit=5))
    assert res["count"] == 1 and res["history"][0]["status"] == "ativo"


def test_admin_tech_stack_endpoint_requires_auth(client):
    # Sob o prefixo /targets → middleware JWT admin. Sem token = barrado.
    r = client.get("/targets/5/tech-stack")
    assert r.status_code in (401, 403)


def test_as_str_list_handles_str_and_list():
    assert m._as_str_list(["a", "b"]) == ["a", "b"]
    assert m._as_str_list('["x","y"]') == ["x", "y"]
    assert m._as_str_list(None) == []
    assert m._as_str_list("nao-json") == []


# --------------------------------------------------------------------------- #
# Grupo D — backfill (decode + prefixo)
# --------------------------------------------------------------------------- #

def test_backfill_decode_archive_roundtrip():
    from scripts.backfill_tech_stack import decode_archive
    payload = {"scan_id": 1, "html": "<html>", "headers": {"Server": "nginx"}}
    raw = gzip.compress(json.dumps(payload).encode("utf-8"))
    assert decode_archive(raw) == payload


def test_backfill_blob_prefix():
    from scripts.backfill_tech_stack import _blob_prefix
    assert _blob_prefix("2026-07-19") == "2026/07/19/"
    assert _blob_prefix(None) == ""
