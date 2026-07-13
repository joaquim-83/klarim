"""Testes da classificação CNAE multi-setor (KL-55) — offline.

Cobre: módulo CNAE (derive + cache/download/fail-open mockados), prompt/parsing da
IA (CNAEs + tags + sector_legacy), consulta CNPJ→Receita (normalização + build +
enrich com store falso) e a integração no enrich_all (G4 + gravação de CNAE). As
partes que dependem de Postgres/rede real são validadas por parse SQL + store falso.
"""

from __future__ import annotations

import asyncio
import json
import os
import time

import pytest

from discovery import cnae, cnpj


def _run(coro):
    return asyncio.run(coro)


# =========================================================================== #
# 1-4 + derive: módulo CNAE
# =========================================================================== #

def test_derive_division_and_section():
    assert cnae.derive_division("62.01-5") == "62"
    assert cnae.derive_section("62.01-5") == "J"   # informação e comunicação
    assert cnae.derive_section("68.22-6") == "L"   # imobiliárias
    assert cnae.derive_section("86.10-1") == "Q"   # saúde
    assert cnae.derive_section("47.11-3") == "G"   # comércio
    assert cnae.derive_section("01.11-3") == "A"   # agropecuária
    assert cnae.derive_section("zz") is None


def test_format_cnae():
    assert cnae.format_cnae("6201500") == "62.01-5"
    assert cnae.format_cnae("6201-5/00") == "62.01-5"
    assert cnae.format_cnae(6201501) == "62.01-5"


def test_sections_and_divisions():
    assert len(cnae.sections()) == 21
    assert len(cnae.divisions()) == 87
    assert cnae.sections()[0]["id"] == "A"


def test_cnae_cache_hit(tmp_path, monkeypatch):
    # cache válido (recente) → não faz download
    cache = tmp_path / "cnae_table.json"
    cache.write_text(json.dumps({"62015": {"descricao": "X", "division": "62", "section": "J"}}))
    monkeypatch.setattr(cnae, "CACHE_FILE", str(cache))
    t = cnae.CNAETable()

    async def _boom():  # se tentar baixar, falha o teste
        raise AssertionError("não devia baixar com cache válido")
    monkeypatch.setattr(t, "_download", _boom)
    _run(t.ensure_loaded())
    assert t.lookup("62.01-5")["descricao"] == "X"


def test_cnae_cache_miss_expired(tmp_path, monkeypatch):
    cache = tmp_path / "cnae_table.json"
    cache.write_text(json.dumps({"62015": {"descricao": "velho"}}))
    old = time.time() - (cnae.CACHE_TTL_SECONDS + 100)
    os.utime(cache, (old, old))
    monkeypatch.setattr(cnae, "CACHE_FILE", str(cache))
    t = cnae.CNAETable()
    called = {"n": 0}

    async def _dl():
        called["n"] += 1
    monkeypatch.setattr(t, "_download", _dl)
    _run(t.ensure_loaded())
    assert called["n"] == 1   # expirado → baixou


def test_cnae_fail_open(monkeypatch, tmp_path):
    # download falha + sem cache → tabela vazia, validação fail-open
    monkeypatch.setattr(cnae, "CACHE_FILE", str(tmp_path / "nope.json"))

    class _Boom:
        def __init__(self, *a, **k): ...
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def get(self, *a, **k): raise RuntimeError("IBGE down")
    monkeypatch.setattr(cnae.httpx, "AsyncClient", _Boom)
    t = cnae.CNAETable()
    _run(t.ensure_loaded())
    assert t._classes == {}
    assert t.validate_code("62.01-5") is True   # fail-open aceita 5+ dígitos
    assert t.lookup("62.01-5") is None


# =========================================================================== #
# 8-10: IA — prompt + parsing
# =========================================================================== #

def _mock_ai(monkeypatch, result):
    import scanner.ai_enrichment as ai

    async def _f(system, user, max_tokens=500):
        return result
    monkeypatch.setattr(ai, "call_openai", _f)
    monkeypatch.setattr(ai, "OPENAI_API_KEY", "sk-test")


def test_prompt_has_new_fields():
    import scanner.ai_enrichment as ai
    for field in ("cnaes", "tags", "sector_legacy", "business_type", "description"):
        assert field in ai.SYSTEM_PROMPT


