"""Testes do enriquecimento por IA (KL-47A). Offline — mock de call_openai, sem rede."""

from __future__ import annotations

import asyncio

import scanner.ai_enrichment as ai
from scanner.ai_enrichment import (
    extract_clean_text, build_user_prompt, merge_ai_into_profile, SECTORS)


def _run(coro):
    return asyncio.run(coro)


def _mock_call(monkeypatch, result):
    async def _f(system, user, max_tokens=500):
        return result
    monkeypatch.setattr(ai, "call_openai", _f)
    monkeypatch.setattr(ai, "OPENAI_API_KEY", "sk-test")


def _result(**kw):
    base = {"sector": "hotel", "sector_confidence": 0.95, "company_name": "Empresa X",
            "description": "Um negócio.", "contacts_found": {}, "business_type": "hotel"}
    base.update(kw)
    return base


# --- 1-3: classificação de setor ------------------------------------------- #

def test_ai_classify_hotel(monkeypatch):
    _mock_call(monkeypatch, _result(sector="hotel", sector_confidence=0.95))
    r = _run(ai.ai_enrich("pousada.com.br", "<html>pousada à beira-mar</html>"))
    assert r["sector"] == "hotel" and r["sector_confidence"] > 0.8


def test_ai_classify_ecommerce(monkeypatch):
    _mock_call(monkeypatch, _result(sector="ecommerce", sector_confidence=0.9))
    assert _run(ai.ai_enrich("loja.com.br", "loja online frete"))["sector"] == "ecommerce"


def test_ai_classify_long_tail(monkeypatch):
    # setor que o regex nunca pegaria
    _mock_call(monkeypatch, _result(sector="consultoria", sector_confidence=0.88))
    r = _run(ai.ai_enrich("rh.com.br", "consultoria de recursos humanos"))
    assert r["sector"] == "consultoria" and "consultoria" in SECTORS


# --- 4-5: contatos + descrição --------------------------------------------- #

def test_ai_contact_extraction(monkeypatch):
    _mock_call(monkeypatch, _result(contacts_found={"phone": "(48) 3333-4444", "email": None,
                                                    "whatsapp": None}))
    r = _run(ai.ai_enrich("x.com.br", "Ligue para nós: 48 3333-4444"))
    assert r["contacts_found"]["phone"] == "(48) 3333-4444"


def test_ai_description(monkeypatch):
    _mock_call(monkeypatch, _result(description="Clínica odontológica em Floripa."))
    assert "odontológica" in _run(ai.ai_enrich("x.com.br", "..."))["description"]


# --- 6-8: fallback --------------------------------------------------------- #

def test_ai_fallback_no_key(monkeypatch):
    monkeypatch.setattr(ai, "OPENAI_API_KEY", None)
    assert _run(ai.ai_enrich("x.com.br", "texto")) is None
    assert _run(ai.call_openai("s", "u")) is None


def test_ai_fallback_call_returns_none(monkeypatch):
    _mock_call(monkeypatch, None)   # erro de rede/parse -> call_openai devolve None
    assert _run(ai.ai_enrich("x.com.br", "texto")) is None


def test_call_openai_swallows_errors(monkeypatch):
    # httpx explode -> call_openai captura e devolve None (nunca levanta).
    monkeypatch.setattr(ai, "OPENAI_API_KEY", "sk-test")

    class _Boom:
        def __init__(self, *a, **k): ...
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def post(self, *a, **k): raise RuntimeError("network down")
    monkeypatch.setattr(ai.httpx, "AsyncClient", _Boom)
    assert _run(ai.call_openai("s", "u")) is None


# --- 9: setor inválido normalizado ----------------------------------------- #

def test_ai_invalid_sector_normalized(monkeypatch):
    _mock_call(monkeypatch, _result(sector="setor-inexistente"))
    assert _run(ai.ai_enrich("x.com.br", "texto"))["sector"] == "outro"


# --- 10-11: complementa, não sobrescreve ----------------------------------- #

def test_merge_does_not_overwrite_regex():
    profile = {"commercial_email": "regex@x.com.br", "phone": "(11) 1111-1111"}
    changed = merge_ai_into_profile(profile, {
        "company_name": "Empresa X", "description": "desc",
        "contacts_found": {"email": "ia@x.com.br", "phone": "(22) 2222-2222"}})
    assert profile["commercial_email"] == "regex@x.com.br"   # mantém o do regex
    assert profile["phone"] == "(11) 1111-1111"
    assert profile["company_name"] == "Empresa X"            # campo vazio preenchido
    assert "company_name" in changed and "commercial_email" not in changed


def test_merge_fills_empty():
    profile = {}
    merge_ai_into_profile(profile, {"contacts_found": {"email": "novo@x.com.br"}})
    assert profile["commercial_email"] == "novo@x.com.br"


# --- 12: controle de custo (truncagem) ------------------------------------- #

def test_extract_clean_text_strips_and_truncates():
    html = "<script>var x=1;</script><style>.a{}</style><p>Olá " + "a" * 5000 + "</p>"
    text = extract_clean_text(html)
    assert "var x" not in text and "<p>" not in text
    assert text.startswith("Olá") and len(text) <= 3000


def test_build_user_prompt_truncates_and_includes_current():
    # domínio sem a letra "b" para contar só o corpo truncado
    prompt = build_user_prompt("site.exemplo", "b" * 4000, {"company_name": "Atual", "sector": "hotel"})
    assert "Atual" in prompt and "Setor atual: hotel" in prompt
    assert prompt.count("b") == 3000   # corpo truncado em 3000 chars
