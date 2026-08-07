"""KL-85 Parte 1 — lead scoring de alertas. Testa a função PURA `calculate_alert_score`, a
integração no alert worker (filtra < threshold, grava score de todos, stats) e o endpoint
`/admin/analytics/alert-quality`. Offline (FakeStore)."""

from __future__ import annotations

import asyncio

import pytest

from discovery.alert_scoring import (
    calculate_alert_score, FREE_EMAIL_DOMAINS, ROLE_BASED_PREFIXES, HIGH_CLICK_SECTORS,
    _email_type_factor,
)


def _sig(result):
    return {s["signal"] for s in result["signals"]}


# =========================================================================== #
# 0. KL-146 — fator de tipo de e-mail (pessoal vs genérico)
# =========================================================================== #

@pytest.mark.parametrize("email,expected", [
    ("joao@dominio.com.br", 15),          # pessoal
    ("maria.silva@dominio.com.br", 15),   # pessoal (nome composto)
    ("contato@dominio.com.br", -10),      # genérico high-bounce (66% dos bounces)
    ("atendimento@dominio.com.br", -5),   # genérico medium-bounce
    ("sac@dominio.com.br", -5),           # genérico medium-bounce
    ("comercial@dominio.com.br", 0),      # genérico neutro
    ("vendas@dominio.com.br", 0),         # genérico neutro
    ("info@dominio.com.br", 0),           # genérico neutro
    ("noreply@dominio.com.br", 0),        # genérico (union c/ ROLE_BASED_PREFIXES) — nunca +15
    ("financeiro@dominio.com.br", 0),     # idem
    ("CONTATO@Dominio.com", -10),         # case-insensitive
    ("", 0),                              # vazio
    (None, 0),                            # None
    ("semarroba", 0),                     # sem @
    ("@dominio.com", 0),                  # sem prefixo
])
def test_email_type_factor(email, expected):
    assert _email_type_factor(email) == expected


# =========================================================================== #
# 1. calculate_alert_score — função pura
# =========================================================================== #

def test_email_matches_domain_plus_30():
    r = calculate_alert_score({"domain": "hotel.com.br", "last_scan_score": None}, "joao@hotel.com.br")
    assert "email_matches_domain" in _sig(r)
    # own domain: +30 (match) +10 (corporate) +15 (pessoal, KL-146) = 55
    assert r["score"] == 55 and "email_type_personal" in _sig(r)


def test_subdomain_match():
    r = calculate_alert_score({"domain": "loja.hotel.com.br", "last_scan_score": None}, "a@hotel.com.br")
    assert "email_matches_domain" in _sig(r)   # email domain é sufixo do site domain


def test_free_third_party_no_penalty():
    # 2026-07-20: e-mail genérico que não casa NÃO penaliza mais (MISMATCH_FREE_PENALTY=0) — muitas
    # PMEs BR usam gmail como e-mail comercial. KL-146: `zezinho@` é pessoal → +15 (só ordena).
    r = calculate_alert_score({"domain": "hotel.com.br", "last_scan_score": None}, "zezinho@gmail.com")
    assert r["score"] == 15 and "email_mismatch_free" not in _sig(r)
    assert "corporate_email" not in _sig(r)    # gmail é free → não corporativo
    assert "email_type_personal" in _sig(r)    # KL-146: pessoal +15


def test_generic_high_bounce_minus_10():
    # KL-146: `contato@` (66% dos bounces, 8,7%) → -10 (era -5 role KL-136). +30 (domain) +10 (corp)
    # -10 (contato high-bounce) = 30. SUBSTITUI a penalidade role-based (não acumula).
    r = calculate_alert_score({"domain": "x.com.br", "last_scan_score": None}, "contato@x.com.br")
    assert r["score"] == 30 and "email_type_generic_high_bounce" in _sig(r)
    assert "role_based_prefix" not in _sig(r)   # KL-146: sinal antigo removido


def test_generic_high_bounce_action_zone_still_sent():
    # KL-146 (cenário do card): contato@ genérico (não casa domínio) na action_zone → 20. Continua
    # ENVIADO (KL-145: envio = sintaxe+MX+blocklist; o score só ORDENA, não filtra).
    r = calculate_alert_score({"domain": "hotel.com.br", "last_scan_score": 70}, "contato@x.com.br")
    # +10 (corp) +20 (action_zone) -10 (contato high-bounce) = 20
    assert r["score"] == 20 and "email_type_generic_high_bounce" in _sig(r)


