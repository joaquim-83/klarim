"""Testes da taxonomia de setores (KL-54) — offline.

Cobre a fonte da verdade (`discovery/sector_taxonomy`), a normalização/aliases, o
prompt e a validação da IA, o mapeamento Schema.org do profiler, o classificador
regex nos setores novos e o endpoint público `GET /sectors`.
"""

from __future__ import annotations

import asyncio

from fastapi.testclient import TestClient

from discovery import sector_taxonomy as t
from discovery.sector_taxonomy import (
    SECTOR_TAXONOMY, VALID_SECTORS, MACRO_SECTORS, get_macro, get_label, normalize_sector)


def _run(coro):
    return asyncio.run(coro)


# --- 1-4: estrutura da taxonomia ------------------------------------------- #

# Nota: o card KL-54 fala em "~47 setores"; o dict entregue tem 48 setores + `outro`.
_NON_OUTRO = len(VALID_SECTORS) - 1


def test_taxonomy_size():
    assert len(SECTOR_TAXONOMY) == 49          # 48 setores + outro
    assert _NON_OUTRO == 48


def test_all_old_sectors_present():
    old = {"hotel", "clinica", "ecommerce", "restaurante", "escola",
           "imobiliaria", "juridico", "contabilidade", "automotivo", "condominio"}
    assert old <= VALID_SECTORS


def test_every_sector_has_macro_and_label():
    for sid, meta in SECTOR_TAXONOMY.items():
        assert meta.get("macro"), sid
        assert meta.get("label"), sid


def test_thirteen_macros_plus_outro():
    non_outro = [m for m in MACRO_SECTORS if m != "outro"]
    assert len(non_outro) == 13
    # todo macro tem label
    assert all(m in t.MACRO_LABELS for m in MACRO_SECTORS)


# --- 5-9: helpers ---------------------------------------------------------- #

def test_get_macro():
    assert get_macro("odontologia") == "saude"
    assert get_macro("hotel") == "turismo"
    assert get_macro("inexistente") == "outro"


def test_get_label():
    assert get_label("odontologia") == "Odontologia"
    assert get_label("zzz") == "zzz"          # fallback: o próprio valor


def test_normalize_cleans_and_lowercases():
    assert normalize_sector("  Odontologia ") == "odontologia"
    assert normalize_sector("Bar Lanchonete") == "bar_lanchonete"
    assert normalize_sector("padaria-confeitaria") == "padaria_confeitaria"


def test_normalize_invalid_is_outro():
    assert normalize_sector("xyz") == "outro"
    assert normalize_sector("") == "outro"
    assert normalize_sector(None) == "outro"


def test_normalize_legacy_alias_saude():
    # o genérico antigo 'saude' virou clinica (a IA refina no batch)
    assert normalize_sector("saude") == "clinica"


# --- 10-12: IA — prompt + validação ---------------------------------------- #

def test_system_prompt_lists_all_sectors():
    import scanner.ai_enrichment as ai
    for sid in VALID_SECTORS - {"outro"}:
        assert sid in ai.SYSTEM_PROMPT, f"faltou {sid} no prompt"
    assert "outro" in ai.SYSTEM_PROMPT


def _mock_ai(monkeypatch, result):
    import scanner.ai_enrichment as ai

    async def _f(system, user, max_tokens=500):
        return result
    monkeypatch.setattr(ai, "call_openai", _f)
    monkeypatch.setattr(ai, "OPENAI_API_KEY", "sk-test")


def _ai_result(**kw):
    base = {"sector": "odontologia", "sector_confidence": 0.9, "company_name": "X",
            "description": "d", "contacts_found": {}, "business_type": "b"}
    base.update(kw)
    return base


def test_ai_accepts_new_sector(monkeypatch):
    import scanner.ai_enrichment as ai
    _mock_ai(monkeypatch, _ai_result(sector="odontologia"))
    assert _run(ai.ai_enrich("dentista.com.br", "clínica odontológica"))["sector"] == "odontologia"


def test_ai_normalizes_invalid_sector(monkeypatch):
    import scanner.ai_enrichment as ai
    _mock_ai(monkeypatch, _ai_result(sector="setor-que-nao-existe"))
    assert _run(ai.ai_enrich("x.com.br", "texto"))["sector"] == "outro"


def test_ai_normalizes_legacy_saude(monkeypatch):
    import scanner.ai_enrichment as ai
    _mock_ai(monkeypatch, _ai_result(sector="saude"))
    assert _run(ai.ai_enrich("x.com.br", "texto"))["sector"] == "clinica"


# --- 13-14: Schema.org → setor fino ---------------------------------------- #

def test_schema_dentist_is_odontologia():
    from scanner import profiler as p
    html = ('<script type="application/ld+json">'
            '{"@type":"Dentist","name":"Sorriso Perfeito"}</script>')
    assert p.extract_structured_data(html)["sector"] == "odontologia"


def test_schema_bakery_is_padaria():
    from scanner import profiler as p
    html = ('<script type="application/ld+json">'
            '{"@type":"Bakery","name":"Pão Quente"}</script>')
    assert p.extract_structured_data(html)["sector"] == "padaria_confeitaria"


def test_all_schema_sectors_valid():
    from scanner.profiler import _SCHEMA_SECTOR
    assert set(_SCHEMA_SECTOR.values()) <= VALID_SECTORS


# --- classificador regex nos setores novos --------------------------------- #

def test_classifier_new_sectors_by_domain():
    from discovery.classifier import classify_by_domain
    cases = {
        "https://odontosorriso.com.br": "odontologia",
        "https://farmaciacentral.com.br": "farmacia",
        "https://academiafit.com.br": "academia",
        "https://petshopamigo.com.br": "petshop",
        "https://barbeariavip.com.br": "salao_barbearia",
        "https://faculdadexyz.com.br": "faculdade",
    }
    for url, expected in cases.items():
        got = classify_by_domain(url)
        assert got and got[0] == expected, f"{url} → {got}"


def test_classifier_price_tiers_cover_all_taxonomy():
    from discovery.classifier import DOMAIN_PATTERNS, SECTOR_KEYWORDS, PRICE_TIERS
    assert set(DOMAIN_PATTERNS) | set(SECTOR_KEYWORDS) <= VALID_SECTORS
    assert VALID_SECTORS <= set(PRICE_TIERS)   # tier para todo setor
    assert set(PRICE_TIERS.values()) == {"standard"}   # preço único


# --- 15: endpoint público /sectors ----------------------------------------- #

def test_api_sectors_endpoint():
    import api.main as m
    client = TestClient(m.app, raise_server_exceptions=False)
    r = client.get("/sectors")
    assert r.status_code == 200
    data = r.json()
    assert len(data["sectors"]) == 48          # sem 'outro'
    assert len(data["macro_sectors"]) == 13
    ids = {s["id"] for s in data["sectors"]}
    assert "odontologia" in ids and "outro" not in ids
    # cada setor traz id/label/macro
    assert all({"id", "label", "macro"} <= set(s) for s in data["sectors"])
