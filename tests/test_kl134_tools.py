"""KL-134 (Prompt 1/2) — micro-ferramentas SEO: URL validator, rate limiter, timeout wrapper,
builders puros e os 6 endpoints (ssl/headers/lgpd/tech/email/stats). Offline: o I/O externo
(TLS/HTTP/DNS/store) é mockado; o Redis é um fake em memória."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

import api.main as m
from api import tools as t


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


@pytest.fixture
def client():
    return TestClient(m.app)


# =========================================================================== #
# Fake Redis (async) — só o que o rate limiter / cache usam.
# =========================================================================== #

class FakeRedis:
    def __init__(self):
        self.kv, self.ttls = {}, {}

    async def incr(self, k):
        self.kv[k] = int(self.kv.get(k, 0)) + 1
        return self.kv[k]

    async def expire(self, k, ttl):
        self.ttls[k] = int(ttl)
        return True

    async def ttl(self, k):
        return self.ttls.get(k, -1)

    async def get(self, k):
        return self.kv.get(k)

    async def set(self, k, v, nx=False, ex=None):
        if nx and k in self.kv:
            return False
        self.kv[k] = v
        if ex is not None:
            self.ttls[k] = int(ex)
        return True


# =========================================================================== #
# 1–4. URL validator
# =========================================================================== #

def test_validate_url_bare_domain_gets_https():
    assert t.validate_tool_url("example.com") == "https://example.com"


def test_validate_url_with_scheme_and_path_accepted():
    assert t.validate_tool_url("https://example.com/path") == "https://example.com/path"
    assert t.validate_tool_url("http://example.com") == "http://example.com"


def test_validate_url_empty_raises():
    with pytest.raises(ValueError):
        t.validate_tool_url("")


def test_validate_url_garbage_raises():
    for bad in ("not a url !!!", "   ", "http://", "sem-ponto"):
        with pytest.raises(ValueError):
            t.validate_tool_url(bad)


def test_validate_domain_accepts_url_and_bare():
    assert t.validate_tool_domain("https://example.com/x") == "example.com"
    assert t.validate_tool_domain("EXAMPLE.com") == "example.com"
    with pytest.raises(ValueError):
        t.validate_tool_domain("nope !!!")


# =========================================================================== #
# 5–6. Rate limiter
# =========================================================================== #

def test_rate_limiter_allows_10_blocks_11th():
    r = FakeRedis()
    for _ in range(TOOLS_OK := 10):
        assert _run(t.check_tools_rate_limit(r, "1.1.1.1")) is None
    retry = _run(t.check_tools_rate_limit(r, "1.1.1.1"))
    assert isinstance(retry, int) and retry > 0


def test_rate_limiter_fail_open_without_redis():
    assert _run(t.check_tools_rate_limit(None, "1.1.1.1")) is None


def test_endpoint_429_has_retry_after(client, monkeypatch):
    r = FakeRedis()
    monkeypatch.setattr(t, "_redis", lambda: r)

    async def _fake_tls(host, port=443):
        return {"ok": False}
    monkeypatch.setattr(t, "get_tls_info", _fake_tls)

    last = None
    for _ in range(11):
        last = client.get("/tools/ssl", params={"url": "example.com"})
    assert last.status_code == 429
    assert "retry-after" in {k.lower() for k in last.headers}


# =========================================================================== #
# run_check_with_timeout
# =========================================================================== #

def test_run_check_with_timeout_raises_tooltimeout():
    async def _slow():
        await asyncio.sleep(0.5)
        return "done"
    with pytest.raises(t.ToolTimeout):
        _run(t.run_check_with_timeout(_slow, timeout=0.01))


# =========================================================================== #
# 7. /tools/ssl
# =========================================================================== #

def _tls_info(days=87, verified=True, protocol="TLSv1.3", self_signed=False,
              issuer="R3", weak=None):
    not_after = datetime.now(timezone.utc) + timedelta(days=days)
    return {
        "ok": True, "verified": verified, "verify_error": None, "protocol": protocol,
        "cipher_name": "TLS_AES_256_GCM_SHA384", "weak_cipher": weak,
        "cert": {"issuer_cn": issuer, "subject_cn": "example.com", "self_signed": self_signed,
                 "not_after": not_after, "not_before": None, "san": [], "key": {}},
    }


def test_ssl_valid(client, monkeypatch):
    async def _fake(host, port=443):
        return _tls_info()
    monkeypatch.setattr(t, "get_tls_info", _fake)
    r = client.get("/tools/ssl", params={"url": "example.com"})
    assert r.status_code == 200
    d = r.json()
    assert d["domain"] == "example.com" and d["valid"] is True
    assert d["issuer"] == "Let's Encrypt" and d["protocol"] == "TLSv1.3"
    assert d["grade"] == "A" and 80 <= d["days_remaining"] <= 88
    assert isinstance(d["checks"], list) and len(d["checks"]) == 3
    assert d["context"]["source"]


def test_ssl_expired(client, monkeypatch):
    async def _fake(host, port=443):
        return _tls_info(days=-12, verified=False)
    monkeypatch.setattr(t, "get_tls_info", _fake)
    d = client.get("/tools/ssl", params={"url": "example.com"}).json()
    assert d["valid"] is False and "expirado" in d["error"].lower()
    assert any(c["status"] == "fail" for c in d["checks"])


def test_ssl_missing_param_400(client):
    assert client.get("/tools/ssl").status_code == 400


def test_ssl_invalid_url_400(client):
    r = client.get("/tools/ssl", params={"url": "not a url !!!"})
    assert r.status_code == 400 and "inválida" in r.json()["detail"].lower()


def test_ssl_timeout_504(client, monkeypatch):
    async def _fake(host, port=443):
        raise t.ToolTimeout("O site não respondeu em 15 segundos.")
    monkeypatch.setattr(t, "get_tls_info", _fake)
    r = client.get("/tools/ssl", params={"url": "example.com"})
    assert r.status_code == 504 and "15 segundos" in r.json()["detail"]


# =========================================================================== #
# 8. /tools/headers
# =========================================================================== #

class _Resp:
    def __init__(self, headers):
        self.headers = headers


def test_headers_ok(client, monkeypatch):
    async def _fetch(url, method="GET", **kw):
        return _Resp({"Strict-Transport-Security": "max-age=31536000",
                      "X-Content-Type-Options": "nosniff",
                      "X-Frame-Options": "DENY"})
    monkeypatch.setattr(t._base, "fetch", _fetch)
    d = client.get("/tools/headers", params={"url": "example.com"}).json()
    assert d["domain"] == "example.com"
    # KL-164: X-XSS-Protection é informativo e NÃO pontua → denominador 6 (não 7).
    assert d["score"] == "3/6"
    hsts = next(h for h in d["headers"] if h["name"] == "Strict-Transport-Security")
    assert hsts["present"] is True and hsts["value"].startswith("max-age")
    csp = next(h for h in d["headers"] if h["name"] == "Content-Security-Policy")
    assert csp["present"] is False and csp["importance"] == "alta" and csp["explanation"]
    xxp = next(h for h in d["headers"] if h["name"] == "X-XSS-Protection")
    assert xxp.get("informational") is True and xxp["importance"] == "informativo"


def test_headers_connection_error_502(client, monkeypatch):
    async def _fetch(url, method="GET", **kw):
        raise RuntimeError("boom")
    monkeypatch.setattr(t._base, "fetch", _fetch)
    assert client.get("/tools/headers", params={"url": "example.com"}).status_code == 502


# =========================================================================== #
# 9. /tools/lgpd
# =========================================================================== #

def test_lgpd_ok(client, monkeypatch):
    fake = {
        "score": 3, "total": 8, "disclaimer": "diagnóstico técnico...",
        "checks": [
            {"id": "privacy_policy", "name": "Política de Privacidade", "status": "PASS",
             "evidence": "Link encontrado: /privacidade"},
            {"id": "cookie_consent", "name": "Banner de Cookies", "status": "FAIL",
             "evidence": "Nenhum banner detectado"},
            {"id": "cookie_policy", "name": "Política de Cookies", "status": "FAIL",
             "evidence": "Sem política de cookies"},
            {"id": "dpo_info", "name": "Identificação do Encarregado (DPO)", "status": "FAIL",
             "evidence": "Sem menção ao DPO"},
            {"id": "dsar_channel", "name": "Canal de direitos do titular", "status": "FAIL",
             "evidence": "Sem canal"},
            {"id": "https_forms", "name": "HTTPS em formulários", "status": "PASS",
             "evidence": "HTTPS ok"},
            {"id": "form_security_headers", "name": "Headers de segurança em formulários",
             "status": "PASS", "evidence": "2/3 headers"},
            {"id": "third_party_cookies", "name": "Cookies de terceiros pré-consentimento",
             "status": "FAIL", "evidence": "cookies de rastreio"},
        ],
    }

    async def _fake(url):
        return fake
    monkeypatch.setattr(t._privacy, "scan_privacy", _fake)
    d = client.get("/tools/lgpd", params={"url": "example.com"}).json()
    assert d["score"] == "3/8" and d["grade"] == "Atenção necessária"
    assert len(d["indicators"]) == 8
    first = d["indicators"][0]
    assert first["status"] == "pass" and first["explanation"]
    assert isinstance(d["context"]["stats"], list) and len(d["context"]["stats"]) == 3


def test_lgpd_unreachable_502(client, monkeypatch):
    async def _fake(url):
        return None
    monkeypatch.setattr(t._privacy, "scan_privacy", _fake)
    assert client.get("/tools/lgpd", params={"url": "example.com"}).status_code == 502


# =========================================================================== #
# 10. /tools/tech
# =========================================================================== #

def test_tech_ok(client, monkeypatch):
    async def _fake_io(norm, host):
        return {"technologies": [
            {"name": "wordpress", "category": "cms", "subcategory": "platform", "version": "6.4"},
            {"name": "cloudflare_cdn", "category": "cdn", "subcategory": None, "version": None},
            {"name": "php", "category": "hosting", "subcategory": "backend", "version": "8.2"},
        ]}
    monkeypatch.setattr(t, "_tech_io", _fake_io)
    d = client.get("/tools/tech", params={"url": "example.com"}).json()
    names = {x["name"]: x for x in d["technologies"]}
    assert names["WordPress"]["category"] == "CMS" and names["WordPress"]["version"] == "6.4"
    assert names["Cloudflare"]["category"] == "CDN" and "version" not in names["Cloudflare"]
    assert names["PHP"]["category"] == "Linguagem"
    assert isinstance(d["context"]["stats"], list)


def test_tech_empty_has_message(client, monkeypatch):
    async def _fake_io(norm, host):
        return {"technologies": []}
    monkeypatch.setattr(t, "_tech_io", _fake_io)
    d = client.get("/tools/tech", params={"url": "example.com"}).json()
    assert d["technologies"] == [] and "Nenhuma tecnologia" in d["message"]


# =========================================================================== #
# 11. /tools/email
# =========================================================================== #

def test_email_ok(client, monkeypatch):
    async def _fake_io(domain):
        txt = ["v=spf1 include:_spf.google.com ~all"]
        selector = "google"
        dmarc = []  # sem DMARC → fail
        mx = ["aspmx.l.google.com", "alt1.aspmx.l.google.com"]
        return txt, selector, dmarc, mx
    monkeypatch.setattr(t, "_email_io", _fake_io)
    d = client.get("/tools/email", params={"domain": "example.com"}).json()
    assert d["domain"] == "example.com" and d["score"] == "3/4"
    by = {r["name"]: r for r in d["records"]}
    assert by["SPF"]["status"] == "pass"
    assert by["DKIM"]["status"] == "pass" and by["DKIM"]["detail"].startswith("Seletor")
    assert by["DMARC"]["status"] == "fail" and by["DMARC"]["recommendation"].startswith("Adicione")
    assert by["MX Records"]["status"] == "pass" and len(by["MX Records"]["value"]) == 2


def test_email_missing_param_400(client):
    assert client.get("/tools/email").status_code == 400


def test_email_all_present(client, monkeypatch):
    async def _fake_io(domain):
        return (["v=spf1 -all"], "resend",
                ["v=DMARC1; p=reject; rua=mailto:d@x.com"], ["mx.x.com"])
    monkeypatch.setattr(t, "_email_io", _fake_io)
    d = client.get("/tools/email", params={"domain": "example.com"}).json()
    assert d["score"] == "4/4"
    assert all(r["status"] == "pass" for r in d["records"])


# =========================================================================== #
# 12–13. builders puros (unit)
# =========================================================================== #

def test_build_email_multiple_dmarc_is_fail():
    resp = t.build_email_response(
        "x.com", ["v=spf1 ~all"], None,
        ["v=DMARC1; p=reject", "v=DMARC1; p=none"], ["mx.x.com"])
    by = {r["name"]: r for r in resp["records"]}
    assert by["DMARC"]["status"] == "fail" and by["DKIM"]["status"] == "fail"
    # SPF (~all) e MX passam; DKIM (ausente) e DMARC (múltiplo) falham → 2/4.
    assert resp["score"] == "2/4"


def test_build_ssl_self_signed_invalid():
    info = _tls_info(self_signed=True, verified=False)
    d = t.build_ssl_response("x.com", info)
    assert d["valid"] is False and "autoassinado" in d["error"].lower()


def test_friendly_issuer_maps_known_cas():
    assert t._friendly_issuer("R3") == "Let's Encrypt"
    assert t._friendly_issuer("E5") == "Let's Encrypt"
    assert t._friendly_issuer("WE1") == "Google Trust Services"  # GTS intermediate
    assert t._friendly_issuer("WR2") == "Google Trust Services"
    assert t._friendly_issuer("DigiCert Global G2") == "DigiCert"
    assert t._friendly_issuer("Some Unknown CA") == "Some Unknown CA"
    assert t._friendly_issuer(None) == "Desconhecido"


def test_lgpd_grade_bands():
    assert t._lgpd_grade(8, 8) == "Adequado"
    assert t._lgpd_grade(6, 8) == "Parcialmente adequado"
    assert t._lgpd_grade(3, 8) == "Atenção necessária"
    assert t._lgpd_grade(1, 8) == "Inadequado"


# =========================================================================== #
# 15–16. /tools/stats
# =========================================================================== #

class _FakeStore:
    def __init__(self):
        self.calls = 0

    async def dashboard_summary(self):
        self.calls += 1
        return {"targets": {"total": 115849}, "profiles": {"total": 67806},
                "scans": {"total": 60913}}

    async def privacy_indicator_stats(self):
        return {"scanned": 19846, "indicators": {
            "privacy_policy": {"pass": 5060, "fail": 14786},
            "cookie_consent": {"pass": 3260, "fail": 16586},
            "dsar_channel": {"pass": 182, "fail": 19664},
            "dpo_info": {"pass": 4485, "fail": 15361},
            "cookie_policy": {"pass": 655, "fail": 19191},
        }}

    async def get_tech_adoption(self, name, sector=None):
        table = {"wordpress": (11959, 59095), "cloudflare": (18210, 59095),
                 "google_analytics_4": (5256, 59095)}
        w, base = table[name]
        return {"total_sites": base, "sites_with_tech": w, "adoption_rate": round(w / base, 4)}


def test_stats_ok(client, monkeypatch):
    import discovery.store as ds
    store = _FakeStore()
    monkeypatch.setattr(ds, "get_target_store", lambda: store)
    monkeypatch.setattr(t, "_redis", lambda: None)  # sem cache
    d = client.get("/tools/stats").json()
    assert d["total_sites"] == 115849 and d["total_scans"] == 60913
    assert d["privacy"]["privacy_policy_fail_pct"] == 74.5
    assert d["privacy"]["dsar_fail_pct"] == 99.1
    assert d["tech"]["wordpress_pct"] == 20.2 and d["tech"]["cloudflare_pct"] == 30.8
    assert d["tech"]["tech_base"] == 59095 and "cached_at" in d


def test_stats_uses_cache(client, monkeypatch):
    r = FakeRedis()
    monkeypatch.setattr(t, "_redis", lambda: r)
    calls = {"n": 0}

    async def _compute():
        calls["n"] += 1
        return {"total_sites": 1, "cached_at": "2026-08-12T00:00:00+00:00"}
    monkeypatch.setattr(t, "_compute_stats", _compute)

    a = client.get("/tools/stats").json()
    b = client.get("/tools/stats").json()
    assert a == b and calls["n"] == 1  # 2º request veio do cache, sem recomputar