def test_generic_neutral_zero():
    # KL-146: genérico neutro (`comercial@`) → 0 (nem bônus de pessoal, nem penalidade).
    r = calculate_alert_score({"domain": "x.com.br", "last_scan_score": 70}, "comercial@x.com.br")
    assert r["score"] == 60 and "email_type_generic" in _sig(r)   # 30+10+20+0


def test_generic_medium_bounce_minus_5():
    # KL-146: `atendimento@`/`sac@` (6-7% bounce) → -5.
    r = calculate_alert_score({"domain": "x.com.br", "last_scan_score": 70}, "sac@x.com.br")
    assert r["score"] == 55 and "email_type_generic_medium_bounce" in _sig(r)   # 30+10+20-5


def test_personal_ranks_above_generic_action_zone():
    # KL-146 (efeito na fila): pessoal na action_zone > genérico na action_zone (mesmo domínio/score).
    t = {"domain": "empresa.com.br", "last_scan_score": 70}
    pessoal = calculate_alert_score(t, "joao@empresa.com.br")["score"]        # 30+10+20+15 = 75
    neutro = calculate_alert_score(t, "comercial@empresa.com.br")["score"]    # 30+10+20+0  = 60
    contato = calculate_alert_score(t, "contato@empresa.com.br")["score"]     # 30+10+20-10 = 50
    assert pessoal > neutro > contato and (pessoal, neutro, contato) == (75, 60, 50)


def test_score_action_zone_plus_20():
    r = calculate_alert_score({"domain": "x.com.br", "last_scan_score": 70}, "a@x.com.br")
    assert "score_action_zone" in _sig(r) and r["score"] == 75   # 30+10+20+15 (pessoal)


def test_score_40_49_plus_10():
    r = calculate_alert_score({"domain": "x.com.br", "last_scan_score": 45}, "a@x.com.br")
    assert "score_high_urgency" in _sig(r) and r["score"] == 65   # 30+10+10+15


def test_score_over_85_plus_5():
    r = calculate_alert_score({"domain": "x.com.br", "last_scan_score": 95}, "a@x.com.br")
    assert "score_low_urgency" in _sig(r) and r["score"] == 60    # 30+10+5+15


def test_low_score_minus_10():
    r = calculate_alert_score({"domain": "x.com.br", "last_scan_score": 30}, "a@x.com.br")
    # 30+10-10 (abandoned, score<40) +15 (pessoal) = 45
    assert "abandoned_or_low_score" in _sig(r) and r["score"] == 45


def test_descartado_minus_10():
    r = calculate_alert_score({"domain": "x.com.br", "last_scan_score": 70, "status": "descartado"}, "a@x.com.br")
    # 30+10+20-10 (descartado) +15 (pessoal) = 65
    assert "abandoned_or_low_score" in _sig(r) and r["score"] == 65


def test_bounce_domain_minus_40():
    r = calculate_alert_score({"domain": "y.com.br", "last_scan_score": None}, "a@othercorp.com", domain_bounced=True)
    # +10 (corp) -40 (bounce) +15 (pessoal) = -15
    assert "bounce_domain" in _sig(r) and r["score"] == -15


def test_combination_75():
    # e-mail corporativo pessoal no domínio com score 70: 30+10+20+15 (pessoal, KL-146) = 75.
    r = calculate_alert_score({"domain": "empresa.com.br", "last_scan_score": 70}, "diretor2@empresa.com.br")
    assert r["score"] == 75


def test_edge_no_at():
    r = calculate_alert_score({"domain": "z.com.br", "last_scan_score": None}, "semarroba")
    assert r["score"] == 0 and r["signals"] == []


def test_edge_empty_domain_target():
    r = calculate_alert_score({"domain": "", "last_scan_score": None}, "a@gmail.com")
    # domínio vazio não casa; gmail free (sem corp). KL-146: `a@` pessoal → +15.
    assert r["score"] == 15


def test_edge_score_none_no_band():
    r = calculate_alert_score({"domain": "x.com.br", "last_scan_score": None}, "a@x.com.br")
    assert not any(s.startswith("score_") for s in _sig(r))
    assert r["score"] == 55    # 30+10+15 (pessoal), sem banda de score


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
    w._redis = False   # sem Redis nos testes → bounce cai direto no store
    return w


