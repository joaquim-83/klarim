"""Testes do profiler comercial (KL-50) — parsers puros + crawl multi-page. Offline."""

from __future__ import annotations

import asyncio

import httpx
import pytest

import scanner.profiler as p


# --- C. contatos ----------------------------------------------------------- #

def test_extract_contacts_all_fields():
    html = ('<a href="mailto:contato@hotel.com.br">e</a>'
            '<a href="tel:+554833334444">t</a>'
            '<a href="https://wa.me/5548999994444">w</a>'
            '<p>CNPJ 11.222.333/0001-81 — Rua das Flores, 123, Florianopolis, SC</p>')
    c = p.extract_contacts({"homepage": html}, "hotel.com.br")
    assert c["email"] == "contato@hotel.com.br"
    assert c["phone"] == "(48) 3333-4444"
    assert c["whatsapp"] == "5548999994444"
    assert c["cnpj"] == "11.222.333/0001-81"
    assert "Rua das Flores" in c["address"] and c["address"].endswith("SC")


def test_extract_contacts_prefers_same_domain_email():
    html = ('<a href="mailto:suporte@terceiro.com">x</a>'
            '<a href="mailto:reservas@hotel.com.br">y</a>')
    c = p.extract_contacts({"homepage": html}, "hotel.com.br")
    assert c["email"] == "reservas@hotel.com.br"


def test_whatsapp_from_api_and_data_phone():
    assert p.extract_contacts({"h": 'a href="https://api.whatsapp.com/send?phone=5511988887777"'}
                              )["whatsapp"] == "5511988887777"
    assert p.extract_contacts({"h": '<div data-phone="5511988887777">'}
                              )["whatsapp"] == "5511988887777"


# --- CNPJ ------------------------------------------------------------------ #

def test_validate_cnpj():
    assert p.validate_cnpj("11.222.333/0001-81") is True
    assert p.validate_cnpj("11222333000181") is True         # sem máscara
    assert p.validate_cnpj("11.222.333/0001-99") is False    # DV errado
    assert p.validate_cnpj("00.000.000/0000-00") is False    # todos iguais
    assert p.validate_cnpj("123") is False


# --- B. JSON-LD ------------------------------------------------------------ #

def test_structured_data_hotel():
    html = ('<script type="application/ld+json">'
            '{"@type":"Hotel","name":"Pousada X","telephone":"+55 48 3000-0000",'
            '"email":"r@px.com.br","address":{"streetAddress":"Av. Mar, 1",'
            '"addressLocality":"Floripa","addressRegion":"SC"},'
            '"openingHours":"Mo-Su 08:00-18:00",'
            '"sameAs":["https://instagram.com/px"]}</script>')
    s = p.extract_structured_data(html)
    assert s["sector"] == "hotel" and s["company_name"] == "Pousada X"
    assert s["phone"] == "+55 48 3000-0000" and s["email"] == "r@px.com.br"
    assert "Av. Mar, 1" in s["address"] and s["business_hours"]
    assert "https://instagram.com/px" in s["same_as"]


def test_structured_data_localbusiness_and_graph():
    html = ('<script type="application/ld+json">'
            '{"@graph":[{"@type":"LegalService","name":"Adv XYZ"}]}</script>')
    assert p.extract_structured_data(html)["sector"] == "juridico"


def test_structured_data_malformed_is_graceful():
    html = '<script type="application/ld+json">{ isto não é json }</script>'
    assert p.extract_structured_data(html) == {"same_as": []}


# --- C. redes sociais ------------------------------------------------------ #

def test_extract_social_links():
    html = ('<a href="https://instagram.com/hotelparaiso">ig</a>'
            '<a href="https://facebook.com/hotelparaiso">fb</a>'
            '<a href="https://www.linkedin.com/company/hotelparaiso">li</a>'
            '<a href="https://youtube.com/@hotelparaiso">yt</a>'
            '<a href="https://www.tiktok.com/@hotelparaiso">tt</a>'
            '<a href="https://maps.google.com/?q=hotel">map</a>'
            '<a href="/blog/dicas">b</a>'
            '<a href="https://play.google.com/store/apps/details?id=x">app</a>')
    s = p.extract_social_links({"homepage": html})
    assert s["instagram"] == "hotelparaiso" and s["facebook"] == "hotelparaiso"
    assert s["linkedin"] == "hotelparaiso" and s["tiktok"] == "hotelparaiso"
    assert "@hotelparaiso" in s["youtube"]
    assert s["google_maps_url"].startswith("maps.google")
    assert s["has_blog"] is True and s["has_app"] is True


def test_social_ignores_reserved_paths():
    html = ('<a href="https://facebook.com/sharer/sharer.php?u=x">share</a>'
            '<a href="https://facebook.com/tr?id=1">px</a>')
    s = p.extract_social_links({"homepage": html})
    assert "facebook" not in s  # sharer/tr ignorados


# --- D. tecnologias -------------------------------------------------------- #

def test_extract_technologies():
    html = ('<script src="https://www.googletagmanager.com/gtag/js?id=G-ABCDEF"></script>'
            '<script src="https://stc.pagseguro.uol.com.br/x.js"></script>'
            '<script src="https://code.jivosite.com/w"></script>'
            '<link href="wp-content/plugins/woocommerce/style.css">')
    tech = p.extract_technologies({"homepage": html}, {"x-powered-by": "PHP"})
    assert "ga4" in tech["analytics"]
    assert "pagseguro" in tech["payment"]
    assert "jivochat" in tech["chat"]
    assert "woocommerce" in tech["ecommerce"]


