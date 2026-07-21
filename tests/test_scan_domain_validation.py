"""Fix de segurança (2026-07-21) — o scanner aceitava qualquer string (ex.: `<script>…`) e gerava
score, refletindo o payload no corpo da página. Testa a barreira `_valid_scan_domain` (pura) e o
400 `invalid_domain` em `/scan/result` e `/scan/summary` ANTES de escanear. Offline."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import api.main as m


# --------------------------------------------------------------------------- #
# 1. _valid_scan_domain (puro)
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("raw,expected", [
    ("exemplo.com.br", "exemplo.com.br"),
    ("https://exemplo.com.br/path?x=1", "exemplo.com.br"),
    ("http://www.hotel.com.br", "hotel.com.br"),          # strip www + protocolo
    ("sub.dominio.com.br", "sub.dominio.com.br"),          # subdomínio
    ("EXEMPLO.COM.BR", "exemplo.com.br"),                  # lowercase
])
def test_valid_domains_accepted(raw, expected):
    assert m._valid_scan_domain(raw) == expected


@pytest.mark.parametrize("raw", [
    "<script>alert(1)</script>",   # tag/XSS
    'exemplo."onerror".com',       # aspas
    "a b.com",                     # espaço
    "naoexiste",                   # sem TLD/ponto
    "localhost",                   # sem TLD
    "192.168.1.1",                 # IP (TLD não-alfabético)
    "x.c",                         # TLD < 2
    "",                            # vazio
    "http://",                     # sem host
    "-inicio.com",                 # label começa com hífen
])
def test_invalid_inputs_rejected(raw):
    assert m._valid_scan_domain(raw) is None


# --------------------------------------------------------------------------- #
# 2. Endpoints — 400 invalid_domain antes de escanear
# --------------------------------------------------------------------------- #

@pytest.fixture
def client():
    return TestClient(m.app, raise_server_exceptions=False)


@pytest.mark.parametrize("bad", ["<script>alert(1)</script>", "naoexiste", "a b.com", "localhost"])
def test_scan_result_rejects_invalid(client, bad):
    r = client.get("/scan/result", params={"url": bad})
    assert r.status_code == 400
    body = r.json()
    assert body["error"] == "invalid_domain"
    assert "domínio válido" in body["detail"]


def test_scan_summary_rejects_invalid(client):
    r = client.get("/scan/summary", params={"url": "<script>alert(1)</script>"})
    assert r.status_code == 400 and r.json()["error"] == "invalid_domain"


def test_scan_result_empty_url_rejected(client):
    # ?url= vazio → 400 (o front trata isso antes, mas o backend é a barreira real)
    r = client.get("/scan/result", params={"url": ""})
    assert r.status_code == 400 and r.json()["error"] == "invalid_domain"


def test_valid_domain_passes_validation():
    # não faz o scan real aqui (custa rede); garante que a barreira NÃO bloqueia um domínio real.
    assert m._valid_scan_domain("igoove.com") == "igoove.com"
