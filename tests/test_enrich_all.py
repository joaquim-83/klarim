"""Testes do reprocessamento completo (scripts/enrich_all.py — KL-50 + KL-47A).

Offline: os helpers de decisão são puros; a seleção (SQL) é validada pelo espelho
Python `enrichment_group`; o fluxo usa um store/redis falsos (sem rede, sem banco).
"""

from __future__ import annotations

import argparse
import asyncio

import scripts.enrich_all as e


def _run(coro):
    return asyncio.run(coro)


def _args(**kw):
    base = dict(limit=500, no_limit=False, only_sem_contato=False,
                only_ai=False, dry_run=False, ai_delay=0.0)
    base.update(kw)
    return argparse.Namespace(**base)


def _row(**kw):
    base = dict(id=1, url="https://x.com.br", domain="x.com.br", status="scanned",
                contact_email=None, profile_id=None, sector="outro",
                classification_source="auto", classification_confidence=0.0,
                profile_description=None, profile_sources=None)
    base.update(kw)
    return base


class FakeStore:
    def __init__(self, candidates=None, groups=None):
        self._candidates = candidates or []
        self._groups = groups or {"group1": 0, "group2": 0, "group3": 0, "total": 0}
        self.profiles = {}
        self.upserts = []
        self.emails = []
        self.reclassified = []
        self.last_limit = "unset"
        self.last_mode = None

    async def ensure_schema(self):
        pass

    async def count_enrichment_groups(self, mode="all"):
        self.last_mode = mode
        return self._groups

    async def list_enrichment_candidates(self, limit=500, mode="all"):
        self.last_limit = limit
        self.last_mode = mode
        rows = list(self._candidates)
        return rows if limit is None else rows[:limit]

    async def get_site_profile(self, tid):
        return self.profiles.get(tid)

    async def upsert_site_profile(self, tid, profile):
        self.upserts.append((tid, profile))

    async def update_target_email(self, tid, email):
        self.emails.append((tid, email))
        return {"id": tid}

    async def ai_update_classification(self, tid, sector, tier, conf):
        self.reclassified.append((tid, sector, conf))


class FakeRedis:
    def __init__(self):
        self.pushed = []

    async def rpush(self, key, val):
        self.pushed.append((key, val))


def _fresh_stats():
    return {k: 0 for k in ("processed", "crawled", "crawl_err", "profiles",
                           "ai_calls", "reclassified", "emails", "erros",
                           "group1", "group2", "group3")}


# --- 1-3: seleção dos três grupos (espelho do SQL) ------------------------- #

def test_select_group1_no_profile():
    assert e.enrichment_group(_row(status="alerted", profile_id=None)) == 1


def test_select_group2_weak_classification():
    row = _row(profile_id=10, sector="outro", classification_source="auto",
               classification_confidence=0.0)
    assert e.enrichment_group(row) == 2


def test_select_group3_missing_description():
    row = _row(profile_id=11, sector="hotel", classification_source="ai",
               classification_confidence=0.92, profile_description="")
    assert e.enrichment_group(row) == 3


# --- 4-6: não seleciona descartado / completo / idempotência --------------- #

def test_does_not_select_discarded():
    assert e.enrichment_group(_row(status="descartado", profile_id=None)) is None


def test_does_not_select_complete():
    row = _row(profile_id=12, sector="hotel", classification_source="ai",
               classification_confidence=0.9, profile_description="Um hotel à beira-mar.")
    assert e.enrichment_group(row) is None


def test_idempotent_completed_not_reselected():
    # Um alvo já enriquecido (perfil + setor IA forte + descrição) nunca reentra.
    row = _row(profile_id=5, sector="clinica", classification_source="ai",
               classification_confidence=0.88, profile_description="Clínica odontológica.",
               profile_sources=["homepage", "contato"])
    assert e.enrichment_group(row) is None
    assert e.needs_crawl(row) is False


# --- decisões de crawl / IA ------------------------------------------------ #

def test_needs_crawl_incomplete_profile():
    assert e.needs_crawl(_row(profile_id=5, profile_sources=None)) is True
    assert e.needs_crawl(_row(profile_id=5, profile_sources=["homepage"])) is False


def test_only_ai_never_crawls():
    assert e.needs_crawl(_row(profile_id=None), only_ai=True) is False


def test_needs_ai_disabled(monkeypatch):
    monkeypatch.setattr(e, "AI_ENRICHMENT_ENABLED", False)
    assert e.needs_ai(_row(sector="outro"), None) is False


def test_needs_ai_outro_or_no_description(monkeypatch):
    monkeypatch.setattr(e, "AI_ENRICHMENT_ENABLED", True)
    assert e.needs_ai(_row(sector="outro", classification_confidence=0.9), {"description": "x"}) is True
    assert e.needs_ai(_row(sector="hotel", classification_confidence=0.9), {"description": ""}) is True
    assert e.needs_ai(_row(sector="hotel", classification_confidence=0.9), {"description": "ok"}) is False


