"""Testes dos perfis públicos SEO (KL-51 f4) — offline (TestClient + FakeStore).

Cobre: /public/profile/{domain} (estados + privacidade), /public/sitemap-domains,
/notify/profile-view, og SVG (puro) e o fail-open do /og/{domain}.png. O render real
do PNG precisa do libcairo (ausente no CI) — testamos o SVG e o fail-open.
"""

from __future__ import annotations

import xml.dom.minidom as minidom
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

import api.main as m


class FakeStore:
    def __init__(self):
        self.targets = {}       # domain -> target dict
        self.profiles = {}      # target_id -> profile
        self.classifications = {}  # target_id -> [cnae]
        self.scans = {}         # target_id -> [scan]
        self.users = set()      # e-mails registrados
        self.public_domains = []

    async def get_target_by_domain(self, domain):
        return self.targets.get(domain.lower().strip())

    async def get_site_profile(self, tid):
        return self.profiles.get(tid)

    async def get_target_classifications(self, tid):
        return self.classifications.get(tid, [])

    async def list_scans(self, target_id=None, limit=1, **kw):
        return self.scans.get(target_id, [])[:limit]

    async def sector_avg_score(self, sector):
        return {"avg_score": 68, "count": 20}

    async def global_avg_score(self):
        return {"avg_score": 70, "count": 500}

    async def list_public_profile_domains(self, limit=50000):
        return self.public_domains

    async def get_user_by_email(self, email, with_hash=False):
        return {"id": 1, "email": email} if email.lower() in self.users else None

    async def site_has_owner(self, tid, exclude_user_id=None):  # KL-68
        return False


@pytest.fixture
def store(monkeypatch):
    s = FakeStore()
    monkeypatch.setattr(m, "get_target_store", lambda: s)
    monkeypatch.setattr("discovery.store.get_target_store", lambda: s)
    return s


@pytest.fixture
def client(store):
    return TestClient(m.app, raise_server_exceptions=False)


def _target(**kw):
    base = {"id": 1, "url": "https://hotelparaiso.com.br", "domain": "hotelparaiso.com.br",
            "sector": "hotel", "platform": "WordPress", "status": "scanned",
            "last_scan_score": 74, "last_scan_at": datetime(2026, 7, 13, tzinfo=timezone.utc),
            "contact_email": "dono@hotelparaiso.com.br"}
    base.update(kw)
    return base


# --- og SVG (puro) ---------------------------------------------------------- #

def test_og_svg_valid_xml():
    svg = m._og_svg("hotelparaiso.com.br", 74, "amarelo", "Hotel boutique à beira-mar & spa.")
    minidom.parseString(svg)  # XML válido
    assert "74" in svg and "#F0C000" in svg and "hotelparaiso.com.br" in svg


# --- /public/profile/{domain} ---------------------------------------------- #

def test_profile_not_found(client, store):
    assert client.get("/public/profile/inexistente.com.br").json()["status"] == "not_found"


def test_profile_discarded(client, store):
    store.targets["x.com.br"] = _target(domain="x.com.br", status="descartado")
    assert client.get("/public/profile/x.com.br").json()["status"] == "discarded"


def test_profile_not_scanned(client, store):
    store.targets["x.com.br"] = _target(domain="x.com.br", last_scan_score=None)
    assert client.get("/public/profile/x.com.br").json()["status"] == "not_scanned"


