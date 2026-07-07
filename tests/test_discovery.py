"""Testes das partes puras do Discovery (offline, sem rede/DB)."""

from __future__ import annotations

import asyncio

from discovery.fingerprint import detect_platform
from discovery.classifier import classify_sector
from discovery.contact import extract_email, _is_junk, _best_email, _collect_emails
from discovery.ct_client import CTClient


# --- fingerprint ----------------------------------------------------------- #

def test_detect_platform():
    assert detect_platform("https://x.com", "usa static.cdn-website.com aqui") == "duda"
    assert detect_platform("https://x.com", "<link href='/wp-content/x.css'>") == "wordpress"
    assert detect_platform("https://x.com", "bundle create-react-app") == "cra"
    assert detect_platform("https://x.com", "static.wixstatic.com") == "wix"
    assert detect_platform("https://x.com", "cdn.shopify.com/x") == "shopify"
    assert detect_platform("https://x.com", "sqsp.net asset") == "squarespace"
    assert detect_platform("https://x.com", "nada aqui") == "unknown"


# --- classifier ------------------------------------------------------------ #

def test_classify_sector():
    assert classify_sector("hotel pousada reserva diária check-in") == ("hotel", "standard")
    assert classify_sector("clínica dentista paciente agendamento saúde")[0] == "clinica"
    assert classify_sector("clínica dentista paciente")[1] == "enterprise"
    assert classify_sector("restaurante cardápio menu delivery")[0] == "restaurante"
    assert classify_sector("restaurante cardápio menu")[1] == "basic"
    assert classify_sector("página institucional sem nada específico") == ("outro", "standard")


# --- contact --------------------------------------------------------------- #

def test_contact_junk_and_best():
    assert _is_junk("noreply@x.com")
    assert _is_junk("webmaster@x.com")
    assert _is_junk("someone@duda.co")
    assert not _is_junk("contato@hotelx.com.br")
    # prefere e-mail do mesmo domínio registrável do site
    emails = ["geral@fornecedor.com", "reservas@hotelx.com.br"]
    assert _best_email(emails, "hotelx.com.br") == "reservas@hotelx.com.br"
    # só junk -> None
    assert _best_email(["noreply@hotelx.com.br", "a@wixpress.com"], "hotelx.com.br") is None


def test_collect_emails_mailto_and_text():
    html = '<a href="mailto:contato@x.com.br?subject=oi">x</a> escreva para vendas@x.com.br'
    got = _collect_emails(html)
    assert "contato@x.com.br" in got and "vendas@x.com.br" in got


def test_extract_email_from_html_no_network():
    html = '<a href="mailto:reservas@hotelx.com.br">reservar</a>'
    assert asyncio.run(extract_email(html, "https://www.hotelx.com.br")) == "reservas@hotelx.com.br"


# --- CT filter ------------------------------------------------------------- #

def test_ct_filter():
    ct = CTClient()
    raw = [
        "*.hotelx.com.br",          # wildcard -> hotelx.com.br
        "www.hotelx.com.br",        # -> hotelx.com.br (dedup)
        "mail.hotelx.com.br",       # prefixo infra -> descartado
        "api.outro.com.br",         # prefixo infra -> descartado
        "clinica.com.br",           # ok
        "algo.cloudfront.net",      # não .com.br -> descartado
        "with space.com.br",        # espaço -> descartado
    ]
    out = ct._filter(raw, ".com.br", 50)
    assert "hotelx.com.br" in out
    assert "clinica.com.br" in out
    assert out.count("hotelx.com.br") == 1  # dedup
    assert all(d.endswith(".com.br") for d in out)
    assert "outro.com.br" not in out  # veio só de api. -> descartado
