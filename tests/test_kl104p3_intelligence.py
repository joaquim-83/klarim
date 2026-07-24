"""KL-104 P3 — Visão 360° do alvo. Testa as montagens PURAS (funil, fontes de tráfego,
mascaramento de IP, merge/paginação da timeline), o orquestrador com degradação graciosa,
e o endpoint (auth/404/200). Offline (FakeStore). O SQL é validado na VM.
"""

from __future__ import annotations

from datetime import datetime

import pytest
from fastapi.testclient import TestClient

import api.main as m
from api import target_intelligence as ti


def _dt(y, mo, d, h=0, mi=0):
    return datetime(y, mo, d, h, mi)


# --------------------------- funil ----------------------------------------- #

def test_build_funnel_active_and_stage():
    target = {"discovered_at": _dt(2026, 7, 10), "last_scan_at": _dt(2026, 7, 11)}
    flags = {"first_alert_at": _dt(2026, 7, 12), "account_at": None,
             "monitoring_at": None, "paid_at": None}
    out = ti.build_funnel(target, flags)
    assert out["funnel_stage"] == "alerted"
    by = {s["stage"]: s for s in out["funnel_stages"]}
    assert by["discovered"]["active"] and by["scanned"]["active"] and by["alerted"]["active"]
    assert not by["account_created"]["active"] and not by["paid"]["active"]
    assert by["alerted"]["at"].startswith("2026-07-12")


def test_build_funnel_defaults_to_discovered():
    out = ti.build_funnel({"discovered_at": None}, {})
    assert out["funnel_stage"] == "discovered"


def test_lead_class_bands():
    assert ti._lead_class(72) == "hot"
    assert ti._lead_class(45) == "warm"
    assert ti._lead_class(10) == "cold"
    assert ti._lead_class(None) is None


# --------------------------- fontes de tráfego ----------------------------- #

def test_classify_traffic_source():
    assert ti.classify_traffic_source(None) == "direct"
    assert ti.classify_traffic_source("") == "direct"
    assert ti.classify_traffic_source("https://alertas.klarim.net/x") == "alert_email"
    assert ti.classify_traffic_source("https://perfil.klarim.net") == "profile_view"
    assert ti.classify_traffic_source("https://www.google.com/") == "google"
    assert ti.classify_traffic_source("https://klarim.net/setores") == "internal"
    assert ti.classify_traffic_source("https://t.co/abc") == "other"


def test_assemble_traffic_sources_aggregates():
    rows = [{"referrer": None, "count": 30},
            {"referrer": "https://alertas.klarim.net", "count": 10},
            {"referrer": "https://google.com", "count": 2}]
    out = ti.assemble_traffic_sources(rows)
    assert out == {"direct": 30, "alert_email": 10, "google": 2}
    assert ti.assemble_traffic_sources([]) is None


# --------------------------- visitantes ------------------------------------ #

def _mask(ip, octets=3):
    from api.access_log_middleware import mask_ip
    return mask_ip(ip, octets)


def test_assemble_visitors_masks_ip_and_attaches_cross_site():
    raw = {"total_queries": 47, "unique_ips": 12, "top_ips": [
        {"ip": "189.44.103.27", "queries": 8, "first_seen": _dt(2026, 7, 5),
         "last_seen": _dt(2026, 7, 23), "country": "BR"}]}
    cross = [{"ip": "189.44.103.27", "domain_queried": "outro.com.br"},
             {"ip": "189.44.103.27", "domain_queried": "terceiro.com.br"}]
    domain_ids = {"outro.com.br": 555}
    out = ti.assemble_visitors(raw, cross, domain_ids, _mask)
    top = out["top_ips"][0]
    assert top["ip_masked"] == "189.44.103.x"          # /24, nunca IP completo
    assert "27" not in top["ip_masked"]
    doms = {d["domain"]: d["target_id"] for d in top["other_domains_queried"]}
    assert doms == {"outro.com.br": 555, "terceiro.com.br": None}
    assert out["period"] == "last_30_days"


def test_assemble_visitors_caps_cross_site_per_ip():
    raw = {"total_queries": 1, "unique_ips": 1,
           "top_ips": [{"ip": "10.0.0.1", "queries": 1}]}
    cross = [{"ip": "10.0.0.1", "domain_queried": f"d{i}.com"} for i in range(12)]
    out = ti.assemble_visitors(raw, cross, {}, _mask, per_ip=5)
    assert len(out["top_ips"][0]["other_domains_queried"]) == 5


