"""KL-131 — sitemap dinâmico (index + sub-sitemaps) servido pelo FastAPI.

Substitui o sitemap Astro único (33k URLs, SSR pesado) por um sitemapindex + sub-sitemaps
paginados (≤10k), Content-Type application/xml, cache Redis. Offline (FakeStore + TestClient;
`_cache` é None nos testes → cache no-op, sem interferência).
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

import api.main as m


class FakeStore:
    def __init__(self, total=25000, sectors=None):
        self._total = total
        self._sectors = sectors if sectors is not None else [
            {"sector": "hotel", "count": 120}, {"sector": "clinica", "count": 40},
            {"sector": "outro", "count": 999},   # deve ser EXCLUÍDO do sitemap
        ]

    async def count_visible_profiles(self):
        return self._total

    async def get_visible_profiles_for_sitemap(self, offset=0, limit=10000):
        # Devolve 3 domínios determinísticos por página (inclui um com & p/ testar escape).
        base = offset // limit
        dt = datetime(2026, 7, 30, tzinfo=timezone.utc)
        return [
            {"domain": f"site{base}a.com.br", "last_scan_at": dt},
            {"domain": f"site{base}b.com.br", "last_scan_at": None},
            {"domain": "a&b.com.br", "last_scan_at": dt},
        ]

    async def public_sector_index(self, min_count=10):
        return self._sectors


@pytest.fixture
def client(monkeypatch):
    store = FakeStore()
    monkeypatch.setattr(m, "get_target_store", lambda: store)
    return TestClient(m.app, raise_server_exceptions=False)


def _xml_ct(resp):
    return resp.headers["content-type"].startswith("application/xml")


def test_sitemap_index_is_sitemapindex(client):
    r = client.get("/sitemap.xml")
    assert r.status_code == 200 and _xml_ct(r)
    body = r.text
    assert "<sitemapindex" in body and "</sitemapindex>" in body
    assert "https://klarim.net/sitemap-static.xml" in body
    assert "https://klarim.net/sitemap-sectors.xml" in body
    # 25.000 perfis / 10k = 3 páginas de perfis
    for i in (1, 2, 3):
        assert f"https://klarim.net/sitemap-profiles-{i}.xml" in body
    assert "sitemap-profiles-4.xml" not in body


def test_sitemap_index_min_one_profile_page_when_empty(monkeypatch):
    store = FakeStore(total=0)
    monkeypatch.setattr(m, "get_target_store", lambda: store)
    c = TestClient(m.app, raise_server_exceptions=False)
    body = c.get("/sitemap.xml").text
    assert "sitemap-profiles-1.xml" in body   # sempre ≥ 1 página


def test_sitemap_profiles_page(client):
    r = client.get("/sitemap-profiles-1.xml")
    assert r.status_code == 200 and _xml_ct(r)
    body = r.text
    assert "<urlset" in body
    assert "<loc>https://klarim.net/site/site0a.com.br</loc>" in body
    assert "<lastmod>2026-07-30</lastmod>" in body
    assert "a&amp;b.com.br" in body            # & escapado (XML válido)
    assert "<changefreq>weekly</changefreq>" in body


def test_sitemap_profiles_out_of_range_404(client):
    assert client.get("/sitemap-profiles-0.xml").status_code == 404
    assert client.get("/sitemap-profiles-9999.xml").status_code == 404


def test_sitemap_sectors_excludes_outro(client):
    r = client.get("/sitemap-sectors.xml")
    assert r.status_code == 200 and _xml_ct(r)
    body = r.text
    assert "https://klarim.net/setor/hotel" in body
    assert "https://klarim.net/setor/clinica" in body
    assert "/setor/outro" not in body          # 'outro' nunca é indexado


def test_sitemap_static(client):
    r = client.get("/sitemap-static.xml")
    assert r.status_code == 200 and _xml_ct(r)
    body = r.text
    assert "<loc>https://klarim.net/</loc>" in body
    assert "<loc>https://klarim.net/setores</loc>" in body
    assert "<urlset" in body


def test_sitemap_index_survives_store_error(monkeypatch):
    class Boom:
        async def count_visible_profiles(self):
            raise RuntimeError("db down")
    monkeypatch.setattr(m, "get_target_store", lambda: Boom())
    c = TestClient(m.app, raise_server_exceptions=False)
    r = c.get("/sitemap.xml")
    assert r.status_code == 200 and "sitemap-static.xml" in r.text   # fail-open (0 perfis)
