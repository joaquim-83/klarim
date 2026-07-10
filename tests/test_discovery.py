"""Testes das partes puras do Discovery (offline, sem rede/DB)."""

from __future__ import annotations

import asyncio

from discovery.fingerprint import detect_platform
import discovery.contact as contact
from discovery.contact import (
    extract_email, _is_junk, _best_email, _collect_emails, _is_valid_email,
    email_has_mx, email_mx_status, _clean_email,
)
from discovery.ct_client import CTClient

# A cobertura do classificador de setor vive em tests/test_classifier.py (refino KL-11).


# --- fingerprint ----------------------------------------------------------- #

def test_detect_platform():
    assert detect_platform("https://x.com", "usa static.cdn-website.com aqui") == "duda"
    assert detect_platform("https://x.com", "<link href='/wp-content/x.css'>") == "wordpress"
    assert detect_platform("https://x.com", "bundle create-react-app") == "cra"
    assert detect_platform("https://x.com", "static.wixstatic.com") == "wix"
    assert detect_platform("https://x.com", "cdn.shopify.com/x") == "shopify"
    assert detect_platform("https://x.com", "sqsp.net asset") == "squarespace"
    assert detect_platform("https://x.com", "nada aqui") == "unknown"


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


def test_clean_email():
    # o bug de produção: %20 no início envenenava o batch do Resend
    assert _clean_email("%20contato@envioz.com.br") == "contato@envioz.com.br"
    assert _clean_email("contato@test.com\n") == "contato@test.com"
    assert _clean_email(" Contato@Test.COM ") == "contato@test.com"
    assert _clean_email("a%40b.com") == "a@b.com"          # %40 -> @
    assert _clean_email("x\ty@z.com") == "xy@z.com"        # tab removido
    assert _clean_email("x\xa0@z.com") == "x@z.com"        # nbsp removido
    assert _clean_email("") == ""


def test_collect_emails_cleans_url_encoded():
    # o e-mail sujo extraído do HTML sai limpo (sem %20)
    html = 'contato: %20contato@envioz.com.br e reservas@hotelx.com.br'
    got = _collect_emails(html)
    assert "contato@envioz.com.br" in got
    assert not any("%20" in e or " " in e for e in got)


def test_is_valid_email_rejects_garbage_and_placeholders():
    # lixo do incidente 08/07 (KL-19)
    assert not _is_valid_email("_@astro.dwg1vcjs.css")      # local curto + .css
    assert not _is_valid_email("seuemail@email.com.br")     # placeholder
    assert not _is_valid_email("logo@site.png")             # extensão de imagem
    assert not _is_valid_email("x@example.com.br")          # domínio de exemplo
    assert not _is_valid_email("a@b")                       # sem TLD
    assert not _is_valid_email("")                          # vazio
    # e-mails reais passam
    assert _is_valid_email("contato@hotelreal.com.br")
    assert _is_valid_email("reservas@verdegreen.com.br")


def test_best_email_filters_invalid():
    # mistura de lixo + placeholder + um válido -> retorna o válido
    emails = ["_@astro.dwg1vcjs.css", "seuemail@email.com.br", "reservas@hotelx.com.br"]
    assert _best_email(emails, "hotelx.com.br") == "reservas@hotelx.com.br"
    # só lixo -> None
    assert _best_email(["_@x.css", "seuemail@email.com.br"], "hotelx.com.br") is None


def test_extract_email_skips_garbage_mailto(monkeypatch):
    # MX ok (não bate na rede): valida a extração, não o DNS.
    monkeypatch.setattr(contact, "_mx_status_cached", lambda d: "ok")
    html = ('<a href="mailto:_@astro.dwg1vcjs.css">x</a> '
            'contato: reservas@hotelx.com.br')
    assert asyncio.run(extract_email(html, "https://www.hotelx.com.br")) == "reservas@hotelx.com.br"


# --- KL-24: validação de MX ------------------------------------------------ #

def test_email_has_mx_tri_state(monkeypatch):
    monkeypatch.setattr(contact, "_mx_status_cached",
                        lambda d: {"tem-mx.com.br": "ok", "sem-mx.com.br": "no_mx"}.get(d, "unknown"))
    assert email_has_mx("a@tem-mx.com.br") is True
    assert email_has_mx("a@sem-mx.com.br") is False       # no_mx -> rejeita
    assert email_has_mx("a@incerto.com.br") is True        # unknown -> fail-open


def test_extract_email_rejects_no_mx(monkeypatch):
    # domínio do site sem MX -> extração devolve None (não aceita e-mail que bounca)
    monkeypatch.setattr(contact, "_mx_status_cached", lambda d: "no_mx")
    html = 'contato: reservas@hotelx.com.br'
    assert asyncio.run(extract_email(html, "https://www.hotelx.com.br")) is None


def test_extract_email_skips_no_mx_and_takes_next(monkeypatch):
    # o do mesmo domínio (sem MX) é pulado; o do outro (com MX) é aceito
    def _status(domain):
        return "no_mx" if domain == "hotelx.com.br" else "ok"

    monkeypatch.setattr(contact, "_mx_status_cached", _status)
    html = 'reservas@hotelx.com.br e geral@grupohotel.com.br'
    assert asyncio.run(extract_email(html, "https://www.hotelx.com.br")) == "geral@grupohotel.com.br"


def test_mx_status_cache_avoids_repeat_lookup(monkeypatch):
    calls = {"n": 0}

    def _raw(domain):
        calls["n"] += 1
        return "ok"

    contact._mx_status_cached.cache_clear()
    monkeypatch.setattr(contact, "_mx_status", _raw)
    assert email_mx_status("a@cache-test-kl24.com.br") == "ok"
    assert email_mx_status("b@cache-test-kl24.com.br") == "ok"  # mesmo domínio
    assert calls["n"] == 1  # segundo veio do cache
    contact._mx_status_cached.cache_clear()


# --- KL-19: timeout por domínio -------------------------------------------- #

def test_run_cycle_skips_timed_out_domain():
    from discovery.worker import DiscoveryWorker

    w = DiscoveryWorker()
    w.pause_s = 0
    w.domain_timeout = 0.15
    w.batch_size = 10

    class FakeStore:
        async def domain_exists(self, d):
            return False

    w.store = FakeStore()

    async def fake_get(stats):
        stats["source"] = "ct_poller"
        stats["buffer"] = 2
        return ["slow.com.br", "fast.com.br"]

    w._get_domains = fake_get

    async def fake_proc(domain, stats):
        if domain == "slow.com.br":
            await asyncio.sleep(5)          # > domain_timeout → wait_for cancela
        else:
            stats["registered"] += 1

    w._process_domain = fake_proc

    stats = asyncio.run(w.run_cycle())
    assert stats["timeouts"] == 1          # slow foi pulado
    assert stats["registered"] == 1        # fast processou (loop continuou)
    assert stats["processed"] == 2


def test_collect_emails_mailto_and_text():
    html = '<a href="mailto:contato@x.com.br?subject=oi">x</a> escreva para vendas@x.com.br'
    got = _collect_emails(html)
    assert "contato@x.com.br" in got and "vendas@x.com.br" in got


def test_extract_email_from_html_no_network(monkeypatch):
    # MX ok (não bate na rede — o CI tem DNS e hotelx.com.br não existe).
    monkeypatch.setattr(contact, "_mx_status_cached", lambda d: "ok")
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
