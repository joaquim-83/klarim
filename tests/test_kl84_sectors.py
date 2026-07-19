"""KL-84 — Taxonomia aberta de setores. Testa (1) sinônimos, (2) sanitize_slug, (3) o fluxo
puro `process_classification` (FakeStore em-memória), (4) o prompt dinâmico + o parsing de
setor novo no `ai_enrich`, (5) os 5 endpoints admin. Tudo offline (sem rede/Postgres)."""

from __future__ import annotations

import asyncio

import pytest

from discovery.sector_synonyms import resolve_synonym, SYNONYMS
from discovery.sector_classification import process_classification, sanitize_slug


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# =========================================================================== #
# 1. Sinônimos (puro)
# =========================================================================== #

def test_synonym_advocacia_to_juridico():
    assert resolve_synonym("advocacia") == "juridico"


def test_synonym_dentista_to_odontologia():
    assert resolve_synonym("dentista") == "odontologia"


def test_synonym_pousada_to_hotel():
    assert resolve_synonym("pousada") == "hotel"


def test_synonym_case_and_spaces_normalized():
    assert resolve_synonym("  Loja Virtual ") == "ecommerce"
    assert resolve_synonym("LOJA-VIRTUAL") == "ecommerce"


def test_synonym_unknown_passthrough_normalized():
    # slug desconhecido volta normalizado (não vira 'outro' aqui — quem decide é o process)
    assert resolve_synonym("Clínica Veterinária".replace("í", "i").replace("á", "a")) == "clinica_veterinaria"


def test_synonyms_all_resolve_to_a_string():
    for k, v in SYNONYMS.items():
        assert isinstance(v, str) and v


# =========================================================================== #
# 2. sanitize_slug
# =========================================================================== #

def test_sanitize_slug_basic():
    assert sanitize_slug("Loja Pet") == "loja_pet"


def test_sanitize_slug_strips_non_alnum():
    assert sanitize_slug("estúdio-tattoo!!") == "estdio_tattoo"


def test_sanitize_slug_maxlen():
    assert len(sanitize_slug("a" * 80)) == 50


def test_sanitize_slug_empty():
    assert sanitize_slug("  ") == ""


# =========================================================================== #
# 3. process_classification (fluxo puro com FakeStore)
# =========================================================================== #

class FakeSectorStore:
    def __init__(self, sectors=None):
        # slug -> {status, merged_into, ...}
        self.sectors = dict(sectors or {})
        self.increments = []
        self.created = []

    async def get_sector(self, slug):
        s = self.sectors.get(slug)
        return dict(s, slug=slug) if s else None

    async def increment_sector_count(self, slug):
        self.increments.append(slug)

    async def create_proposed_sector(self, slug, label, macro):
        self.created.append((slug, label, macro))
        self.sectors[slug] = {"status": "proposed", "label": label, "macro_sector": macro}


def test_process_existing_sector():
    store = FakeSectorStore({"hotel": {"status": "official"}})
    d = _run(process_classification(store, {"sector": "hotel", "sector_confidence": 0.9}))
    assert d == {"sector": "hotel", "confidence": 0.9, "action": "existing"}
    assert store.increments == ["hotel"]


def test_process_synonym_resolves_before_table():
    store = FakeSectorStore({"juridico": {"status": "official"}})
    d = _run(process_classification(store, {"sector": "advocacia", "sector_confidence": 0.8}))
    assert d["sector"] == "juridico" and d["action"] == "existing"


def test_process_merged_follows_target():
    store = FakeSectorStore({
        "pizzaria_artesanal": {"status": "merged", "merged_into": "restaurante"},
        "restaurante": {"status": "official"},
    })
    d = _run(process_classification(store, {"sector": "pizzaria_artesanal", "sector_confidence": 0.9}))
    assert d["sector"] == "restaurante" and d["action"] == "merged"
    assert store.increments == ["restaurante"]


def test_process_rejected_falls_back_to_outro():
    store = FakeSectorStore({"spam_sector": {"status": "rejected"}})
    d = _run(process_classification(store, {"sector": "spam_sector", "sector_confidence": 0.9}))
    assert d["sector"] == "outro" and d["action"] == "fallback"
    assert store.increments == []   # não conta 'outro'


def test_process_new_sector_creates_proposed():
    store = FakeSectorStore()
    d = _run(process_classification(store, {
        "sector": "clinica_veterinaria", "is_new_sector": True,
        "sector_label": "Clínica Veterinária", "macro_sector_suggestion": "saude",
        "sector_confidence": 0.85}))
    assert d["sector"] == "clinica_veterinaria" and d["action"] == "proposed"
    assert store.created == [("clinica_veterinaria", "Clínica Veterinária", "saude")]
    assert store.increments == ["clinica_veterinaria"]


