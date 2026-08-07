"""KL-152 P2 — as 7 docs de integração do Security Gate entram no sitemap-static.

Offline (FakeStore + TestClient; `_cache` None → cache no-op). O conteúdo das páginas em si
é validado no build do Astro + browser; aqui garantimos a descoberta por SEO (sitemap).
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import api.main as m


class _Store:
    async def count_visible_profiles(self):
        return 0

    async def get_visible_profiles_for_sitemap(self, offset=0, limit=10000):
        return []

    async def public_sector_index(self, min_count=10):
        return []


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(m, "get_target_store", lambda: _Store())
    monkeypatch.setattr(m, "_cache", None)
    return TestClient(m.app, raise_server_exceptions=False)


_DOC_SLUGS = ["github-actions", "gitlab-ci", "bitbucket", "jenkins", "manual", "api", "troubleshooting"]


def test_docs_in_sitemap_static(client):
    body = client.get("/sitemap-static.xml").text
    for slug in _DOC_SLUGS:
        assert f"<loc>https://klarim.net/docs/gate/{slug}</loc>" in body


def test_docs_count_in_static_list():
    docs = [p for (p, _cf, _pr) in m._SITEMAP_STATIC if p.startswith("/docs/gate/")]
    assert len(docs) == 7