def test_profile_ok_and_privacy(client, store):
    store.targets["hotelparaiso.com.br"] = _target()
    store.profiles[1] = {
        "description": "Hotel boutique.", "business_type": "Hotel", "tags": ["hotel", "spa"],
        "maturity_score": 6, "phone": "(48) 3333-4444", "address": "Florianópolis",
        "cnpj": "11.222.333/0001-81", "commercial_email": "dono@hotelparaiso.com.br",
        "whatsapp": "5548999999999",
    }
    store.classifications[1] = [{"cnae_code": "55.10-8", "cnae_description": "Hotelaria"}]
    store.scans[1] = [{"semaphore": "amarelo"}]
    body = client.get("/public/profile/hotelparaiso.com.br").json()
    assert body["status"] == "ok"
    assert body["target"]["score"] == 74 and body["target"]["semaphore"] == "amarelo"
    assert body["profile"]["business_type"] == "Hotel"
    assert body["benchmark"]["count"] == 20
    # privacidade: nada de e-mail de contato, cnpj ou whatsapp no payload público
    blob = str(body)
    assert "cnpj" not in body["profile"] and "commercial_email" not in body["profile"]
    assert "whatsapp" not in body["profile"]
    assert "contact_email" not in body["target"]
    assert "id" not in body["target"]  # KL-44 fix (auditoria F-03): PK interna não exposta
    assert "11.222.333" not in blob and "5548999999999" not in blob


def test_profile_www_normalized(client, store):
    store.targets["hotelparaiso.com.br"] = _target()
    # /site/www.hotelparaiso.com.br deve resolver o mesmo alvo (normaliza www.)
    assert client.get("/public/profile/www.hotelparaiso.com.br").json()["status"] == "ok"


# --- sitemap domains -------------------------------------------------------- #

def test_sitemap_domains(client, store):
    store.public_domains = [
        {"domain": "a.com.br", "last_scan_at": datetime(2026, 7, 13, tzinfo=timezone.utc)},
        {"domain": "b.com.br", "last_scan_at": None},
    ]
    body = client.get("/public/sitemap-domains").json()
    assert len(body["domains"]) == 2
    assert body["domains"][0] == {"domain": "a.com.br", "lastmod": "2026-07-13"}
    assert body["domains"][1]["lastmod"] is None


# --- notify + og fail-open -------------------------------------------------- #

def test_notify_profile_view_ok(client, store):
    assert client.post("/notify/profile-view", json={"domain": "hotelparaiso.com.br"}).json()["ok"] is True


# --- KL-44: anti-loop — visita do próprio dono vinda do e-mail de alerta ------ #

def _capture_spawn(monkeypatch):
    """Substitui _spawn para registrar se a notificação foi agendada — sem rodá-la."""
    spawned = []

    def _fake(coro):
        spawned.append(coro)
        coro.close()  # evita o warning 'coroutine was never awaited'

    monkeypatch.setattr(m, "_spawn", _fake)
    return spawned


def test_notify_skips_alert_utm(client, store, monkeypatch):
    # Dono clicando no link do e-mail de alerta (utm_campaign=alerta) → NÃO notifica.
    store.targets["hotelparaiso.com.br"] = _target()
    spawned = _capture_spawn(monkeypatch)
    body = client.post(
        "/notify/profile-view",
        json={"domain": "hotelparaiso.com.br", "utm_campaign": "alerta"},
    ).json()
    assert body == {"ok": True, "notified": False}
    assert spawned == []  # nenhuma notificação agendada (anti-loop)


def test_notify_skips_alert_score100_utm(client, store, monkeypatch):
    # A campanha do score 100 (alerta_score100) também é ignorada (prefixo 'alerta').
    store.targets["hotelparaiso.com.br"] = _target()
    spawned = _capture_spawn(monkeypatch)
    body = client.post(
        "/notify/profile-view",
        json={"domain": "hotelparaiso.com.br", "utm_campaign": "alerta_score100"},
    ).json()
    assert body["notified"] is False and spawned == []


def test_notify_organic_still_notifies(client, store, monkeypatch):
    # Visita orgânica (sem utm de alerta) continua agendando a notificação ao dono.
    store.targets["hotelparaiso.com.br"] = _target()
    spawned = _capture_spawn(monkeypatch)
    r = client.post("/notify/profile-view", json={"domain": "hotelparaiso.com.br"}).json()
    assert r["ok"] is True and len(spawned) == 1


def test_og_image_fallback_when_missing(client, store):
    # alvo inexistente → 302 para o favicon (fail-open, antes do cairosvg)
    r = client.get("/og/inexistente.com.br.png", follow_redirects=False)
    assert r.status_code == 302 and "favicon" in r.headers.get("location", "")