def test_technologies_case_insensitive_and_headers():
    tech = p.extract_technologies({"h": "<div>GTM-ABC123</div>"}, {"Server": "cloudflare"})
    assert "google_tag_manager" in tech.get("analytics", [])


# --- E. infraestrutura ----------------------------------------------------- #

def test_extract_infrastructure():
    infra = p.extract_infrastructure(
        headers={"cf-ray": "abc"}, mx_records=["aspmx.l.google.com"],
        ns_records=["ns1.cloudflare.com", "ns2.cloudflare.com"],
        certificate_authority="lets_encrypt")
    assert infra["email_provider"] == "google_workspace"
    assert infra["dns_provider"] == "cloudflare" and infra["cdn"] == "cloudflare"
    assert infra["certificate_authority"] == "lets_encrypt"


def test_infra_cloudfront_and_no_match():
    assert p.extract_infrastructure(headers={"x-amz-cf-id": "z"})["cdn"] == "cloudfront"
    assert p.extract_infrastructure(headers={}, mx_records=["mx.unknownhost.net"]
                                    )["email_provider"] is None


# --- F. maturidade --------------------------------------------------------- #

def test_maturity_seven_signals():
    profile = {
        "technologies": {"analytics": ["ga4"], "payment": ["pagseguro"],
                         "cookie_consent": ["cookiebot"]},
        "instagram": "x", "facebook": "y",          # 2 redes
        "whatsapp": "5548999990000",
        "has_blog": True,
        "commercial_email": "contato@empresa.com.br",  # domínio profissional
        # sem _responsive, sem security → sinais 1,9,10 = 0
    }
    assert p.calculate_maturity_score(profile, security_score=None) == 7


def test_maturity_free_email_no_point():
    profile = {"commercial_email": "empresa@gmail.com", "technologies": {}}
    assert p.calculate_maturity_score(profile, security_score=None) == 0


# --- camada 1: crawl multi-page ------------------------------------------- #

def _fake_fetch(responses):
    """responses: {path_substring: (status, text)}; default 404."""
    async def _fetch(url, method="GET", **kwargs):
        for frag, (status, text) in responses.items():
            if url.rstrip("/").endswith(frag) or (frag == "/" and url.rstrip("/").count("/") <= 2):
                req = httpx.Request("GET", url)
                return httpx.Response(status, text=text, request=req)
        return httpx.Response(404, text="", request=httpx.Request("GET", url))
    return _fetch


def test_crawl_multipage_collects_200s(monkeypatch):
    monkeypatch.setattr(p, "fetch", _fake_fetch({
        "/": (200, "<html>home</html>"),
        "contato": (200, '<a href="mailto:c@x.com.br">e</a>'),
        "sobre": (200, "<html>sobre</html>"),
    }))
    pages = asyncio.run(p.crawl_contact_pages("https://x.com.br"))
    assert "homepage" in pages and "contato" in pages and "sobre" in pages
    assert "c@x.com.br" in pages["contato"]


def test_crawl_fallback_all_internal_404(monkeypatch):
    # Todas as internas 404 → não quebra, usa só a homepage.
    monkeypatch.setattr(p, "fetch", _fake_fetch({"/": (200, "<html>only home</html>")}))
    pages = asyncio.run(p.crawl_contact_pages("https://x.com.br"))
    assert list(pages.keys()) == ["homepage"]


def test_crawl_uses_given_homepage_html(monkeypatch):
    calls = {"n": 0}

    async def _fetch(url, method="GET", **kwargs):
        calls["n"] += 1
        return httpx.Response(404, text="", request=httpx.Request("GET", url))
    monkeypatch.setattr(p, "fetch", _fetch)
    pages = asyncio.run(p.crawl_contact_pages("https://x.com.br", homepage_html="<html>h</html>"))
    assert pages["homepage"] == "<html>h</html>"
    assert calls["n"] == len(p.CONTACT_PATHS)  # não refez a homepage


# --- edge cases ------------------------------------------------------------ #

def test_empty_and_malformed_html():
    assert p.extract_contacts({"h": ""}, "x.com")["email"] is None
    assert p.extract_social_links({"h": "<a href="})["has_blog"] is False
    assert p.extract_technologies({"h": "<<<>>>"}, {}) == {}


def test_build_profile_end_to_end(monkeypatch):
    html = ('<meta name="viewport" content="w">'
            '<script type="application/ld+json">{"@type":"Restaurant","name":"Cantina"}</script>'
            '<a href="mailto:contato@cantina.com.br">e</a>'
            '<a href="https://instagram.com/cantina">ig</a>')
    monkeypatch.setattr(p, "fetch", _fake_fetch({"/": (200, html)}))
    prof = asyncio.run(p.build_profile("https://cantina.com.br",
                                       headers={"strict-transport-security": "max-age=1"},
                                       security_score=85))
    assert prof["company_name"] == "Cantina" and prof["sector_hint"] == "restaurante"
    assert prof["commercial_email"] == "contato@cantina.com.br"
    assert prof["instagram"] == "cantina"
    assert isinstance(prof["maturity_score"], int)
    assert "_hsts" not in prof and "_responsive" not in prof  # sinais internos removidos
