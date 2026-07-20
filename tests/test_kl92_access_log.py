"""KL-92 — access log server-side (fonte de verdade das métricas de visitante).

Testa: classificação de bot (pura), helpers do middleware (skip de assets, extração de
IP/país/domínio, mascaramento LGPD), buffer + flush, processamento em background
(classificação + retroatividade), os 3 endpoints admin (auth/shape/mascaramento) e a
anonimização LGPD. As agregações SQL (`al_*`) são validadas na VM (como os demais aa_*).
Offline."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request

import api.main as m
import api.admin_analytics as aa
import api.bot_classifier as bc
import api.access_log_middleware as alm

NOW = datetime(2026, 7, 20, 15, 30, tzinfo=timezone.utc)


# =========================================================================== #
# 1. Classificação de bot — datacenter / crawler / rate / prefetch (pura)
# =========================================================================== #

def test_datacenter_aws():
    assert bc.is_datacenter_ip("52.23.44.1") is True
    assert bc.is_datacenter_ip("3.91.2.3") is True


def test_datacenter_gcp():
    assert bc.is_datacenter_ip("35.184.10.20") is True


def test_datacenter_digitalocean():
    assert bc.is_datacenter_ip("164.90.1.2") is True


def test_datacenter_hetzner():
    assert bc.is_datacenter_ip("65.108.5.5") is True


def test_datacenter_residential_br_false():
    # faixa residencial brasileira (Vivo/Claro) — não é datacenter
    assert bc.is_datacenter_ip("189.28.100.42") is False


def test_datacenter_invalid_ip_false():
    assert bc.is_datacenter_ip("not-an-ip") is False
    assert bc.is_datacenter_ip("") is False


def test_datacenter_ip_is_bot_with_reason():
    is_bot, reason = bc.classify_bot("52.1.2.3", "Mozilla/5.0", "US", "/")
    assert is_bot is True and reason == "datacenter_ip"


def test_our_ip_never_bot():
    # nosso IP estático (KL-77) — self-scan/healthcheck NUNCA é bot, mesmo de datacenter GCP
    assert bc.classify_bot("34.135.194.208", "curl/7.0", "US", "/site/x.com") == (False, None)


def test_our_ip_env_override(monkeypatch):
    monkeypatch.setenv("KLARIM_OWN_IPS", "203.0.113.7, 203.0.113.8")
    assert bc.classify_bot("203.0.113.7", "python-requests", "US", "/") == (False, None)


def test_crawler_googlebot():
    assert bc.classify_bot("177.1.2.3", "Googlebot/2.1", "US", "/") == (True, "crawler_ua")


def test_crawler_tools():
    for ua in ("python-requests/2.31", "curl/8.1", "Go-http-client/1.1", "facebookexternalhit/1.1"):
        is_bot, reason = bc.classify_bot("177.9.9.9", ua, "BR", "/")
        assert is_bot is True and reason == "crawler_ua", ua


def test_crawler_empty_ua_false():
    assert bc.is_crawler_ua("") is False
    assert bc.is_crawler_ua(None) is False


def test_high_rate_bot():
    is_bot, reason = bc.classify_bot("177.1.2.3", "Mozilla", "BR", "/", request_count_last_hour=51)
    assert is_bot is True and reason == "high_rate"


def test_high_rate_with_user_not_bot():
    # conta logada → nunca bot, mesmo com rate alto (usuário real navegando)
    assert bc.classify_bot("177.1.2.3", "Mozilla", "BR", "/",
                           request_count_last_hour=200, user_id=19) == (False, None)


def test_prefetch_pattern_bot():
    is_bot, reason = bc.classify_bot("198.51.100.5", "Mozilla", "US", "/site/hotel.com.br",
                                     request_count_last_hour=1, has_other_requests=False)
    assert is_bot is True and reason == "prefetch_pattern"


def test_prefetch_with_other_requests_not_bot():
    # o mesmo IP já navegou antes (has_other_requests) → não é pre-fetch isolado
    assert bc.classify_bot("198.51.100.5", "Mozilla", "US", "/site/hotel.com.br",
                           request_count_last_hour=3, has_other_requests=True) == (False, None)


def test_br_normal_visitor_not_bot():
    assert bc.classify_bot("189.28.100.42", "Mozilla/5.0", "BR", "/scan",
                           request_count_last_hour=4, has_other_requests=True) == (False, None)


def test_authenticated_user_from_datacenter_not_bot():
    # dev/cliente logado atrás de nuvem/VPN — autenticação prova humanidade
    assert bc.classify_bot("52.1.2.3", "Mozilla", "US", "/account/dashboard-summary",
                           user_id=7) == (False, None)


def test_empty_ip_not_bot():
    assert bc.classify_bot("", "Mozilla", "BR", "/") == (False, None)
    assert bc.classify_bot("unknown", "Mozilla", "BR", "/") == (False, None)


def test_is_human_action():
    assert bc.is_human_action("GET", "/scan/result") is True
    assert bc.is_human_action("POST", "/account/signup") is True
    assert bc.is_human_action("POST", "/events") is True
    assert bc.is_human_action("GET", "/site/hotel.com.br") is False
    assert bc.is_human_action("GET", "/") is False


# =========================================================================== #
# 2. Middleware — helpers puros (skip, extração, máscara)
# =========================================================================== #

@pytest.mark.parametrize("path", [
    "/_astro/chunk.js", "/assets/logo.png", "/favicon.ico", "/robots.txt",
    "/sitemap.xml", "/track.js", "/fonts/inter.woff2", "/theme.js",
    "/style.css", "/img/hero.webp", "/.well-known/acme",
])
def test_should_log_assets_skipped(path):
    assert alm.should_log(path) is False


@pytest.mark.parametrize("path", ["/scan", "/scan/result", "/site/x.com", "/account/signup", "/events"])
def test_should_log_real_paths(path):
    assert alm.should_log(path) is True


def test_should_log_empty_false():
    assert alm.should_log("") is False


def test_extract_domain_site():
    assert alm.extract_domain("/site/hotel.com.br") == "hotel.com.br"
    assert alm.extract_domain("/site/www.hotel.com.br/x") == "hotel.com.br"


def test_extract_domain_scan_url():
    assert alm.extract_domain("/scan", "https://www.clinica.com.br/x") == "clinica.com.br"
    assert alm.extract_domain("/scan/result", "escola.com.br") == "escola.com.br"


def test_extract_domain_setor_is_none():
    # /setor/{slug} é slug, não domínio → None
    assert alm.extract_domain("/setor/hotelaria") is None


def test_extract_domain_state_override():
    # handler resolveu o domínio (POST body) e colocou no request.state
    assert alm.extract_domain("/scan/result", None, "loja.com.br") == "loja.com.br"


def test_extract_domain_other_none():
    assert alm.extract_domain("/account/login") is None
    assert alm.extract_domain("/") is None


def test_extract_domain_rejects_invalid():
    assert alm.extract_domain("/site/nodot") is None
    assert alm.extract_domain("/scan", "https://bad host!/x") is None


def test_extract_ip_cf_connecting():
    req = _req("/", headers={"CF-Connecting-IP": "189.28.100.42", "X-Real-IP": "10.0.0.1"})
    assert alm.extract_ip(req) == "189.28.100.42"


def test_extract_ip_xreal_fallback():
    req = _req("/", headers={"X-Real-IP": "177.1.2.3, 10.0.0.1"})
    assert alm.extract_ip(req) == "177.1.2.3"


def test_extract_ip_client_fallback():
    req = _req("/", client=("203.0.113.9", 5555))
    assert alm.extract_ip(req) == "203.0.113.9"


def test_mask_ip_one_octet():
    assert alm.mask_ip("189.28.100.42", 1) == "189.x.x.x"


def test_mask_ip_two_octets():
    assert alm.mask_ip("189.28.100.42", 2) == "189.28.x.x"


def test_mask_ip_ipv6():
    assert alm.mask_ip("2804:14c:1:2::3", 2).endswith("::x")


def test_mask_ip_invalid_or_empty():
    assert alm.mask_ip("") == ""
    assert alm.mask_ip(None) == ""


# =========================================================================== #
# 3. Buffer + flush + processamento em background
# =========================================================================== #

@pytest.fixture(autouse=True)
def _clear_buffer():
    alm._BUFFER.clear()
    yield
    alm._BUFFER.clear()


class _FakeAccessStore:
    def __init__(self):
        self.batches = []
        self.human_marks = []
        self.fail = False

    async def log_access_batch(self, records):
        if self.fail:
            raise RuntimeError("db down")
        self.batches.append(list(records))
        return len(records)

    async def mark_ip_human_today(self, ip):
        self.human_marks.append(ip)
        return 1


def test_enqueue_adds_to_buffer():
    alm._enqueue({"ip_address": "1.2.3.4"})
    assert len(alm._BUFFER) == 1


@pytest.mark.asyncio
async def test_flush_calls_store_and_clears(monkeypatch):
    store = _FakeAccessStore()
    monkeypatch.setattr(alm, "get_target_store", lambda: store)
    alm._enqueue({"ip_address": "1.1.1.1", "endpoint": "/"})
    alm._enqueue({"ip_address": "2.2.2.2", "endpoint": "/scan"})
    n = await alm.flush_access_log()
    assert n == 2 and len(store.batches[0]) == 2
    assert alm._BUFFER == []   # buffer drenado


@pytest.mark.asyncio
async def test_flush_empty_returns_zero(monkeypatch):
    store = _FakeAccessStore()
    monkeypatch.setattr(alm, "get_target_store", lambda: store)
    assert await alm.flush_access_log() == 0
    assert store.batches == []


@pytest.mark.asyncio
async def test_flush_swallows_db_error(monkeypatch):
    store = _FakeAccessStore()
    store.fail = True
    monkeypatch.setattr(alm, "get_target_store", lambda: store)
    alm._enqueue({"ip_address": "1.1.1.1"})
    n = await alm.flush_access_log()          # não levanta
    assert n == 0 and alm._BUFFER == []       # lote descartado (sem loop infinito)


@pytest.mark.asyncio
async def test_process_access_classifies_and_enqueues(monkeypatch):
    store = _FakeAccessStore()
    monkeypatch.setattr(alm, "get_target_store", lambda: store)
    ctx = {"ip_address": "52.1.2.3", "user_agent": "Mozilla", "country_code": "US",
           "endpoint": "/", "http_method": "GET", "user_id": None}
    await alm._process_access(ctx)
    assert len(alm._BUFFER) == 1
    rec = alm._BUFFER[0]
    assert rec["is_bot"] is True and rec["bot_reason"] == "datacenter_ip"
    assert store.human_marks == []            # não foi ação humana


@pytest.mark.asyncio
async def test_process_access_skips_invalid_ip(monkeypatch):
    # ip_address é INET NOT NULL — um IP inválido quebraria o batch → NÃO é logado
    store = _FakeAccessStore()
    monkeypatch.setattr(alm, "get_target_store", lambda: store)
    ctx = {"ip_address": "unknown", "user_agent": "Mozilla", "country_code": "US",
           "endpoint": "/", "http_method": "GET", "user_id": None}
    await alm._process_access(ctx)
    assert alm._BUFFER == []


def test_is_valid_ip():
    assert alm.is_valid_ip("189.28.100.42") is True
    assert alm.is_valid_ip("2804:14c::1") is True
    assert alm.is_valid_ip("unknown") is False
    assert alm.is_valid_ip("") is False
    assert alm.is_valid_ip(None) is False


@pytest.mark.asyncio
async def test_process_access_human_action_marks_retroactive(monkeypatch):
    store = _FakeAccessStore()
    monkeypatch.setattr(alm, "get_target_store", lambda: store)
    # IP de datacenter MAS fazendo um scan (ação humana) → não-bot + retroatividade
    ctx = {"ip_address": "52.1.2.3", "user_agent": "Mozilla", "country_code": "US",
           "endpoint": "/scan/result", "http_method": "GET", "user_id": None}
    await alm._process_access(ctx)
    rec = alm._BUFFER[0]
    assert rec["is_bot"] is False and rec["bot_reason"] is None
    assert store.human_marks == ["52.1.2.3"]


# =========================================================================== #
# 4. Middleware — captura + integração (não quebra o response)
# =========================================================================== #

def _req(path, method="GET", headers=None, query="", client=("9.9.9.9", 1234)):
    hlist = [(k.lower().encode(), str(v).encode()) for k, v in (headers or {}).items()]
    qs = query.encode() if isinstance(query, str) else query
    scope = {"type": "http", "method": method, "path": path, "raw_path": path.encode(),
             "query_string": qs, "headers": hlist, "client": client, "scheme": "https",
             "server": ("testserver", 443)}
    return Request(scope)


class _Resp:
    status_code = 200


def test_capture_extracts_fields():
    req = _req("/scan", headers={"CF-Connecting-IP": "189.28.100.42", "CF-IPCountry": "br",
                                 "User-Agent": "Mozilla", "Referer": "https://google.com"},
               query="url=https://hotel.com.br")
    ctx = alm._capture(req, _Resp(), "/scan", 42)
    assert ctx["ip_address"] == "189.28.100.42"
    assert ctx["country_code"] == "BR"           # uppercased
    assert ctx["domain_queried"] == "hotel.com.br"
    assert ctx["http_status"] == 200
    assert ctx["response_time_ms"] == 42
    assert ctx["http_method"] == "GET"


def test_capture_user_id_none_for_anonymous():
    ctx = alm._capture(_req("/"), _Resp(), "/", 1)
    assert ctx["user_id"] is None


def test_middleware_logs_nonstatic_request(monkeypatch):
    called = {"v": False}

    def fake_spawn(coro):
        called["v"] = True
        coro.close()

    monkeypatch.setattr(alm, "_spawn", fake_spawn)
    c = TestClient(m.app, raise_server_exceptions=False)
    r = c.get("/")
    assert r.status_code == 200
    assert called["v"] is True    # middleware agendou a gravação


def test_middleware_skips_static_asset(monkeypatch):
    called = {"v": False}
    monkeypatch.setattr(alm, "_spawn", lambda coro: (called.__setitem__("v", True), coro.close()))
    c = TestClient(m.app, raise_server_exceptions=False)
    c.get("/favicon.ico")
    assert called["v"] is False   # asset estático não é logado


def test_middleware_failsafe_capture_raises(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("boom")

    monkeypatch.setattr(alm, "_capture", boom)
    monkeypatch.setattr(alm, "_spawn", lambda coro: coro.close())
    c = TestClient(m.app, raise_server_exceptions=False)
    r = c.get("/")
    assert r.status_code == 200   # erro no log NÃO afeta o response


# =========================================================================== #
# 5. Endpoints admin — server-metrics / ip-behavior / ip-detail
# =========================================================================== #

class _FakeStore:
    async def al_server_metrics(self, start, end):
        return {"visitors_br": 80, "visitors_total": 120, "bots_filtered": 340,
                "scans": 61, "accounts": 5, "pdfs": 3, "alert_clicks_br": 12,
                "profiles_viewed_br": 45, "unique_domains_queried": 38,
                "top_countries": [{"code": "BR", "count": 80}, {"code": "US", "count": 25}],
                "top_endpoints": [{"endpoint": "/site/x", "count": 245}],
                "hourly": {0: 5, 15: 30}}

    async def al_ip_behavior(self, start, end):
        return {"multi_site_visitors": 15, "returning_visitors": 8,
                "avg_sites_per_visitor": 1.4,
                "top_multi_site_ips": [{"ip": "189.28.100.42", "country": "BR", "sites": 5,
                                        "domains": ["hotel.com.br", "clinica.com.br"]}],
                "top_returning_ips": [{"ip": "177.1.2.3", "country": "BR",
                                       "days_active": 4, "total_requests": 32}]}

    async def al_ip_detail(self, ip, timeline_limit=100):
        if ip == "8.8.8.8":
            return None
        return {"ip": ip, "country": "BR", "first_seen": NOW, "last_seen": NOW,
                "days_active": 3, "total_requests": 24,
                "domains_queried": ["hotel.com.br"], "bot_reasons": [], "user_id": 19,
                "is_bot": False,
                "timeline": [{"at": NOW, "endpoint": "/scan/result", "method": "POST",
                              "status": 200, "domain": "hotel.com.br"}]}

    # --- KL-92 P2 ---
    async def al_server_funnel(self, start, end):
        return {"visitors_br": 80, "viewed_profile": 45, "started_scan": 12,
                "completed_scan": 10, "created_account": 5, "downloaded_pdf": 3}

    async def al_top_domains(self, start, end, limit=20):
        return [{"domain": "hotel.com.br", "views": 12, "unique_ips": 8, "scans": 2},
                {"domain": "clinica.com.br", "views": 8, "unique_ips": 6, "scans": 1}]

    async def al_daily_series(self, start, end):
        return [{"day": NOW.date().isoformat(), "visitors_br": 52, "scans": 8, "accounts": 1}]

    async def al_hourly_heatmap(self, start, end):
        return [{"dow": 1, "hour": 15, "count": 30}, {"dow": 3, "hour": 9, "count": 12}]

    async def al_pre_signup_journeys(self, start, end, limit=2000):
        return [
            {"ip_address": "189.28.100.42", "endpoint": "/site/hotel.com.br",
             "domain_queried": "hotel.com.br", "referrer": None, "user_id": None,
             "created_at": NOW, "minutes_relative": -45},
            {"ip_address": "189.28.100.42", "endpoint": "/account/signup",
             "domain_queried": None, "referrer": None, "user_id": None,
             "created_at": NOW, "minutes_relative": 0},
            {"ip_address": "189.28.100.42", "endpoint": "/account/dashboard",
             "domain_queried": None, "referrer": None, "user_id": 19,
             "created_at": NOW, "minutes_relative": 2},
        ]

    async def al_retention(self, start, end):
        return {"total": 5, "day_1": 3, "day_3": 2, "day_7": 1}


@pytest.fixture
def store(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "k" * 64)
    monkeypatch.setenv("ADMIN_USER", "op")
    s = _FakeStore()
    monkeypatch.setattr("discovery.store.get_target_store", lambda: s)
    monkeypatch.setattr(aa, "get_target_store", lambda: s)
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


def test_server_metrics_requires_admin(client):
    assert client.get("/admin/analytics/server-metrics?period=7d").status_code == 401


def test_server_metrics_shape(client):
    j = client.get("/admin/analytics/server-metrics?period=7d", headers=_admin()).json()
    assert j["visitors_br"] == 80 and j["visitors_total"] == 120
    assert j["bots_filtered"] == 340 and j["scans"] == 61
    assert j["top_countries"][0]["code"] == "BR"
    assert j["period"]["days"] == 7


def test_server_metrics_hourly_dense_24(client):
    j = client.get("/admin/analytics/server-metrics?period=7d", headers=_admin()).json()
    hd = j["hourly_distribution"]
    assert len(hd) == 24
    assert hd[0] == {"hour": 0, "count": 5}
    assert hd[15] == {"hour": 15, "count": 30}
    assert hd[3] == {"hour": 3, "count": 0}   # horas sem tráfego preenchidas com 0


def test_ip_behavior_requires_admin(client):
    assert client.get("/admin/analytics/ip-behavior?period=7d").status_code == 401


def test_ip_behavior_masks_ips(client):
    j = client.get("/admin/analytics/ip-behavior?period=7d", headers=_admin()).json()
    assert j["multi_site_visitors"] == 15 and j["returning_visitors"] == 8
    top = j["top_multi_site_ips"][0]
    assert top["ip_masked"] == "189.x.x.x"      # LGPD: IP mascarado
    assert "ip" not in top                       # IP completo NUNCA sai
    assert top["sites"] == 5
    assert j["top_returning_ips"][0]["ip_masked"] == "177.x.x.x"


def test_ip_detail_requires_admin(client):
    assert client.get("/admin/analytics/ip-detail?ip=189.28.100.42").status_code == 401


def test_ip_detail_shape_and_mask(client):
    j = client.get("/admin/analytics/ip-detail?ip=189.28.100.42", headers=_admin()).json()
    assert j["found"] is True
    assert j["ip"] == "189.28.x.x"               # LGPD: 2 octetos no response
    assert j["total_requests"] == 24 and j["days_active"] == 3
    assert j["timeline"][0]["endpoint"] == "/scan/result"
    assert isinstance(j["first_seen"], str)      # datetime serializado p/ ISO


def test_ip_detail_not_found(client):
    j = client.get("/admin/analytics/ip-detail?ip=8.8.8.8", headers=_admin()).json()
    assert j["found"] is False and j["timeline"] == []


def test_ip_detail_invalid_ip_422(client):
    assert client.get("/admin/analytics/ip-detail?ip=not-an-ip", headers=_admin()).status_code == 422


def test_ip_detail_missing_ip_422(client):
    assert client.get("/admin/analytics/ip-detail", headers=_admin()).status_code == 422


# =========================================================================== #
# 6. Derivação pura (assemble) + LGPD
# =========================================================================== #

def test_assemble_server_metrics_hourly_expansion():
    raw = {"visitors_br": 10, "hourly": {2: 4, 20: 9}}
    out = aa.assemble_server_metrics(raw, {"days": 7})
    assert len(out["hourly_distribution"]) == 24
    assert out["hourly_distribution"][2]["count"] == 4
    assert out["hourly_distribution"][20]["count"] == 9
    assert out["visitors_br"] == 10


def test_assemble_ip_behavior_masks():
    raw = {"multi_site_visitors": 1, "returning_visitors": 0, "avg_sites_per_visitor": 2.0,
           "top_multi_site_ips": [{"ip": "45.10.20.30", "country": "BR", "sites": 3, "domains": []}],
           "top_returning_ips": []}
    out = aa.assemble_ip_behavior(raw)
    assert out["top_multi_site_ips"][0]["ip_masked"] == "45.x.x.x"
    assert "ip" not in out["top_multi_site_ips"][0]


def test_valid_ip_helper():
    assert aa._valid_ip("189.28.100.42") == "189.28.100.42"
    assert aa._valid_ip("garbage") is None
    assert aa._valid_ip(None) is None


def test_store_has_lgpd_and_batch_methods():
    # contrato: métodos existem (SQL validado na VM). Anonimização = LGPD retenção 90d.
    from discovery.store import TargetStore
    for name in ("log_access_batch", "mark_ip_human_today", "anonymize_old_access_logs",
                 "al_server_metrics", "al_ip_behavior", "al_ip_detail",
                 "al_server_funnel", "al_top_domains", "al_daily_series",
                 "al_hourly_heatmap", "al_pre_signup_journeys", "al_retention"):
        assert callable(getattr(TargetStore, name)), name


# =========================================================================== #
# 7. Prompt 2 — comportamento (funil / série / jornada / retenção / heatmap)
# =========================================================================== #

def test_assemble_server_funnel_rates():
    out = aa.assemble_server_funnel({"visitors_br": 80, "viewed_profile": 45,
                                     "started_scan": 12, "completed_scan": 10,
                                     "created_account": 5, "downloaded_pdf": 3})
    r = out["conversion_rates"]
    assert r["visit_to_profile"] == 56.2       # 45/80 (round bancário do Python)
    assert r["profile_to_scan"] == 26.7        # 12/45
    assert r["scan_to_account"] == 50.0        # 5/10 (usa completed_scan)
    assert r["account_to_pdf"] == 60.0         # 3/5
    assert r["overall"] == 6.2                 # 5/80


def test_assemble_server_funnel_zero_safe():
    out = aa.assemble_server_funnel({})
    assert out["conversion_rates"]["overall"] is None   # div/0 → None, não erro
    assert out["visitors_br"] == 0


def test_assemble_retention_pct():
    out = aa.assemble_retention({"total": 5, "day_1": 3, "day_3": 2, "day_7": 1})
    assert out["day_1"] == {"returned": 3, "total": 5, "pct": 60.0}
    assert out["day_7"]["pct"] == 20.0


def test_assemble_retention_zero_total():
    out = aa.assemble_retention({"total": 0, "day_1": 0, "day_3": 0, "day_7": 0})
    assert out["day_1"]["pct"] == 0.0          # sem signups → 0, não erro


def test_assemble_daily_series_densifies():
    rows = [{"day": "2026-07-16", "visitors_br": 52, "scans": 8, "accounts": 1}]
    out = aa.assemble_daily_series(rows, ["2026-07-15", "2026-07-16", "2026-07-17"])
    assert out["dates"] == ["2026-07-15", "2026-07-16", "2026-07-17"]
    assert out["visitors_br"] == [0, 52, 0]    # dias sem dado → 0
    assert out["scans"] == [0, 8, 0]
    assert len(out["accounts"]) == 3


def test_assemble_pre_signup_journeys_groups_and_typical():
    rows = [
        {"ip_address": "1.1.1.1", "endpoint": "/site/a.com", "domain_queried": "a.com",
         "referrer": None, "user_id": None, "created_at": None, "minutes_relative": -30},
        {"ip_address": "1.1.1.1", "endpoint": "/account/signup", "domain_queried": None,
         "referrer": None, "user_id": None, "created_at": None, "minutes_relative": 0},
        {"ip_address": "1.1.1.1", "endpoint": "/account/dashboard", "domain_queried": None,
         "referrer": None, "user_id": 7, "created_at": None, "minutes_relative": 3},
    ]
    out = aa.assemble_pre_signup_journeys(rows)
    j = out["pre_signup_journey"][0]
    assert j["user_id"] == 7                    # user_id recolhido do pós-signup
    assert j["steps_before"][0]["minutes_before"] == -30
    assert len(j["steps_after"]) == 1
    t = out["typical_journey"]
    assert t["most_common_first_action"] == "/site/a.com"
    assert t["avg_minutes_to_signup"] == 30.0
    assert t["pct_via_organic"] == 100.0


def test_assemble_pre_signup_journeys_empty():
    out = aa.assemble_pre_signup_journeys([])
    assert out["pre_signup_journey"] == []
    assert out["typical_journey"]["most_common_first_action"] is None


def test_assemble_pre_signup_journeys_via_alert():
    rows = [
        {"ip_address": "2.2.2.2", "endpoint": "/alert-access", "domain_queried": None,
         "referrer": None, "user_id": None, "created_at": None, "minutes_relative": -5},
        {"ip_address": "2.2.2.2", "endpoint": "/account/signup", "domain_queried": None,
         "referrer": None, "user_id": None, "created_at": None, "minutes_relative": 0},
    ]
    out = aa.assemble_pre_signup_journeys(rows)
    assert out["pre_signup_journey"][0]["via_alert"] is True
    assert out["typical_journey"]["pct_via_alert"] == 100.0


def test_assemble_hourly_heatmap_grid():
    out = aa.assemble_hourly_heatmap([{"dow": 1, "hour": 15, "count": 30},
                                      {"dow": 6, "hour": 23, "count": 5}])
    assert len(out["grid"]) == 7 and len(out["grid"][0]) == 24
    assert out["grid"][1][15] == 30
    assert out["grid"][6][23] == 5
    assert out["max"] == 30


def test_server_metrics_includes_p2_fields(client):
    j = client.get("/admin/analytics/server-metrics?period=7d", headers=_admin()).json()
    assert j["server_funnel"]["conversion_rates"]["overall"] == 6.2
    assert j["top_domains"][0]["domain"] == "hotel.com.br"
    assert "dates" in j["daily_series"] and "visitors_br" in j["daily_series"]
    assert len(j["hourly_heatmap"]["grid"]) == 7


def test_ip_behavior_includes_p2_fields(client):
    j = client.get("/admin/analytics/ip-behavior?period=7d", headers=_admin()).json()
    assert j["post_signup_retention"]["day_1"]["returned"] == 3
    assert j["typical_journey"]["most_common_first_action"] == "/site/hotel.com.br"
    assert j["pre_signup_journey"][0]["user_id"] == 19