def test_assemble_visitors_none_when_no_raw():
    assert ti.assemble_visitors(None, [], {}, _mask) is None


# --------------------------- timeline -------------------------------------- #

def test_assemble_timeline_merges_and_sorts_desc():
    scans = [{"id": 9, "at": _dt(2026, 7, 23, 0, 38), "score": 70, "semaphore": "🟡",
              "pass_count": 10, "fail_count": 4}]
    alerts = [{"at": _dt(2026, 7, 24, 7, 57), "from_domain": "alertas.klarim.net", "status": "sent"}]
    pviews = [{"at": _dt(2026, 7, 22, 14, 20), "ip": "189.44.103.27", "country_code": "BR"}]
    target = {"discovered_at": _dt(2026, 7, 20, 9, 15), "source": "CT log"}
    out = ti.assemble_timeline(scans, alerts, pviews, [], target, _mask, limit=30)
    types = [e["type"] for e in out["events"]]
    assert types == ["alert_sent", "scan_complete", "profile_viewed", "discovered"]
    assert out["events"][0]["link"] is None
    assert out["events"][1]["link"] == "/painel/scans/9"
    # IP mascarado no evento de perfil
    pv = next(e for e in out["events"] if e["type"] == "profile_viewed")
    assert "189.44.103.x" in pv["detail"] and "27" not in pv["detail"]
    assert out["has_more"] is False and out["next_cursor"] is None


def test_assemble_timeline_pagination_cursor():
    scans = [{"id": i, "at": _dt(2026, 7, 20, 0, i), "score": 50, "semaphore": "🟡",
              "pass_count": 1, "fail_count": 1} for i in range(5)]
    out = ti.assemble_timeline(scans, [], [], [], {"discovered_at": None}, _mask, limit=3,
                               include_discovered=True)
    assert len(out["events"]) == 3
    assert out["has_more"] is True
    assert out["next_cursor"] is not None


def test_assemble_timeline_discovered_only_first_page():
    target = {"discovered_at": _dt(2026, 7, 20), "source": "CT log"}
    out = ti.assemble_timeline([], [], [], [], target, _mask, include_discovered=False)
    assert all(e["type"] != "discovered" for e in out["events"])


def test_parse_cursor():
    assert ti.parse_cursor(None) is None
    assert ti.parse_cursor("lixo") is None
    d = ti.parse_cursor("2026-07-24T07:57:00Z")
    assert d is not None and d.tzinfo is None and d.year == 2026


# --------------------------- orquestrador (FakeStore) ---------------------- #

class FakeIntelStore:
    async def ti_monitors(self, tid):
        return [{"email": "dono@x.com", "plan": "pro", "account_level": 2,
                 "added_at": _dt(2026, 7, 15), "is_owner": True}]

    async def ti_vigilias(self, domain):
        return [{"tipo": "ssl", "last_status": "ok", "last_check_at": _dt(2026, 7, 24),
                 "next_check_at": _dt(2026, 7, 25), "enabled": True}]

    async def ti_ownership(self, tid):
        return {"method": "dns_txt", "verified_at": _dt(2026, 7, 10)}

    async def ti_technician(self, tid):
        return {"email": "tec@a.com", "status": "active", "linked_at": _dt(2026, 7, 12),
                "invited_at": _dt(2026, 7, 11)}

    async def ti_funnel_flags(self, tid, url, domain):
        return {"first_alert_at": _dt(2026, 7, 12), "account_at": None,
                "monitoring_at": None, "paid_at": None}

    async def ti_emails(self, tid, limit=20):
        return [{"email_type": "alert", "sent_at": _dt(2026, 7, 12), "from_domain":
                 "alertas.klarim.net", "status": "sent", "email_id": "re_1"}]

    async def ti_emails_summary(self, tid):
        return {"total": 1, "last_sent_at": _dt(2026, 7, 12),
                "by_type": {"alert": 1}, "by_status": {"sent": 1}}

    async def ti_visitors(self, domain, days=30, top=10):
        return {"total_queries": 5, "unique_ips": 2, "top_ips": [
            {"ip": "189.44.103.27", "queries": 3, "first_seen": _dt(2026, 7, 5),
             "last_seen": _dt(2026, 7, 23), "country": "BR"}]}

    async def ti_cross_site(self, ips, domain, days=30):
        return [{"ip": "189.44.103.27", "domain_queried": "outro.com.br"}]

    async def ti_traffic_sources(self, domain, days=30):
        return [{"referrer": None, "count": 5}]

    async def ti_domain_ids(self, domains):
        return {"outro.com.br": 42}

    async def ti_tl_scans(self, tid, before, limit=30):
        return [{"id": 9, "at": _dt(2026, 7, 23), "score": 70, "semaphore": "🟡",
                 "pass_count": 10, "fail_count": 4}]

    async def ti_tl_alerts(self, tid, before, limit=30):
        return [{"at": _dt(2026, 7, 24), "from_domain": "alertas.klarim.net", "status": "sent"}]

    async def ti_tl_profile_views(self, domain, before, limit=30):
        return [{"at": _dt(2026, 7, 22), "ip": "189.44.103.27", "country_code": "BR"}]

    async def ti_tl_status(self, tid, before, limit=30):
        return []


