"""Enriquecimento de perfil compartilhado (KL-51 f5) — offline (mocks, sem rede/IA).

`scanner.enrichment.enrich_profile` é chamado tanto pelo scan worker quanto pelo
`/scan/summary`. Grava `site_profile` + `target_classifications` (CNAE). Best-effort.
"""

from __future__ import annotations

import asyncio

import scanner.enrichment as enr


class _Resp:
    status_code = 200
    headers: dict = {}
    text = "<html>hotel boutique</html>"


class FakeStore:
    def __init__(self):
        self.profile = None
        self.cnaes = None
        self.sector = None

    async def upsert_site_profile(self, tid, profile):
        self.profile = profile

    async def upsert_target_classifications(self, tid, cls):
        self.cnaes = cls

    async def ai_update_classification(self, tid, sector, tier, conf):
        self.sector = (sector, conf)

    # KL-84 — métodos da taxonomia aberta usados por process_classification/_approved_sectors.
    async def list_sectors(self, statuses):
        return []

    async def get_sector(self, slug):
        from discovery.sector_taxonomy import VALID_SECTORS
        return {"slug": slug, "status": "official"} if slug in VALID_SECTORS else None

    async def increment_sector_count(self, slug):
        pass

    async def create_proposed_sector(self, slug, label, macro):
        pass


def _mock_pipeline(monkeypatch, ai_result):
    async def _fetch(url, **kw):
        return _Resp()
    monkeypatch.setattr("scanner.checks.base.fetch", _fetch)
    monkeypatch.setattr("scanner.checks.dns_util.resolve_mx", lambda d: [])
    monkeypatch.setattr("scanner.checks.dns_util.resolve_ns", lambda d: [])

    async def _build(url, **kw):
        return {"description": "Hotel", "maturity_score": 6, "tags": []}
    monkeypatch.setattr("scanner.profiler.build_profile", _build)
    monkeypatch.setattr("scanner.ai_enrichment.AI_ENRICHMENT_ENABLED", True)

    async def _ai(domain, html, current_profile=None, known_sectors=None):
        return ai_result
    monkeypatch.setattr("scanner.ai_enrichment.ai_enrich", _ai)


def test_enrich_writes_profile_sector_and_cnaes(monkeypatch):
    store = FakeStore()
    _mock_pipeline(monkeypatch, {
        "sector": "hotel", "sector_confidence": 0.9, "description": "Hotel boutique",
        "tags": ["hotel", "spa"],
        "cnaes": [{"code": "55.10-8", "description": "Hotelaria",
                   "section": "I", "division": "55", "confidence": 0.9}],
    })
    asyncio.run(enr.enrich_profile(store, 1, "https://hotelparaiso.com.br", 74))
    # perfil gravado
    assert store.profile is not None and store.profile.get("maturity_score") == 6
    # setor refinado pela IA
    assert store.sector == ("hotel", 0.9)
    # CNAE gravado (source='ai', rank 1)
    assert store.cnaes and store.cnaes[0]["cnae_code"] == "55.10-8"
    assert store.cnaes[0]["source"] == "ai" and store.cnaes[0]["rank"] == 1


def test_enrich_no_cnae_when_ai_returns_none(monkeypatch):
    store = FakeStore()
    _mock_pipeline(monkeypatch, {"sector": "outro", "sector_confidence": 0.3, "cnaes": []})
    asyncio.run(enr.enrich_profile(store, 1, "https://x.com.br", 50))
    assert store.profile is not None       # perfil sempre gravado
    assert store.cnaes is None             # sem CNAE (lista vazia)
    assert store.sector is None            # setor fraco (outro/conf baixa) → não atualiza


def test_enrich_is_best_effort(monkeypatch):
    # store que explode no upsert NÃO derruba (best-effort)
    class Boom:
        async def upsert_site_profile(self, *a):
            raise RuntimeError("db down")
    _mock_pipeline(monkeypatch, {"sector": "outro", "cnaes": []})
    asyncio.run(enr.enrich_profile(Boom(), 1, "https://x.com.br", 50))  # não levanta