def test_process_unknown_not_new_becomes_outro():
    store = FakeSectorStore()
    d = _run(process_classification(store, {"sector": "algo_estranho", "sector_confidence": 0.9}))
    assert d["sector"] == "outro" and d["action"] == "fallback"
    assert store.created == []


def test_process_new_sector_invalid_macro_defaults_outro():
    store = FakeSectorStore()
    d = _run(process_classification(store, {
        "sector": "loja_pet", "is_new_sector": True, "macro_sector_suggestion": "inventado",
        "sector_confidence": 0.9}))
    assert store.created[0][2] == "outro"   # macro inválida → 'outro'
    assert d["action"] == "proposed"


def test_process_outro_passthrough():
    store = FakeSectorStore()
    d = _run(process_classification(store, {"sector": "outro", "sector_confidence": 0.9}))
    assert d["sector"] == "outro" and d["action"] == "fallback"


def test_process_new_sector_sanitizes_slug():
    store = FakeSectorStore()
    d = _run(process_classification(store, {
        "sector": "Bar & Boteco", "is_new_sector": True, "macro_sector_suggestion": "alimentacao",
        "sector_confidence": 0.9}))
    assert d["sector"] == "bar__boteco"   # sanitizado (espaços/símbolos)


# =========================================================================== #
# 4. Prompt dinâmico + parsing de setor novo no ai_enrich
# =========================================================================== #

def test_build_system_prompt_base_has_known_fields():
    from scanner.ai_enrichment import build_system_prompt
    p = build_system_prompt()
    assert "is_new_sector" in p and "macro_sector_suggestion" in p and "sector_legacy" in p


def test_build_system_prompt_appends_approved():
    from scanner.ai_enrichment import build_system_prompt
    p = build_system_prompt(["clinica_veterinaria", "loja_pet"])
    assert "clinica_veterinaria" in p and "loja_pet" in p


def test_build_system_prompt_ignores_official_dupes():
    from scanner.ai_enrichment import build_system_prompt, VALID_SECTORS
    # um setor já oficial não é anexado como "adicional"
    known = list(VALID_SECTORS)[:1]
    assert build_system_prompt(known) == build_system_prompt()


def test_ai_enrich_preserves_new_sector(monkeypatch):
    import scanner.ai_enrichment as ai
    monkeypatch.setattr(ai, "OPENAI_API_KEY", "x")

    async def fake_call(system, user, max_tokens=900):
        return {"sector_legacy": "clinica_veterinaria", "is_new_sector": True,
                "sector_label": "Clínica Veterinária", "macro_sector_suggestion": "saude",
                "sector_confidence": 0.9, "cnaes": [], "tags": []}
    monkeypatch.setattr(ai, "call_openai", fake_call)
    out = _run(ai.ai_enrich("vet.com.br", "texto do site"))
    assert out["sector"] == "clinica_veterinaria" and out["is_new_sector"] is True


def test_ai_enrich_known_sector_normalized(monkeypatch):
    import scanner.ai_enrichment as ai
    monkeypatch.setattr(ai, "OPENAI_API_KEY", "x")

    async def fake_call(system, user, max_tokens=900):
        return {"sector_legacy": "hotel", "is_new_sector": False,
                "sector_confidence": 0.9, "cnaes": [], "tags": []}
    monkeypatch.setattr(ai, "call_openai", fake_call)
    out = _run(ai.ai_enrich("h.com.br", "texto"))
    assert out["sector"] == "hotel" and out["is_new_sector"] is False


def test_ai_enrich_new_flag_but_known_slug_not_new(monkeypatch):
    import scanner.ai_enrichment as ai
    monkeypatch.setattr(ai, "OPENAI_API_KEY", "x")

    async def fake_call(system, user, max_tokens=900):
        # IA marca is_new mas o slug já existe → normaliza e is_new=False
        return {"sector_legacy": "hotel", "is_new_sector": True,
                "sector_confidence": 0.9, "cnaes": [], "tags": []}
    monkeypatch.setattr(ai, "call_openai", fake_call)
    out = _run(ai.ai_enrich("h.com.br", "texto"))
    assert out["sector"] == "hotel" and out["is_new_sector"] is False


# =========================================================================== #
# 5. Endpoints admin
# =========================================================================== #

import api.main as m  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