# --- 8: a IA respeita a classificação manual ------------------------------- #

def test_ai_respects_manual():
    manual = _row(classification_source="manual", sector="outro",
                  classification_confidence=1.0)
    assert e.should_update_sector(manual, {"sector": "hotel", "sector_confidence": 0.95}) is False


def test_ai_updates_weak_but_not_strong_regex():
    weak = _row(classification_source="auto", sector="outro", classification_confidence=0.0)
    assert e.should_update_sector(weak, {"sector": "hotel", "sector_confidence": 0.9}) is True
    strong = _row(classification_source="domain", sector="clinica", classification_confidence=0.9)
    assert e.should_update_sector(strong, {"sector": "hotel", "sector_confidence": 0.95}) is False
    lowconf = _row(classification_source="auto", sector="outro", classification_confidence=0.0)
    assert e.should_update_sector(lowconf, {"sector": "hotel", "sector_confidence": 0.5}) is False


# --- 7: e-mail encontrado reativa o sem_contato + enfileira ---------------- #

def test_email_found_reactivates_sem_contato(monkeypatch):
    store, redis = FakeStore(), FakeRedis()
    row = _row(id=42, status="sem_contato")

    async def _crawl(url, homepage_html=None):
        return {"homepage": "<html>contato@x.com.br</html>"}

    async def _extract(html, url, validate_mx=True):
        return "contato@x.com.br"

    async def _build(url, **kw):
        return {"company_name": "X", "description": None, "technologies": {},
                "extraction_sources": ["homepage"]}

    async def _fetch(url):
        return "<html>", {}

    monkeypatch.setattr(e.profiler, "crawl_contact_pages", _crawl)
    monkeypatch.setattr(e, "extract_email", _extract)
    monkeypatch.setattr(e.profiler, "build_profile", _build)
    monkeypatch.setattr(e, "_fetch_home", _fetch)
    monkeypatch.setattr(e.dns_util, "resolve_mx", lambda d: [])
    monkeypatch.setattr(e.dns_util, "resolve_ns", lambda d: [])
    monkeypatch.setattr(e, "AI_ENRICHMENT_ENABLED", False)  # isola o caminho do e-mail

    stats = _fresh_stats()
    _run(e.process_target(store, redis, row, _args(), stats))

    assert store.emails == [(42, "contato@x.com.br")]        # reativado
    assert redis.pushed and "42" in redis.pushed[0][1]        # enfileirado
    assert store.upserts and store.upserts[0][0] == 42        # perfil salvo
    assert stats["emails"] == 1 and stats["profiles"] == 1


# --- 9: dry-run não grava nada --------------------------------------------- #

def test_dry_run_writes_nothing(monkeypatch):
    rows = [_row(id=i, url=f"https://s{i}.com.br", status="scanned") for i in range(3)]
    store = FakeStore(candidates=rows, groups={"group1": 3, "group2": 0, "group3": 0, "total": 3})
    monkeypatch.setattr(e, "get_target_store", lambda: store)

    stats = _run(e.main(_args(dry_run=True)))
    assert stats["processed"] == 3
    assert store.upserts == [] and store.emails == [] and store.reclassified == []


# --- 10: --limit é propagado e respeitado ---------------------------------- #

def test_limit_is_passed_and_respected(monkeypatch):
    rows = [_row(id=i, url=f"https://s{i}.com.br", status="scanned") for i in range(50)]
    store = FakeStore(candidates=rows, groups={"group1": 50, "group2": 0, "group3": 0, "total": 50})
    monkeypatch.setattr(e, "get_target_store", lambda: store)

    stats = _run(e.main(_args(dry_run=True, limit=10)))
    assert store.last_limit == 10 and store.last_mode == "all"
    assert stats["processed"] == 10


def test_no_limit_processes_all(monkeypatch):
    rows = [_row(id=i, url=f"https://s{i}.com.br", status="scanned") for i in range(50)]
    store = FakeStore(candidates=rows, groups={"group1": 50, "group2": 0, "group3": 0, "total": 50})
    monkeypatch.setattr(e, "get_target_store", lambda: store)

    stats = _run(e.main(_args(dry_run=True, no_limit=True)))
    assert store.last_limit is None
    assert stats["processed"] == 50


# --- modos mapeiam para o store -------------------------------------------- #

def test_only_sem_contato_mode(monkeypatch):
    store = FakeStore(candidates=[], groups={"group1": 0, "group2": 0, "group3": 0, "total": 0})
    monkeypatch.setattr(e, "get_target_store", lambda: store)
    _run(e.main(_args(dry_run=True, only_sem_contato=True)))
    assert store.last_mode == "sem_contato"


def test_only_ai_mode(monkeypatch):
    store = FakeStore(candidates=[], groups={"group1": 0, "group2": 0, "group3": 0, "total": 0})
    monkeypatch.setattr(e, "get_target_store", lambda: store)
    _run(e.main(_args(dry_run=True, only_ai=True)))
    assert store.last_mode == "only_ai"
