"""KL-85 Parte 1 — lead scoring de alertas. Testa a função PURA `calculate_alert_score`, a
integração no alert worker (filtra < threshold, grava score de todos, stats) e o endpoint
`/admin/analytics/alert-quality`. Offline (FakeStore)."""

from __future__ import annotations

import asyncio

import pytest

from discovery.alert_scoring import (
    calculate_alert_score, FREE_EMAIL_DOMAINS, ROLE_BASED_PREFIXES, HIGH_CLICK_SECTORS,
)


def _sig(result):
    return {s["signal"] for s in result["signals"]}


# =========================================================================== #
# 1. calculate_alert_score — função pura
# =========================================================================== #

def test_email_matches_domain_plus_30():
    r = calculate_alert_score({"domain": "hotel.com.br", "last_scan_score": None}, "joao@hotel.com.br")
    assert "email_matches_domain" in _sig(r)
    # own domain: +30 (match) +10 (corporate) = 40
    assert r["score"] == 40


def test_subdomain_match():
    r = calculate_alert_score({"domain": "loja.hotel.com.br", "last_scan_score": None}, "a@hotel.com.br")
    assert "email_matches_domain" in _sig(r)   # email domain é sufixo do site domain


def test_free_third_party_minus_20():
    r = calculate_alert_score({"domain": "hotel.com.br", "last_scan_score": None}, "zezinho@gmail.com")
    assert r["score"] == -20 and "email_mismatch_free" in _sig(r)
    assert "corporate_email" not in _sig(r)    # gmail é free → não corporativo


def test_role_based_minus_15():
    r = calculate_alert_score({"domain": "x.com.br", "last_scan_score": None}, "contato@x.com.br")
    # +30 (domain) +10 (corp) -15 (role) = 25
    assert r["score"] == 25 and "role_based_prefix" in _sig(r)


def test_score_action_zone_plus_20():
    r = calculate_alert_score({"domain": "x.com.br", "last_scan_score": 70}, "a@x.com.br")
    assert "score_action_zone" in _sig(r) and r["score"] == 60   # 30+10+20


def test_score_40_49_plus_10():
    r = calculate_alert_score({"domain": "x.com.br", "last_scan_score": 45}, "a@x.com.br")
    assert "score_high_urgency" in _sig(r) and r["score"] == 50   # 30+10+10


def test_score_over_85_plus_5():
    r = calculate_alert_score({"domain": "x.com.br", "last_scan_score": 95}, "a@x.com.br")
    assert "score_low_urgency" in _sig(r) and r["score"] == 45    # 30+10+5


def test_low_score_minus_10():
    r = calculate_alert_score({"domain": "x.com.br", "last_scan_score": 30}, "a@x.com.br")
    # 30+10-10 (abandoned, score<40) = 30
    assert "abandoned_or_low_score" in _sig(r) and r["score"] == 30


def test_descartado_minus_10():
    r = calculate_alert_score({"domain": "x.com.br", "last_scan_score": 70, "status": "descartado"}, "a@x.com.br")
    # 30+10+20-10 (descartado) = 50
    assert "abandoned_or_low_score" in _sig(r) and r["score"] == 50


def test_bounce_domain_minus_40():
    r = calculate_alert_score({"domain": "y.com.br", "last_scan_score": None}, "a@othercorp.com", domain_bounced=True)
    # +10 (corp) -40 (bounce) = -30
    assert "bounce_domain" in _sig(r) and r["score"] == -30


def test_combination_60():
    # e-mail corporativo no domínio com score 70 = 60 (exemplo do card)
    r = calculate_alert_score({"domain": "empresa.com.br", "last_scan_score": 70}, "diretor2@empresa.com.br")
    assert r["score"] == 60


def test_edge_no_at():
    r = calculate_alert_score({"domain": "z.com.br", "last_scan_score": None}, "semarroba")
    assert r["score"] == 0 and r["signals"] == []


def test_edge_empty_domain_target():
    r = calculate_alert_score({"domain": "", "last_scan_score": None}, "a@gmail.com")
    assert r["score"] == -20   # free, não bate domínio vazio


def test_edge_score_none_no_band():
    r = calculate_alert_score({"domain": "x.com.br", "last_scan_score": None}, "a@x.com.br")
    assert not any(s.startswith("score_") for s in _sig(r))
    assert r["score"] == 40    # 30+10, sem banda de score


def test_high_click_sector_empty_by_default():
    assert HIGH_CLICK_SECTORS == set()   # começa vazio (não inventa dados)
    r = calculate_alert_score({"domain": "x.com.br", "last_scan_score": None, "sector": "hotelaria"}, "a@x.com.br")
    assert "high_click_sector" not in _sig(r)


def test_constants_present():
    assert "gmail.com" in FREE_EMAIL_DOMAINS and "sac" in ROLE_BASED_PREFIXES


# =========================================================================== #
# 2. Integração no alert worker (_apply_alert_scoring)
# =========================================================================== #

class FakeStore:
    def __init__(self, bounce_domains=None):
        self.scores = {}
        self.bounce_domains = set(bounce_domains or [])

    async def update_target_alert_score(self, tid, score):
        self.scores[tid] = score

    async def domain_has_bounce(self, domain):
        return domain in self.bounce_domains