class AdminSectorStore:
    def __init__(self):
        self.sectors = {
            "hotel": {"slug": "hotel", "label": "Hotel", "macro_sector": "turismo",
                      "status": "official", "site_count": 100},
            "clinica_veterinaria": {"slug": "clinica_veterinaria", "label": "Clínica Veterinária",
                                    "macro_sector": "saude", "status": "proposed", "site_count": 4},
        }
        self.approved = []
        self.merged = []
        self.rejected = []

    async def sector_taxonomy_stats(self):
        return {"by_status": {"official": 48, "proposed": 1}, "total_classified": 8000,
                "outro_count": 1200, "outro_pct": 15.0}

    async def list_sectors(self, statuses):
        return [s for s in self.sectors.values() if s["status"] in statuses]

    async def get_sector(self, slug):
        return self.sectors.get(slug)

    async def sector_examples(self, slug, limit=5):
        return ["a.com.br", "b.com.br"]

    async def approve_sector(self, slug, label, macro, who):
        s = self.sectors.get(slug)
        if not s or s["status"] != "proposed":
            return None
        s["status"] = "approved"
        self.approved.append((slug, who))
        return s

    async def merge_sector(self, slug, dest):
        s = self.sectors.get(slug)
        if not s or s["status"] != "proposed":
            return None
        s["status"] = "merged"
        self.merged.append((slug, dest))
        return {"sector": s, "reclassified_count": 3}

    async def reject_sector(self, slug):
        s = self.sectors.get(slug)
        if not s or s["status"] != "proposed":
            return None
        s["status"] = "rejected"
        self.rejected.append(slug)
        return {"sector": s, "reclassified_count": 2}


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "k" * 64)
    monkeypatch.setenv("ADMIN_USER", "op")
    s = AdminSectorStore()
    import api.admin_sectors as asec
    monkeypatch.setattr(asec, "get_target_store", lambda: s)
    return TestClient(m.app, raise_server_exceptions=False), s


def _admin():
    return {"Authorization": f"Bearer {m._create_token('op')}"}


def test_sectors_requires_admin(client):
    c, _ = client
    assert c.get("/admin/sectors").status_code == 401


def test_sectors_list(client):
    c, _ = client
    j = c.get("/admin/sectors", headers=_admin()).json()
    assert j["stats"]["outro_pct"] == 15.0
    assert len(j["emerging"]) == 1 and j["emerging"][0]["slug"] == "clinica_veterinaria"
    assert any(t["slug"] == "hotel" for t in j["taxonomy"])


def test_sector_examples(client):
    c, _ = client
    j = c.get("/admin/sectors/clinica_veterinaria/examples", headers=_admin()).json()
    assert j["examples"] == ["a.com.br", "b.com.br"]


def test_sector_examples_404(client):
    c, _ = client
    assert c.get("/admin/sectors/inexistente/examples", headers=_admin()).status_code == 404


def test_approve_sector(client):
    c, s = client
    r = c.post("/admin/sectors/clinica_veterinaria/approve", headers=_admin(), json={})
    assert r.status_code == 200 and r.json()["ok"] is True
    assert s.sectors["clinica_veterinaria"]["status"] == "approved"
    assert s.approved and s.approved[0][1] == "op"   # audit trail = sub do JWT


def test_approve_non_proposed_404(client):
    c, _ = client
    # 'hotel' é official, não proposto → 404
    assert c.post("/admin/sectors/hotel/approve", headers=_admin(), json={}).status_code == 404


def test_approve_invalid_macro_422(client):
    c, _ = client
    r = c.post("/admin/sectors/clinica_veterinaria/approve", headers=_admin(),
               json={"macro_sector": "inventado"})
    assert r.status_code == 422


def test_merge_sector(client):
    c, s = client
    r = c.post("/admin/sectors/clinica_veterinaria/merge", headers=_admin(),
               json={"merge_into": "hotel"})
    assert r.status_code == 200
    assert r.json()["reclassified_count"] == 3 and s.merged == [("clinica_veterinaria", "hotel")]


def test_merge_into_invalid_dest_422(client):
    c, _ = client
    r = c.post("/admin/sectors/clinica_veterinaria/merge", headers=_admin(),
               json={"merge_into": "nao_existe"})
    assert r.status_code == 422


def test_merge_into_self_422(client):
    c, _ = client
    r = c.post("/admin/sectors/clinica_veterinaria/merge", headers=_admin(),
               json={"merge_into": "clinica_veterinaria"})
    assert r.status_code == 422


def test_reject_sector(client):
    c, s = client
    r = c.post("/admin/sectors/clinica_veterinaria/reject", headers=_admin())
    assert r.status_code == 200 and r.json()["reclassified_count"] == 2
    assert s.sectors["clinica_veterinaria"]["status"] == "rejected"


def test_reject_non_proposed_404(client):
    c, _ = client
    assert c.post("/admin/sectors/hotel/reject", headers=_admin()).status_code == 404
