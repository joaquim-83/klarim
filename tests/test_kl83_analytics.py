"""KL-83 — analytics admin. Testa a DERIVAÇÃO pura (período, %, sparkline, funil, jornadas,
páginas) + os endpoints (auth, validação, shape, cache, paginação) com FakeStore. As
agregações SQL (`aa_*`) são validadas na VM (como os demais analytics_*). Offline."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

import api.main as m
import api.admin_analytics as aa

NOW = datetime(2026, 7, 19, 12, 0, tzinfo=timezone.utc)


# =========================================================================== #
# 1. resolve_period — validação
# =========================================================================== #

def test_period_fixed():
    for p, d in (("today", 1), ("7d", 7), ("30d", 30), ("90d", 90)):
        pr = aa.resolve_period(p, None, None, now=NOW)
        assert pr["days"] == d
        assert (pr["start"] - pr["prev_start"]).days == d   # período anterior de mesmo tamanho


def test_period_today_bounds():
    pr = aa.resolve_period("today", None, None, now=NOW)
    assert pr["start"].hour == 0 and pr["start"].date() == NOW.date()


def test_period_custom_valid():
    pr = aa.resolve_period("custom", "2026-07-01", "2026-07-10", now=NOW)
    assert pr["days"] == 10   # [01 00:00, 10+1 00:00) = 10 dias inclusivos


def test_period_custom_over_90_days():
    with pytest.raises(Exception) as e:
        aa.resolve_period("custom", "2026-01-01", "2026-07-01", now=NOW)
    assert getattr(e.value, "status_code", None) == 422


def test_period_custom_future():
    with pytest.raises(Exception) as e:
        aa.resolve_period("custom", "2026-08-01", "2026-08-05", now=NOW)
    assert getattr(e.value, "status_code", None) == 422


def test_period_custom_missing_dates():
    with pytest.raises(Exception) as e:
        aa.resolve_period("custom", None, None, now=NOW)
    assert getattr(e.value, "status_code", None) == 422


def test_period_custom_bad_date():
    with pytest.raises(Exception) as e:
        aa.resolve_period("custom", "not-a-date", "2026-07-10", now=NOW)
    assert getattr(e.value, "status_code", None) == 422


def test_period_custom_end_before_start():
    with pytest.raises(Exception) as e:
        aa.resolve_period("custom", "2026-07-10", "2026-07-01", now=NOW)
    assert getattr(e.value, "status_code", None) == 422


def test_period_invalid():
    with pytest.raises(Exception) as e:
        aa.resolve_period("1y", None, None, now=NOW)
    assert getattr(e.value, "status_code", None) == 422


# =========================================================================== #
# 2. Helpers puros
# =========================================================================== #

def test_pct_change():
    assert aa.pct_change(120, 100) == 20.0
    assert aa.pct_change(80, 100) == -20.0
    assert aa.pct_change(50, 50) == 0.0
    assert aa.pct_change(5, 0) is None       # sem base → None


def test_day_list():
    pr = aa.resolve_period("7d", None, None, now=NOW)
    dl = aa.day_list(pr["start"], pr["days"])
    assert len(dl) == 7 and dl == sorted(dl)


def test_normalize_path():
    assert aa.normalize_path("/site/hotel.com.br") == "/site/{domain}"
    assert aa.normalize_path("/setor/hotelaria?x=1") == "/setor/{slug}"
    assert aa.normalize_path("/cadastrar") == "/cadastrar"
    assert aa.normalize_path("") == "/"


def test_page_group():
    assert aa._page_group("/site/x.com") == "Perfis públicos"
    assert aa._page_group("/setor/y") == "Páginas de setor"
    assert aa._page_group("/scan") == "Scans"
    assert aa._page_group("/dashboard") == "Cadastro/Login"
    assert aa._page_group("/algo") == "Outras"


# =========================================================================== #
# 3. assemble_metrics
# =========================================================================== #

def _raw(v, pvw, sc, ac, al, cl, pvs=None):
    """Monta um dict de agregação bruta (totais; daily simplificado num só dia). `pvs` =
    sessões com page_view (default = visitantes, p/ compat)."""
    def one(total):
        return {"daily": {"2026-07-13": total}, "total": total}
    return {"visitors": one(v), "pageviews": one(pvw), "scans": one(sc),
            "accounts": one(ac), "alerts_sent": one(al), "alert_clicks": one(cl),
            "pageview_sessions": one(v if pvs is None else pvs)}


def test_assemble_metrics_values_and_change():
    cur = _raw(200, 600, 50, 10, 100, 40)
    prev = _raw(100, 200, 25, 4, 80, 20)
    days = ["2026-07-13"]
    m6 = aa.assemble_metrics(cur, prev, days)
    assert m6["unique_visitors"]["value"] == 200 and m6["unique_visitors"]["change_pct"] == 100.0
    assert m6["scans_manual"]["value"] == 50
    assert m6["accounts_created"]["value"] == 10
    # conversão = 10/200*100 = 5.0
    assert m6["conversion_rate"]["value"] == 5.0
    # pageviews/sessão = 600 / 200 sessões-com-pageview = 3.0
    assert m6["pageviews_per_session"]["value"] == 3.0
    # alert_click_rate = 40/100*100 = 40.0
    assert m6["alert_click_rate"]["value"] == 40.0
    assert len(m6["unique_visitors"]["sparkline"]) == 1


def test_pageviews_per_session_uses_pageview_sessions():
    # KL-64 fix: 600 pageviews, 400 VISITANTES, mas só 300 sessões têm page_view → 600/300 = 2.0
    # (>= 1). Com o denominador antigo (visitantes) daria 600/400 = 1.5. Nunca < 1.
    cur = _raw(400, 600, 0, 0, 0, 0, pvs=300)
    prev = _raw(200, 300, 0, 0, 0, 0, pvs=200)
    m6 = aa.assemble_metrics(cur, prev, ["2026-07-13"])
    assert m6["pageviews_per_session"]["value"] == 2.0
    assert m6["pageviews_per_session"]["value"] >= 1.0


def test_assemble_metrics_zero_safe():
    cur = _raw(0, 0, 0, 0, 0, 0)
    prev = _raw(0, 0, 0, 0, 0, 0)
    m6 = aa.assemble_metrics(cur, prev, ["2026-07-13"])
    assert m6["conversion_rate"]["value"] == 0
    assert m6["unique_visitors"]["change_pct"] is None   # previous 0


# =========================================================================== #
# 4. assemble_funnel
# =========================================================================== #

def _funnel_raw(totals):
    order = ["emails_sent", "clicks", "result_viewed", "scan_started",
             "account_created", "payment_created", "payment_completed"]
    return {name: {"total": t, "by_campaign": {"alerta": t}} for name, t in zip(order, totals)}


def test_assemble_funnel_conversion_and_bottleneck():
    raw = _funnel_raw([1000, 100, 50, 40, 4, 2, 0])
    stages = aa.assemble_funnel(raw)
    assert [s["name"] for s in stages][0] == "emails_sent"
    assert stages[0]["conversion_from_previous"] is None       # 1ª etapa
    assert stages[1]["conversion_from_previous"] == 10.0       # 100/1000
    assert stages[4]["conversion_from_previous"] == 10.0       # 4/40
    # gargalo = menor conversão (payment_completed 0/2=0.0)
    assert stages[-1]["bottleneck"] is True
    assert sum(1 for s in stages if s["bottleneck"]) == 1


# =========================================================================== #
# 5. assemble_journeys
# =========================================================================== #

def _sess(pages, campaign=None, converted=False):
    evs = [{"event_type": "page_view", "page_url": p, "utm_campaign": campaign} for p in pages]
    if converted:
        evs.append({"event_type": "account_created", "page_url": None, "utm_campaign": campaign})
    return evs


def test_assemble_journeys_grouping_and_alerta_and_saiu():
    sessions = [
        _sess(["/", "/site/a.com", "/cadastrar"], converted=True),
        _sess(["/", "/site/b.com", "/cadastrar"], converted=True),   # mesma sequência normalizada
        _sess(["/site/c.com"], campaign="alerta"),                    # alerta + saiu
    ]
    js = aa.assemble_journeys(sessions, limit=10)
    top = js[0]
    assert top["sequence"] == ["/", "/site/{domain}", "/cadastrar"] and top["count"] == 2
    assert top["converted"] == 2 and top["conversion_rate"] == 100.0
    alerta = next(j for j in js if j["sequence"][0] == "alerta")
    assert alerta["sequence"] == ["alerta", "/site/{domain}", "[saiu]"]


# =========================================================================== #
# 6. assemble_pages
# =========================================================================== #

def test_assemble_pages_bounce_next_conversion_delta():
    rows = [{"page_url": "/site/a.com", "views": 10, "sessions": 4}]
    sessions = [
        _sess(["/site/a.com"]),                                # bounce (1 pageview)
        _sess(["/site/a.com", "/cadastrar"], converted=True),  # next=/cadastrar + converteu
        _sess(["/site/a.com", "/cadastrar"]),
        _sess(["/site/a.com", "/scan"]),
    ]
    prev_views = {"/site/a.com": 6}
    out = aa.assemble_pages(rows, sessions, prev_views)
    p = out["pages"][0]
    assert p["path"] == "/site/a.com" and p["views"] == 10
    assert p["bounce_rate"] == 25.0            # 1 de 4 sessões
    assert p["next_page"] == "/cadastrar"      # 2x vs /scan 1x
    assert p["conversion"] == 25.0             # 1 de 4 converteu
    assert p["delta_views"] == 4               # 10 - 6
    assert out["groups"][0]["group"] == "Perfis públicos"


# =========================================================================== #
# 7. Endpoints (FakeStore + TestClient)
# =========================================================================== #

class FakeStore:
    def __init__(self):
        self.metrics_calls = 0

    async def aa_metrics_raw(self, start, end, include_bots=False):
        self.metrics_calls += 1
        self.last_include_bots = include_bots
        return _raw(200, 600, 50, 10, 100, 40)

    async def aa_funnel_raw(self, start, end, include_bots=False):
        self.last_include_bots = include_bots
        return _funnel_raw([1000, 100, 50, 40, 4, 2, 0])

    async def aa_events(self, start, end, types, domain, campaign, path, offset, limit, include_bots=False):
        self.last_include_bots = include_bots
        evs = [{"id": i, "event_type": "page_view", "session_id": f"s{i}",
                "target_url": None, "page_url": "/", "utm_campaign": None,
                "referrer": None, "metadata": {}, "created_at": NOW} for i in range(limit)]
        return {"events": evs, "total": 152,
                "counters": {"events": 152, "sessions": 43, "domains": 12, "scans": 3, "accounts": 1}}

    async def aa_events_export(self, start, end, types, domain, campaign, path,
                               include_bots=False, limit=10000):
        self.last_include_bots = include_bots
        n = min(getattr(self, "export_rows", 3), limit + 1)
        return [(NOW, "page_view", "/site/a.com", "https://a.com", "alerta",
                 f"s{i}", True, "https://google.com") for i in range(n)]

    async def aa_sessions(self, start, end, offset, limit, include_bots=False):
        return {"sessions": [{"session_id": "abc", "event_count": 2,
                              "first_event_at": NOW, "last_event_at": NOW,
                              "converted": True, "campaign": "alerta",
                              "events": []}], "total": 5}

    async def aa_pages_raw(self, start, end, search, limit=200, include_bots=False):
        return [{"page_url": "/site/a.com", "views": 10, "sessions": 4}]

    async def aa_journeys_raw(self, start, end, max_sessions=3000, include_bots=False):
        return [_sess(["/", "/site/a.com"], converted=True)]

    async def aa_funnel_by_sector(self, start, end, limit=30, include_bots=False):
        return [{"sector": "hotelaria", "clicks": 45, "scans": 8, "accounts": 0}]


@pytest.fixture
def store(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "k" * 64)
    monkeypatch.setenv("ADMIN_USER", "op")
    s = FakeStore()
    monkeypatch.setattr("discovery.store.get_target_store", lambda: s)
    monkeypatch.setattr(aa, "get_target_store", lambda: s)
    # cache no-op (força recomputar; um teste específico valida o cache real)
    monkeypatch.setattr(m, "_cache_get", lambda k: _none())
    monkeypatch.setattr(m, "_cache_set", lambda k, v, ttl=300: _none())
    return s


async def _none():
    return None


@pytest.fixture
def client(store):
    return TestClient(m.app, raise_server_exceptions=False)


def _admin():
    return {"Authorization": f"Bearer {m._create_token('op')}"}


def test_metrics_requires_admin(client):
    assert client.get("/admin/analytics/metrics?period=7d").status_code == 401


def test_metrics_endpoint(client):
    j = client.get("/admin/analytics/metrics?period=7d", headers=_admin()).json()
    assert set(j["metrics"].keys()) == {
        "unique_visitors", "scans_manual", "accounts_created",
        "conversion_rate", "pageviews_per_session", "alert_click_rate"}
    assert j["metrics"]["unique_visitors"]["value"] == 200
    assert "sparkline" in j["metrics"]["unique_visitors"]
    assert j["period"]["days"] == 7


def test_metrics_invalid_period_422(client):
    assert client.get("/admin/analytics/metrics?period=5y", headers=_admin()).status_code == 422


def test_trend_endpoint(client):
    j = client.get("/admin/analytics/trend?period=7d&metrics=visitors,scans", headers=_admin()).json()
    assert len(j["dates"]) == 7
    assert set(j["series"].keys()) == {"visitors", "scans"}


def test_funnel_endpoint(client):
    j = client.get("/admin/analytics/funnel?period=7d", headers=_admin()).json()
    assert len(j["stages"]) == 7
    assert j["stages"][0]["name"] == "emails_sent"
    assert j["stages"][1]["conversion_from_previous"] == 10.0
    assert "comparison" in j


def test_events_pagination_and_counters(client):
    j = client.get("/admin/analytics/events?period=7d&page=1&limit=50", headers=_admin()).json()
    assert j["pagination"]["total"] == 152 and j["pagination"]["pages"] == 4
    assert j["counters"]["sessions"] == 43
    assert len(j["events"]) == 50


def test_events_filters_passthrough(client):
    r = client.get("/admin/analytics/events?period=today&type=page_view,scan_started&domain=hotel.com.br&campaign=alerta",
                   headers=_admin())
    assert r.status_code == 200


def test_sessions_endpoint(client):
    j = client.get("/admin/analytics/sessions?period=7d", headers=_admin()).json()
    assert j["pagination"]["total"] == 5
    assert j["sessions"][0]["converted"] is True


def test_journeys_and_pages_and_sector(client):
    assert client.get("/admin/analytics/journeys?period=7d", headers=_admin()).status_code == 200
    assert client.get("/admin/analytics/pages?period=7d", headers=_admin()).status_code == 200
    js = client.get("/admin/analytics/funnel-by-sector?period=7d", headers=_admin()).json()
    assert js["sectors"][0]["sector"] == "hotelaria"


def test_events_limit_capped(client):
    # limit > 100 é rejeitado pelo Query(le=100)
    assert client.get("/admin/analytics/events?limit=500", headers=_admin()).status_code == 422


def test_events_invalid_page(client):
    # page < 1 rejeitado (ge=1)
    assert client.get("/admin/analytics/events?page=0", headers=_admin()).status_code == 422


def test_metrics_custom_period_endpoint(client):
    j = client.get("/admin/analytics/metrics?period=custom&start=2026-07-01&end=2026-07-07",
                   headers=_admin()).json()
    assert j["period"]["days"] == 7


def test_funnel_by_sector_click_rate(client):
    js = client.get("/admin/analytics/funnel-by-sector?period=7d", headers=_admin()).json()
    s = js["sectors"][0]
    # click_rate = scans/clicks*100 = 8/45*100 ≈ 17.8
    assert s["click_rate"] == 17.8


def test_pages_sort_order(client):
    r = client.get("/admin/analytics/pages?period=7d&sort=bounce_rate&order=asc", headers=_admin())
    assert r.status_code == 200 and "pages" in r.json()


def test_trend_ignores_unknown_metric(client):
    j = client.get("/admin/analytics/trend?period=7d&metrics=visitors,bogus", headers=_admin()).json()
    assert set(j["series"].keys()) == {"visitors"}   # 'bogus' descartado


def test_metrics_cache_hit(monkeypatch, store):
    """Com cache REAL (in-memory), a 2ª chamada não recomputa (store chamado 1x)."""
    monkeypatch.setenv("JWT_SECRET", "k" * 64)
    cache: dict = {}

    async def cget(k):
        return cache.get(k)

    async def cset(k, v, ttl=300):
        cache[k] = v

    monkeypatch.setattr(m, "_cache_get", cget)
    monkeypatch.setattr(m, "_cache_set", cset)
    client = TestClient(m.app, raise_server_exceptions=False)
    client.get("/admin/analytics/metrics?period=7d", headers=_admin())
    calls_after_first = store.metrics_calls
    client.get("/admin/analytics/metrics?period=7d", headers=_admin())
    assert store.metrics_calls == calls_after_first   # 2ª veio do cache


# --------------------------------------------------------------------------- #
# KL-64 — include_bots passthrough + export CSV server-side
# --------------------------------------------------------------------------- #

def test_include_bots_passthrough(store, client):
    # default (sem param) → só humanos; ?include_bots=true → tudo (chega no store).
    client.get("/admin/analytics/metrics?period=7d", headers=_admin())
    assert store.last_include_bots is False
    client.get("/admin/analytics/metrics?period=7d&include_bots=true", headers=_admin())
    assert store.last_include_bots is True
    client.get("/admin/analytics/funnel?period=7d&include_bots=true", headers=_admin())
    assert store.last_include_bots is True


def test_export_requires_admin(client):
    assert client.get("/admin/analytics/events/export?period=7d").status_code == 401


def test_export_csv_headers_and_content(store, client):
    r = client.get("/admin/analytics/events/export?period=7d", headers=_admin())
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")
    assert 'attachment; filename="klarim-events-' in r.headers["content-disposition"]
    lines = r.text.strip().splitlines()
    assert lines[0] == "timestamp,event_type,page,domain,campaign,session_id,is_human,referrer"
    assert len(lines) == 1 + 3          # header + 3 linhas (fake)
    assert "X-Truncated" not in r.headers


def test_export_default_is_human_only(store, client):
    client.get("/admin/analytics/events/export?period=7d", headers=_admin())
    assert store.last_include_bots is False
    client.get("/admin/analytics/events/export?period=7d&include_bots=true", headers=_admin())
    assert store.last_include_bots is True


def test_export_truncation_flag(store, client):
    store.export_rows = 10001            # > 10.000 → trunca + header + linha de aviso
    r = client.get("/admin/analytics/events/export?period=7d", headers=_admin())
    assert r.status_code == 200
    assert r.headers.get("x-truncated") == "true"
    assert "Exportacao limitada a 10000" in r.text
