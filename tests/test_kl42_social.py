"""Testes do score social (KL-42) — widget, card, score público, ranking e selo.
Offline (TestClient + FakeStore). O render real do PNG usa cairosvg (presente no CI);
testamos o SVG puro (dimensões) + o fail-open, além dos endpoints JSON.
"""

from __future__ import annotations

import xml.dom.minidom as minidom
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

import api.main as m
from api import auth_users


class FakeStore:
    def __init__(self):
        self.targets = {}       # domain -> target dict
        self.profiles = {}      # target_id -> profile
        self.scans = {}         # target_id -> [scan]
        self.sector_ranking = {}    # sector -> [rows]
        self.sectors_summary = []   # ranking_sectors_summary rows
        self.positions = {}     # (sector, target_id) -> {position, total}
        self.users = {}         # id -> user
        self.user_sites = {}    # (user_id, target_id) -> link
        self.targets_by_id = {}  # id -> target
        self.classifications = {}
        self.accounts = set()   # KL-78 item 3: target_ids com conta atribuída (selo)

    # --- público ---
    async def get_target_by_domain(self, domain):
        return self.targets.get(domain.lower().strip())

    async def get_site_profile(self, tid):
        return self.profiles.get(tid)

    async def list_scans(self, target_id=None, limit=1, **kw):
        return self.scans.get(target_id, [])[:limit]

    async def sector_avg_score(self, sector):
        return {"avg_score": 68, "count": 20}

    async def global_avg_score(self):
        return {"avg_score": 70, "count": 500}

    async def get_latest_scan_full(self, tid):   # KL-44 P5
        rows = self.scans.get(tid) or []
        return rows[0] if rows else None

    async def sector_benchmark(self, sector, min_count=10):   # KL-44 P5
        return {"sector": sector, "count": 20, "avg_score": 68, "median": 70,
                "min_score": 30, "max_score": 95,
                "distribution": {"green_pct": 10, "yellow_pct": 80, "red_pct": 10}}

    async def list_sector_ranking(self, sector, limit=20):
        return self.sector_ranking.get(sector, [])[:limit]

    async def ranking_sectors_summary(self, min_count=5):
        return self.sectors_summary

    async def get_sector_position(self, sector, target_id):
        return self.positions.get((sector, target_id))

    # --- conta (para /account/sites/{id}) ---
    async def get_user_by_id(self, uid):
        return self.users.get(int(uid))

    async def get_user_site(self, uid, tid):
        return self.user_sites.get((uid, tid))

    async def site_has_account(self, tid):   # KL-78 item 3
        return tid in self.accounts

    async def get_target(self, tid):
        return self.targets_by_id.get(tid)

    async def get_scan(self, scan_id):
        return None

    async def get_target_classifications(self, tid):
        return self.classifications.get(tid, [])


@pytest.fixture
def store(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "k" * 64)
    monkeypatch.setattr(auth_users, "_secret", lambda: "k" * 64)
    s = FakeStore()
    monkeypatch.setattr(m, "get_target_store", lambda: s)
    monkeypatch.setattr("discovery.store.get_target_store", lambda: s)
    return s


@pytest.fixture
def client(store):
    return TestClient(m.app, raise_server_exceptions=False)


def _target(**kw):
    base = {"id": 1, "url": "https://poll360.com.br", "domain": "poll360.com.br",
            "sector": "tecnologia", "platform": "WordPress", "status": "scanned",
            "last_scan_score": 82,
            "last_scan_at": datetime(2026, 7, 14, tzinfo=timezone.utc)}
    base.update(kw)
    return base


# --- selo/badge (puro) ------------------------------------------------------ #

def test_badge_score_100_with_account():
    # KL-78 item 3: selo só com score 100 E conta atribuída.
    b = m._score_badge(100, has_account=True)
    assert b["level"] == "high" and b["icon"] == "⭐" and b["label"] == "Monitorado por Klarim"


def test_badge_100_without_account_none():
    assert m._score_badge(100, has_account=False) is None


def test_badge_below_100_none():
    # score < 100 nunca tem selo, mesmo com conta.
    assert m._score_badge(95, has_account=True) is None
    assert m._score_badge(82, has_account=True) is None


def test_badge_none():
    assert m._score_badge(65, has_account=True) is None
    assert m._score_badge(None, has_account=True) is None


# --- /score/{domain} -------------------------------------------------------- #

def test_score_json(client, store):
    # KL-78 item 3: selo só com score 100 + conta.
    store.targets["poll360.com.br"] = _target(last_scan_score=100)
    store.profiles[1] = {"public_visible": True}
    store.scans[1] = [{"semaphore": "verde"}]
    store.accounts.add(1)
    r = client.get("/score/poll360.com.br")
    assert r.status_code == 200
    body = r.json()
    assert body["domain"] == "poll360.com.br" and body["score"] == 100
    assert body["semaphore"] == "verde"
    assert body["badge"]["level"] == "high"
    assert body["profile_url"].endswith("/site/poll360.com.br")


def test_score_badge_null_without_account(client, store):
    # score 100 mas sem conta → sem selo.
    store.targets["poll360.com.br"] = _target(last_scan_score=100)
    store.profiles[1] = {"public_visible": True}
    assert client.get("/score/poll360.com.br").json()["badge"] is None