TARGET = {"id": 1, "domain": "x.com.br", "url": "https://x.com.br",
          "discovered_at": _dt(2026, 7, 10), "last_scan_at": _dt(2026, 7, 11),
          "source": "ct_log", "owner_verified": True, "alert_quality_score": 72}


@pytest.mark.asyncio
async def test_build_intelligence_full():
    out = await ti.build_intelligence(FakeIntelStore(), TARGET, before=None, limit=30, mask=_mask)
    assert set(out) == {"monitoring", "funnel", "visitors", "timeline"}
    assert out["monitoring"]["monitors"][0]["user_email"] == "dono@x.com"
    assert out["monitoring"]["owner_verified"]["method"] == "dns_txt"
    assert out["monitoring"]["technician"]["email"] == "tec@a.com"
    assert out["funnel"]["funnel_stage"] == "alerted"
    assert out["funnel"]["lead_score"] == {"score": 72, "classification": "hot"}
    assert out["visitors"]["top_ips"][0]["ip_masked"] == "189.44.103.x"
    assert out["visitors"]["top_ips"][0]["other_domains_queried"][0]["target_id"] == 42
    assert out["visitors"]["traffic_sources"] == {"direct": 5}
    assert out["timeline"]["events"][0]["type"] == "alert_sent"


@pytest.mark.asyncio
async def test_section_failure_is_isolated():
    class Broken(FakeIntelStore):
        async def ti_visitors(self, domain, days=30, top=10):
            raise RuntimeError("boom")  # tabela access_log inacessível
    out = await ti.build_intelligence(Broken(), TARGET, mask=_mask)
    assert out["visitors"] is None                     # seção degrada (sem dados)
    assert out["funnel"]["funnel_stage"] == "alerted"  # as outras seguem intactas
    assert out["monitoring"]["monitors"]
    assert out["timeline"]["events"]


@pytest.mark.asyncio
async def test_missing_subtable_degrades_gracefully():
    class NoTech(FakeIntelStore):
        async def ti_technician(self, tid):
            raise RuntimeError("technician_links não existe")
    out = await ti.build_intelligence(NoTech(), TARGET, mask=_mask)
    assert out["monitoring"]["technician"] is None      # sub-query cai p/ None
    assert out["monitoring"]["monitors"]                # o resto da seção fica


# --------------------------- endpoint (auth/404/200) ----------------------- #

class EndpointStore(FakeIntelStore):
    async def get_target(self, tid):
        return dict(TARGET) if tid == 1 else None


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("ADMIN_USER", "admin")
    monkeypatch.setenv("ADMIN_PASSWORD", "s3nha-forte-1234")
    monkeypatch.setenv("JWT_SECRET", "x" * 64)
    monkeypatch.setattr(m, "get_target_store", lambda: EndpointStore())
    c = TestClient(m.app, raise_server_exceptions=False)
    return c


def _auth(client):
    r = client.post("/auth/login", json={"username": "admin", "password": "s3nha-forte-1234"})
    return {"Authorization": f"Bearer {r.json()['token']}"}


def test_endpoint_requires_auth(client):
    assert client.get("/admin/targets/1/intelligence").status_code == 401


def test_endpoint_404_for_missing_target(client):
    r = client.get("/admin/targets/999/intelligence", headers=_auth(client))
    assert r.status_code == 404


def test_endpoint_200_with_four_sections(client):
    r = client.get("/admin/targets/1/intelligence", headers=_auth(client))
    assert r.status_code == 200
    d = r.json()
    assert set(d) == {"monitoring", "funnel", "visitors", "timeline"}
    # IP nunca completo no response
    assert "189.44.103.27" not in r.text
    assert d["visitors"]["top_ips"][0]["ip_masked"] == "189.44.103.x"