def _run(coro):
    # Loop isolado (não mexe no loop global): outros testes (TestClient) podem tê-lo fechado.
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _worker(monkeypatch, store):
    from discovery import alert_worker as aw
    monkeypatch.setattr(aw, "get_target_store", lambda: store)
    w = aw.AlertWorker()
    w.store = store
    w.alert_score_threshold = 20
    w._redis = False   # sem Redis nos testes → bounce cai direto no store
    return w


def test_worker_filters_below_threshold(monkeypatch):
    store = FakeStore()
    w = _worker(monkeypatch, store)
    targets = [
        {"id": 1, "domain": "hotel.com.br", "last_scan_score": 70, "contact_email": "dono@hotel.com.br"},  # 60 → passa
        {"id": 2, "domain": "hotel.com.br", "last_scan_score": None, "contact_email": "x@gmail.com"},       # -20 → filtra
    ]
    kept, skipped, avg = _run(w._apply_alert_scoring(targets))
    assert [t["id"] for t in kept] == [1] and skipped == 1
    assert avg == 60


def test_worker_writes_score_for_all_even_skipped(monkeypatch):
    store = FakeStore()
    w = _worker(monkeypatch, store)
    targets = [
        {"id": 1, "domain": "hotel.com.br", "last_scan_score": 70, "contact_email": "dono@hotel.com.br"},
        {"id": 2, "domain": "hotel.com.br", "last_scan_score": None, "contact_email": "x@gmail.com"},
    ]
    _run(w._apply_alert_scoring(targets))
    assert store.scores == {1: 60, 2: -20}   # gravou o score de TODOS, mesmo o filtrado


def test_worker_bounce_penalizes(monkeypatch):
    store = FakeStore(bounce_domains={"empresa.com.br"})
    w = _worker(monkeypatch, store)
    # e-mail corporativo de outro domínio (não-match) que bounçou → +10 -40 = -30 → filtra
    targets = [{"id": 5, "domain": "site.com.br", "last_scan_score": None, "contact_email": "a@empresa.com.br"}]
    kept, skipped, _ = _run(w._apply_alert_scoring(targets))
    assert kept == [] and skipped == 1 and store.scores[5] == -30


def test_worker_scoring_failsafe_keeps_target(monkeypatch):
    """Bug de scoring NÃO derruba o alvo (fail-safe: mantém)."""
    store = FakeStore()
    w = _worker(monkeypatch, store)
    from discovery import alert_worker as aw
    monkeypatch.setattr(aw, "calculate_alert_score", lambda *a, **k: (_ for _ in ()).throw(ValueError("boom")))
    targets = [{"id": 9, "domain": "x.com.br", "last_scan_score": 70, "contact_email": "a@x.com.br"}]
    kept, skipped, _ = _run(w._apply_alert_scoring(targets))
    assert [t["id"] for t in kept] == [9] and skipped == 0   # mantido apesar do erro


# =========================================================================== #
# 3. Endpoint /admin/analytics/alert-quality
# =========================================================================== #
import api.main as m  # noqa: E402
from api import auth_users  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


class EndpointStore:
    async def alert_quality_stats(self):
        return {"total_with_email": 7180, "total_scored": 7000,
                "distribution": {"[-40,-20)": 230, "[-20,0)": 850, "[0,20)": 1800,
                                 "[20,40)": 2100, "[40,60)": 1500, "[60,80)": 500, "[80,200)": 20},
                "qualified": 4120, "low": 1800, "disqualified": 1080, "avg_score": 22.5}

    async def alert_quality_sent_stats(self, start, end):
        return {"total_sent": 320, "scored_sent": 300, "avg_score_sent": 42,
                "high": 180, "medium": 100, "low": 20}


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "k" * 64)
    monkeypatch.setenv("ADMIN_USER", "op")
    s = EndpointStore()
    monkeypatch.setattr("discovery.store.get_target_store", lambda: s)
    import api.admin_analytics as aa
    monkeypatch.setattr(aa, "get_target_store", lambda: s)

    async def _none(k):
        return None
    monkeypatch.setattr(m, "_cache_get", _none)
    monkeypatch.setattr(m, "_cache_set", lambda k, v, ttl=300: _none(k))
    return TestClient(m.app, raise_server_exceptions=False)


def _admin():
    return {"Authorization": f"Bearer {m._create_token('op')}"}


def test_alert_quality_requires_admin(client):
    assert client.get("/admin/analytics/alert-quality?period=7d").status_code == 401


def test_alert_quality_endpoint(client):
    j = client.get("/admin/analytics/alert-quality?period=7d", headers=_admin()).json()
    assert j["total_evaluated"] == 7000
    assert j["total_sent"] == 320
    # filtered = low + disq = 1800 + 1080 = 2880
    assert j["total_filtered"] == 2880
    assert j["by_score_range"]["high_quality"]["count"] == 2020   # 1500+500+20
    assert j["by_score_range"]["medium_quality"]["count"] == 2100
    assert j["avg_score_sent"] == 42


def test_alert_quality_invalid_period(client):
    assert client.get("/admin/analytics/alert-quality?period=2y", headers=_admin()).status_code == 422
