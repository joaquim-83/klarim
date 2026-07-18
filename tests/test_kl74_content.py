"""KL-74 — arquitetura de conteúdo navegável.

Testa os endpoints públicos de setores/vitrine/estatísticas + a navegação contextual
do perfil (posição no ranking + cross-linking). Offline (FakeStore), sem rede/Postgres.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

import api.main as m


class FakeStore:
    """Store em memória com só os métodos que os endpoints KL-74 (e /public/profile)
    tocam. Cada método devolve dados previsíveis para asserções de forma/contrato."""

    def __init__(self):
        self.targets = {}   # domain -> target dict

    # --- KL-74: índice/detalhe de setores ------------------------------------ #
    async def public_sector_index(self, min_count=10):
        return [
            {"sector": "tecnologia", "count": 312, "avg_score": 71, "median_score": 73,
             "verde": 25, "amarelo": 265, "vermelho": 22, "score_100": 5},
            {"sector": "clinica", "count": 40, "avg_score": 60, "median_score": 62,
             "verde": 4, "amarelo": 30, "vermelho": 6, "score_100": 1},
        ]

    async def public_sector_stats(self, sector):
        return {"count": 40, "avg_score": 60, "median_score": 62, "score_100_count": 1,
                "distribution": {"verde": 4, "amarelo": 30, "vermelho": 6,
                                 "verde_pct": 10, "amarelo_pct": 75, "vermelho_pct": 15}}

    async def public_sector_sites(self, sector, limit=20, offset=0, sort="score_desc"):
        return [
            {"domain": "alpha.com.br", "score": 100, "semaphore": "verde",
             "company_name": "Alpha", "description": "Uma descrição bem longa " * 10,
             "last_scan_at": datetime(2026, 7, 15, tzinfo=timezone.utc),
             "privacy_score": "6", "owner_verified": True},
            {"domain": "beta.com.br", "score": 55, "semaphore": "amarelo",
             "company_name": "Beta", "description": None,
             "last_scan_at": None, "privacy_score": None, "owner_verified": False},
        ]

    async def public_sector_top_fails(self, sector, limit=5, sample=3000):
        return {"scanned": 40, "fails": [
            {"check_name": "HSTS Header", "fail_count": 24, "fail_pct": 60, "severity": "ALTA"},
            {"check_name": "CSP", "fail_count": 20, "fail_pct": 50, "severity": "MEDIA"},
        ]}

    async def public_score_100_sites(self, sector=None, limit=200):
        rows = [{"domain": "alpha.com.br", "sector": "tecnologia", "company_name": "Alpha",
                 "owner_verified": True},
                {"domain": "gamma.com.br", "sector": "clinica", "company_name": "Gamma",
                 "owner_verified": False}]
        return [r for r in rows if not sector or r["sector"] == sector]

    async def public_related_sites(self, sector, exclude_domain, limit=8):
        return [{"domain": "beta.com.br", "score": 55, "semaphore": "amarelo",
                 "company_name": "Beta", "sector": sector or "tecnologia"}]

    async def public_platform_stats(self):
        return {"total_targets": 30000, "total_scans": 10800, "scanned": 8100,
                "score_100_count": 134,
                "distribution": {"verde": 810, "amarelo": 6480, "vermelho": 810,
                                 "verde_pct": 10, "amarelo_pct": 80, "vermelho_pct": 10}}

    async def all_sector_benchmarks(self, min_count=10):
        return [
            {"sector": "tecnologia", "count": 312, "avg_score": 71, "median": 73},
            {"sector": "clinica", "count": 40, "avg_score": 60, "median": 62},
            {"sector": "restaurante", "count": 30, "avg_score": 45, "median": 44},
        ]

    # --- /public/profile (para o teste de ranking + related) ----------------- #
    async def get_target_by_domain(self, domain):
        return self.targets.get(domain.lower().strip())

    async def get_site_profile(self, tid):
        return {"public_visible": True, "company_name": "Alpha", "description": "Site"}

    async def get_target_classifications(self, tid):
        return []

    async def get_latest_scan_full(self, tid):
        return {"semaphore": "verde", "checks_json": {"privacy": {"score": 6, "total": 8}}}

    async def sector_benchmark(self, sector, min_count=10):
        return {"sector": sector, "count": 40, "avg_score": 60, "median": 62,
                "min_score": 20, "max_score": 100,
                "distribution": {"green_pct": 10, "yellow_pct": 75, "red_pct": 15}}

    async def global_avg_score(self):
        return {"avg_score": 65, "count": 8100}

    async def site_has_owner(self, tid, exclude_user_id=None):
        return True

    async def get_sector_position(self, sector, tid):
        return {"position": 3, "total": 40}

    # --- fix dedup de domínios duplicados ------------------------------------ #
    async def find_duplicate_domains(self, limit=500):
        return [{"domain": "klarim.net", "count": 4, "target_ids": [10, 7, 3, 1]}]

    async def dedup_targets(self, apply=False, add_constraint=True):
        return {"duplicates_found": 1, "domains_affected": ["klarim.net"],
                "domains_affected_total": 1,
                "records_merged": 3 if apply else 0,
                "records_deleted": 3 if apply else 0,
                "constraint_added": bool(apply and add_constraint)}


@pytest.fixture
def store(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "k" * 64)  # p/ gerar token admin nos testes de dedup
    s = FakeStore()
    monkeypatch.setattr(m, "get_target_store", lambda: s)
    monkeypatch.setattr("discovery.store.get_target_store", lambda: s)
    return s


@pytest.fixture
def client(store):
    return TestClient(m.app, raise_server_exceptions=False)


# --------------------------------------------------------------------------- #
# 1A — índice de setores
# --------------------------------------------------------------------------- #
def test_public_sectors(client):
    r = client.get("/public/sectors")
    assert r.status_code == 200
    assert "max-age=3600" in r.headers.get("cache-control", "")
    body = r.json()
    assert body["count"] == 2
    s0 = body["sectors"][0]
    assert s0["slug"] == "tecnologia"
    assert s0["name"]  # rótulo humano resolvido
    assert s0["count"] == 312
    assert s0["median_score"] == 73
    assert s0["semaphore_distribution"] == {"verde": 25, "amarelo": 265, "vermelho": 22}
    assert s0["score_100_count"] == 5


# --------------------------------------------------------------------------- #
# 1B — detalhe do setor (sites paginados + top fails + benchmark)
# --------------------------------------------------------------------------- #
def test_public_sector_detail(client):
    r = client.get("/public/sector/clinica?page=1&limit=20&sort=score_desc")
    assert r.status_code == 200
    body = r.json()
    assert body["sector"]["slug"] == "clinica"
    assert body["sector"]["count"] == 40
    assert body["pagination"] == {"page": 1, "limit": 20, "total": 40, "pages": 2}
    assert len(body["sites"]) == 2
    a = body["sites"][0]
    assert a["domain"] == "alpha.com.br"
    assert a["privacy_score"] == 6           # convertido de texto p/ int
    assert a["owner_verified"] is True
    assert a["last_scan_date"] == "2026-07-15"
    assert len(a["description_short"]) <= 121  # truncado (120 + reticências)
    assert body["sites"][1]["last_scan_date"] is None
    assert body["top_fails"][0]["check_name"] == "HSTS Header"
    assert body["score_100_count"] == 1
    assert body["score_100_sites"]            # vitrine na página 1


def test_public_sector_detail_sort_whitelist(client):
    # sort inválido → não quebra (cai no default), sem 500
    r = client.get("/public/sector/clinica?sort=DROP%20TABLE")
    assert r.status_code == 200


# --------------------------------------------------------------------------- #
# 1C — top fails
# --------------------------------------------------------------------------- #
def test_public_top_fails(client):
    r = client.get("/public/top-fails?sector=clinica&limit=5")
    assert r.status_code == 200
    assert "max-age=86400" in r.headers.get("cache-control", "")
    body = r.json()
    assert body["sector"] == "clinica"
    assert body["scanned"] == 40
    assert body["top_fails"][0]["fail_pct"] == 60


def test_public_top_fails_requires_sector(client):
    r = client.get("/public/top-fails")
    assert r.status_code == 422  # sector é obrigatório


# --------------------------------------------------------------------------- #
# 1D — sites relacionados (exclui o próprio domínio)
# --------------------------------------------------------------------------- #
def test_public_related(client):
    r = client.get("/public/related?domain=alpha.com.br&limit=8")
    assert r.status_code == 200
    body = r.json()
    assert body["domain"] == "alpha.com.br"
    assert all(s["domain"] != "alpha.com.br" for s in body["sites"])
    assert body["sites"][0]["sector_label"]  # rótulo resolvido


# --------------------------------------------------------------------------- #
# 2C/2D — vitrine dos melhores + estatísticas
# --------------------------------------------------------------------------- #
def test_public_best(client):
    r = client.get("/public/best")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 2
    # agrupado por setor, do maior grupo p/ o menor
    slugs = [g["slug"] for g in body["sectors"]]
    assert "tecnologia" in slugs and "clinica" in slugs
    for g in body["sectors"]:
        assert g["name"]  # rótulo humano


def test_public_stats(client):
    r = client.get("/public/stats")
    assert r.status_code == 200
    body = r.json()
    assert body["total_targets"] == 30000
    assert body["score_100_count"] == 134
    assert body["checks_per_site"] == 48
    assert body["sectors_count"] == 3
    assert body["distribution"]["verde_pct"] == 10
    # setores mais seguros primeiro; oportunidade = piores
    assert body["safest_sectors"][0]["slug"] == "tecnologia"
    assert body["opportunity_sectors"][0]["slug"] == "restaurante"


# --------------------------------------------------------------------------- #
# 3B/3C — navegação contextual no perfil
# --------------------------------------------------------------------------- #
def test_profile_includes_ranking(client, store):
    store.targets["alpha.com.br"] = {
        "id": 1, "url": "https://alpha.com.br", "domain": "alpha.com.br",
        "status": "scanned", "sector": "tecnologia", "platform": "wordpress",
        "last_scan_score": 100,
        "last_scan_at": datetime(2026, 7, 15, tzinfo=timezone.utc),
    }
    body = client.get("/public/profile/alpha.com.br").json()
    assert body["status"] == "ok"
    assert body["ranking"] == {"position": 3, "total": 40, "sector": "tecnologia",
                               "sector_label": body["ranking"]["sector_label"]}
    # nunca expõe contact_email
    assert "contact_email" not in body["target"]


# --------------------------------------------------------------------------- #
# Segurança — rate limit só vale para IP real (X-Forwarded-For), não p/ SSR interno
# --------------------------------------------------------------------------- #
def test_rate_limit_external_ip(client):
    # sem XFF (SSR interno) nunca é limitado
    for _ in range(40):
        assert client.get("/public/sectors").status_code == 200
    # com XFF (cliente externo via nginx) o teto de 30/min entra em ação
    hit_429 = False
    for _ in range(45):
        resp = client.get("/public/sectors", headers={"X-Forwarded-For": "203.0.113.9"})
        if resp.status_code == 429:
            hit_429 = True
            break
    assert hit_429


# --------------------------------------------------------------------------- #
# FIX — dedup de domínios duplicados (admin, protegido por JWT)
# --------------------------------------------------------------------------- #
def _admin(client):
    return {"Authorization": f"Bearer {m._create_token('op')}"}


def test_dedup_requires_admin(client):
    # sem token → 401 (middleware do prefixo /admin)
    assert client.post("/admin/dedup-targets").status_code == 401
    assert client.get("/admin/duplicate-domains").status_code == 401


def test_duplicate_domains_diagnostic(client):
    r = client.get("/admin/duplicate-domains", headers=_admin(client))
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 1
    assert body["duplicates"][0]["domain"] == "klarim.net"


def test_dedup_dry_run_changes_nothing(client):
    r = client.post("/admin/dedup-targets?dry_run=true", headers=_admin(client))
    assert r.status_code == 200
    body = r.json()
    assert body["duplicates_found"] == 1
    assert body["records_merged"] == 0        # dry-run não altera
    assert body["constraint_added"] is False


def test_dedup_apply(client):
    r = client.post("/admin/dedup-targets?dry_run=false", headers=_admin(client))
    assert r.status_code == 200
    body = r.json()
    assert body["records_merged"] == 3
    assert body["records_deleted"] == 3
    assert body["constraint_added"] is True