def test_worker_scores_all_no_filter(monkeypatch):
    # KL-137: o score NÃO filtra mais — ambos os alvos são MANTIDOS (o run_cycle só os ordena).
    store = FakeStore()
    w = _worker(monkeypatch, store)
    targets = [
        {"id": 1, "domain": "hotel.com.br", "last_scan_score": 70, "contact_email": "dono@hotel.com.br"},  # 60
        {"id": 2, "domain": "hotel.com.br", "last_scan_score": None, "contact_email": "x@gmail.com"},       # 0
    ]
    kept, avg = _run(w._apply_alert_scoring(targets))
    assert {t["id"] for t in kept} == {1, 2}   # nenhum filtrado
    assert avg == 45   # KL-146: (75 + 15) / 2 — pessoais ganham +15


def test_worker_writes_score_for_all(monkeypatch):
    store = FakeStore()
    w = _worker(monkeypatch, store)
    targets = [
        {"id": 1, "domain": "hotel.com.br", "last_scan_score": 70, "contact_email": "dono@hotel.com.br"},
        {"id": 2, "domain": "hotel.com.br", "last_scan_score": None, "contact_email": "x@gmail.com"},
    ]
    _run(w._apply_alert_scoring(targets))
    assert store.scores == {1: 75, 2: 15}   # KL-146: pessoais +15 (dono@ 75, x@gmail 15)


def test_worker_bounce_penalizes_score(monkeypatch):
    store = FakeStore(bounce_domains={"empresa.com.br"})
    w = _worker(monkeypatch, store)
    # e-mail corporativo pessoal de outro domínio (não-match) que bounçou → +10 -40 +15 = -15.
    targets = [{"id": 5, "domain": "site.com.br", "last_scan_score": None, "contact_email": "a@empresa.com.br"}]
    kept, _ = _run(w._apply_alert_scoring(targets))
    assert [t["id"] for t in kept] == [5] and store.scores[5] == -15   # KL-137: mantido (só ordena)


# --- Fix 2026-07-20: bounce por-domínio NÃO penaliza provedores genéricos ---------------------- #
def test_domain_bounced_free_provider_short_circuits(monkeypatch):
    # gmail.com TEM bounce no banco, mas é provedor genérico → curto-circuita p/ False (não lê
    # store/redis). Domínio corporativo com bounce → True (comportamento normal).
    store = FakeStore(bounce_domains={"gmail.com", "empresa.com.br"})
    w = _worker(monkeypatch, store)
    assert _run(w._domain_bounced("gmail.com", {})) is False
    assert _run(w._domain_bounced("empresa.com.br", {})) is True


def test_calc_score_bounce_ignored_for_free_domain():
    # e-mail genérico (gmail) + bounce → NÃO aplica -40 (um bounce em outro gmail é irrelevante).
    r = calculate_alert_score({"domain": "hotel.com.br", "last_scan_score": 70},
                              "zezinho@gmail.com", domain_bounced=True)
    assert "bounce_domain" not in _sig(r)


def test_calc_score_bounce_applies_for_corporate_domain():
    # domínio corporativo próprio + bounce → aplica -40 (servidor de e-mail da empresa com problema).
    r = calculate_alert_score({"domain": "hotel.com.br", "last_scan_score": 70},
                              "a@othercorp.com", domain_bounced=True)
    assert "bounce_domain" in _sig(r)


def test_worker_gmail_good_lead_scored(monkeypatch):
    # E2E dos fixes (2026-07-20): gmail (mismatch) score 70 + gmail COM bounce no banco →
    # (1) bounce não penaliza (provedor genérico) e (2) mismatch_free=0. KL-146: +15 (pessoal).
    store = FakeStore(bounce_domains={"gmail.com"})
    w = _worker(monkeypatch, store)
    targets = [{"id": 9, "domain": "hotel.com.br", "last_scan_score": 70, "contact_email": "x@gmail.com"}]
    kept, _ = _run(w._apply_alert_scoring(targets))
    # KL-146: x@gmail é pessoal → 0 (mismatch/free) +20 (action) +15 (pessoal) = 35.
    assert store.scores[9] == 35 and [t["id"] for t in kept] == [9]   # KL-137: mantido (score só ordena)


def test_worker_scoring_failsafe_keeps_target(monkeypatch):
    """Bug de scoring NÃO derruba o alvo (fail-safe: mantém)."""
    store = FakeStore()
    w = _worker(monkeypatch, store)
    from discovery import alert_worker as aw
    monkeypatch.setattr(aw, "calculate_alert_score", lambda *a, **k: (_ for _ in ()).throw(ValueError("boom")))
    targets = [{"id": 9, "domain": "x.com.br", "last_scan_score": 70, "contact_email": "a@x.com.br"}]
    kept, _ = _run(w._apply_alert_scoring(targets))
    assert [t["id"] for t in kept] == [9]   # mantido apesar do erro


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