def test_score_cors_and_cache(client, store):
    store.targets["poll360.com.br"] = _target()
    r = client.get("/score/poll360.com.br")
    assert r.headers["access-control-allow-origin"] == "*"
    assert "max-age=86400" in r.headers["cache-control"]


def test_score_hidden_profile_is_null(client, store):
    store.targets["poll360.com.br"] = _target()
    store.profiles[1] = {"public_visible": False}   # landing desligada (KL-56)
    assert client.get("/score/poll360.com.br").json()["score"] is None


def test_score_discarded_is_null(client, store):
    store.targets["x.com.br"] = _target(domain="x.com.br", status="descartado")
    assert client.get("/score/x.com.br").json()["score"] is None


# --- widget JS -------------------------------------------------------------- #

def test_widget_js(client, store):
    r = client.get("/widget/poll360.com.br.js")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/javascript")
    assert "max-age=3600" in r.headers["cache-control"]
    assert "poll360.com.br" in r.text and "Verificado por Klarim" in r.text


def test_widget_beacon(client, store):
    store.targets["poll360.com.br"] = _target()
    r = client.get("/widget/event?e=widget_loaded&d=poll360.com.br&s=sess123")
    assert r.status_code == 204


# --- card PNG --------------------------------------------------------------- #

def test_card_svg_dimensions():
    sq = m._card_svg("poll360.com.br", 82, "amarelo", "square")
    minidom.parseString(sq)
    assert 'width="1080" height="1080"' in sq and "82" in sq and "E o seu?" in sq
    ls = m._card_svg("poll360.com.br", 82, "amarelo", "landscape")
    minidom.parseString(ls)
    assert 'width="1200" height="630"' in ls


def test_card_png_endpoint(client, store):
    store.targets["poll360.com.br"] = _target()
    store.profiles[1] = {"public_visible": True}
    r = client.get("/card/poll360.com.br.png?format=square", follow_redirects=False)
    # 200 image/png (cairo disponível) ou 302 favicon (fail-open sem cairo)
    assert r.status_code in (200, 302)
    if r.status_code == 200:
        assert r.headers["content-type"].startswith("image/png")


def test_card_png_fallback_when_missing(client, store):
    r = client.get("/card/inexistente.com.br.png", follow_redirects=False)
    assert r.status_code == 302 and "favicon" in r.headers.get("location", "")


# --- ranking ---------------------------------------------------------------- #

def test_ranking_index(client, store):
    store.sectors_summary = [
        {"sector": "hotel", "count": 519, "avg_score": 68, "top_domain": "hotelparaiso.com.br"},
        {"sector": "tecnologia", "count": 471, "avg_score": 78, "top_domain": "poll360.com.br"},
    ]
    body = client.get("/ranking").json()
    assert body["count"] == 2
    assert body["sectors"][0]["sector"] == "hotel"
    assert body["sectors"][0]["label"]        # rótulo resolvido pela taxonomia
    assert body["sectors"][0]["top_domain"] == "hotelparaiso.com.br"


def test_ranking_sector(client, store):
    # KL-78 item 3: selo só com score 100 + conta.
    store.sector_ranking["hotel"] = [
        {"domain": "hotelperfeito.com.br", "last_scan_score": 100, "has_account": True},
        {"domain": "hotelparaiso.com.br", "last_scan_score": 100, "has_account": False},
        {"domain": "pousadamar.com.br", "last_scan_score": 82, "has_account": True},
    ]
    body = client.get("/ranking/hotel").json()
    assert body["sector"] == "hotel"
    sites = body["sites"]
    assert len(sites) == 3
    assert sites[0]["position"] == 1 and sites[0]["domain"] == "hotelperfeito.com.br"
    assert sites[0]["badge"]["level"] == "high"   # 100 + conta → selo
    assert sites[1]["badge"] is None                  # 100 sem conta → sem selo
    assert sites[2]["badge"] is None                  # 82 → sem selo


# --- posição no ranking no /account/sites/{id} ------------------------------ #

def test_account_site_detail_ranking(client, store):
    store.users[7] = {"id": 7, "email": "u@x.com.br", "plan": "free",
                      "max_sites": 1, "is_active": True}
    store.user_sites[(7, 1)] = {"is_owner": True}
    store.accounts.add(1)   # KL-78 item 3: site monitorado → tem conta
    store.targets_by_id[1] = _target(last_scan_score=100)
    store.scans[1] = [{"id": 10, "score": 100, "semaphore": "verde",
                       "fail_count": 0, "scanned_at": datetime(2026, 7, 14, tzinfo=timezone.utc)}]
    store.profiles[1] = {"public_visible": True}
    store.positions[("tecnologia", 1)] = {"position": 12, "total": 471}
    tok = auth_users.create_user_token({"id": 7, "email": "u@x.com.br", "plan": "free"})
    r = client.get("/account/sites/1", headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 200
    body = r.json()
    assert body["badge"]["level"] == "high"
    assert body["ranking"]["position"] == 12 and body["ranking"]["total"] == 471
    # acima de (471-12)/471 ≈ 97%
    assert body["ranking"]["percentile"] == 97
    assert body["ranking"]["sector"] == "tecnologia"


# --- eventos registrados ---------------------------------------------------- #

def test_events_registered():
    for e in ("widget_loaded", "widget_clicked", "widget_copied",
              "card_downloaded", "share_clicked", "ranking_viewed"):
        assert e in m._KNOWN_EVENTS