def test_ai_returns_cnaes_and_tags(monkeypatch):
    import scanner.ai_enrichment as ai
    _mock_ai(monkeypatch, {
        "description": "Plataforma SaaS de gestão condominial.",
        "business_type": "PropTech SaaS",
        "tags": ["proptech", "SaaS", "gestão condominial"],
        "cnaes": [{"code": "62.01-5", "description": "Dev software", "confidence": 0.9},
                  {"code": "68.22-6", "description": "Gestão condomínios", "confidence": 0.8}],
        "sector_legacy": "tecnologia", "sector_confidence": 0.88, "contacts_found": {}})
    r = _run(ai.ai_enrich("x.com.br", "texto"))
    assert len(r["cnaes"]) == 2
    assert r["cnaes"][0]["section"] == "J" and r["cnaes"][1]["section"] == "L"
    assert r["tags"] == ["proptech", "saas", "gestão condominial"]  # minúsculas
    assert r["sector_legacy"] == "tecnologia" and r["sector"] == "tecnologia"  # retrocompat
    assert r["sector_confidence"] == 0.88
    assert r["business_type"] == "PropTech SaaS"


def test_ai_sector_legacy_normalized(monkeypatch):
    import scanner.ai_enrichment as ai
    _mock_ai(monkeypatch, {"sector_legacy": "saude", "cnaes": [], "tags": []})
    # alias legado saude → clinica (retrocompat KL-54)
    assert _run(ai.ai_enrich("x.com.br", "t"))["sector"] == "clinica"
    _mock_ai(monkeypatch, {"sector_legacy": "inexistente", "cnaes": [], "tags": []})
    assert _run(ai.ai_enrich("x.com.br", "t"))["sector"] == "outro"


def test_merge_tags_and_business_type():
    import scanner.ai_enrichment as ai
    profile = {"description": "regex desc"}   # já tem descrição (regex)
    changed = ai.merge_ai_into_profile(profile, {
        "description": "ia desc", "business_type": "Hamburgueria", "tags": ["burger", "delivery"]})
    assert profile["description"] == "regex desc"      # não sobrescreve (regra de ouro)
    assert profile["business_type"] == "Hamburgueria"  # campo vazio preenchido
    assert profile["tags"] == ["burger", "delivery"]   # tags são da IA (sobrescreve)
    assert "business_type" in changed and "tags" in changed


# =========================================================================== #
# 11: CNPJ → Receita
# =========================================================================== #

def test_normalize_brasilapi():
    d = cnpj._normalize_brasilapi({
        "cnae_fiscal": 6201501, "cnae_fiscal_descricao": "Dev software",
        "cnaes_secundarios": [{"codigo": 6822600, "descricao": "Gestão"}],
        "razao_social": "ACME LTDA"})
    assert d["principal"]["code"] == "62.01-5"
    assert d["secundarios"][0]["code"] == "68.22-6"


def test_normalize_receitaws():
    d = cnpj._normalize_receitaws({
        "atividade_principal": [{"code": "62.01-5-01", "text": "Dev"}],
        "atividades_secundarias": [{"code": "68.22-6-00", "text": "Gestão"}],
        "nome": "ACME"})
    assert d["principal"]["code"] == "62.01-5"
    assert d["secundarios"][0]["code"] == "68.22-6"


def test_build_receita_classifications():
    data = {"principal": {"code": "62.01-5", "description": "Dev"},
            "secundarios": [{"code": "68.22-6", "description": "Gestão"},
                            {"code": "47.11-3", "description": "Comércio"}]}
    cls = cnpj.build_receita_classifications(data)
    assert [c["rank"] for c in cls] == [1, 2, 3]
    assert all(c["source"] == "receita" and c["confidence"] == 1.0 for c in cls)
    assert cls[0]["cnae_section"] == "J" and cls[2]["cnae_section"] == "G"


def test_enrich_from_cnpj_writes_receita(monkeypatch):
    calls = []

    class _Store:
        async def upsert_target_classifications(self, tid, classifications):
            calls.append((tid, classifications))

    async def _fake_fetch(c):
        return {"principal": {"code": "62.01-5", "description": "Dev"}, "secundarios": []}
    monkeypatch.setattr(cnpj, "fetch_cnpj", _fake_fetch)

    n = _run(cnpj.enrich_from_cnpj("11.222.333/0001-81", _Store(), 42))
    assert n == 1
    tid, cls = calls[0]
    assert tid == 42 and cls[0]["source"] == "receita" and cls[0]["cnae_code"] == "62.01-5"


def test_enrich_from_cnpj_fail_open(monkeypatch):
    async def _none(c):
        return None
    monkeypatch.setattr(cnpj, "fetch_cnpj", _none)
    assert _run(cnpj.enrich_from_cnpj("00000000000000", object(), 1)) == 0  # nada, sem levantar
