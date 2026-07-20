"""KL-82 — confiança progressiva (Slice 1): scan anônimo + resultado por nível de acesso.
Offline (TestClient + FakeStore, sem rede/DB).

Cobre:
  * `_check_category` / `_build_categories` — agregação por categoria (puro).
  * `_filter_scan_result` — anonymous vê preview; unconfirmed parcial (sem evidência);
    confirmed vê tudo. NUNCA vaza evidência/detalhe para os níveis baixos.
  * `GET /scan/result` — escaneia sem e-mail; anonymous não vaza evidência; conta logada
    (confirmed) recebe checks completos + PDF; rate limit anônimo 5/h → 429; logado ilimitado.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import api.main as m
from api import auth_users


# --------------------------------------------------------------------------- #
# Fixtures — report falso + store falso
# --------------------------------------------------------------------------- #

def _fake_report():
    metas = m.CHECK_META
    results = []
    for i, meta in enumerate(metas[:8]):
        cid = meta["check_id"]
        fail = i == 0  # o 1º check FALHA (crítico), o resto passa
        results.append(SimpleNamespace(
            check_id=cid, name=meta["name"],
            status="FAIL" if fail else "PASS",
            severity="CRITICA" if fail else "BAIXA",
            evidence=f"ev-{cid}" if fail else "",
            owasp="A02", cwe="CWE-319", lgpd="Art. 46"))
    score = SimpleNamespace(score=72, semaphore="amarelo", grade_icon="🟡",
                            failed=1, passed=7, inconclusive=0)
    return SimpleNamespace(url="https://x.com.br", started_at="", finished_at="2026-07-19T10:00:00Z",
                           duration_s=1.0, results=results, score=score,
                           privacy={"score": 3, "total": 8, "checks": [{"id": "c", "status": "FAIL"}]})


class FakeStore:
    def __init__(self):
        self.users = {}
        self.by_id = {}
        self.next_id = 1
        self.target = {"id": 1, "domain": "x.com.br", "url": "https://x.com.br",
                       "status": "descoberto", "sector": "outro"}

    async def get_target_by_url(self, url):
        return self.target

    async def get_recent_scan_checks(self, url, max_age_minutes=60):
        # Sem scan recente no banco por padrão → o fluxo cai no scan novo (from_cache=False).
        return None

    async def get_site_profile(self, tid):
        return {"public_visible": True}

    async def global_avg_score(self):
        return {"avg_score": 64, "count": 8000}

    # users (JWT)
    async def email_has_verified_scan(self, email):
        return True

    async def create_user(self, email, password_hash, name=None, role="owner",
                          email_confirmed=True, confirmation_source=None):
        email = email.lower().strip()
        if email in self.users:
            return None
        u = {"id": self.next_id, "email": email, "name": name, "plan": "free",
             "max_sites": 1, "is_active": True, "role": role, "password_hash": password_hash,
             "email_confirmed": email_confirmed}
        self.users[email] = u
        self.by_id[u["id"]] = u
        self.next_id += 1
        return {k: v for k, v in u.items() if k != "password_hash"}

    async def get_user_by_id(self, uid):
        u = self.by_id.get(int(uid))
        return {k: v for k, v in u.items() if k != "password_hash"} if u else None

    async def get_user_by_email(self, email, with_hash=False):
        u = self.users.get(email.lower().strip())
        if not u:
            return None
        return dict(u) if with_hash else {k: v for k, v in u.items() if k != "password_hash"}

    async def touch_user_login(self, uid):
        pass

    async def count_user_sites(self, uid):
        return 0

    async def set_lead_account(self, email, account_id):
        pass

    async def auto_link_technician_by_email(self, email, tuid):
        return 0


@pytest.fixture
def store(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "k" * 64)
    s = FakeStore()
    monkeypatch.setattr(m, "get_target_store", lambda: s)
    monkeypatch.setattr("discovery.store.get_target_store", lambda: s)
    monkeypatch.setattr(auth_users, "_secret", lambda: "k" * 64)
    monkeypatch.setattr(m, "_email_enabled", lambda: False)

    async def _fake_safe_scan(url, full=True, ingest_source=None, scanned_by_email=None, force=False):
        return _fake_report()
    monkeypatch.setattr(m, "_safe_scan", _fake_safe_scan)
    # rate limit anônimo cai no fallback in-memory — zera entre testes
    m._scan_anon_hour.clear()
    m._scan_anon_day.clear()
    m._signup_attempts.clear()
    m._signup_daily_attempts.clear()
    return s


@pytest.fixture
def client(store):
    return TestClient(m.app, raise_server_exceptions=False)


def _bearer(user):
    return {"Authorization": f"Bearer {auth_users.create_user_token(user)}"}


def _signup(client, email="dono@x.com.br", pw="segredo123"):
    return client.post("/account/signup", json={"email": email, "password": pw}).json()["user"]


# --------------------------------------------------------------------------- #
# 1. Helpers puros
# --------------------------------------------------------------------------- #

def test_check_category_maps_known_and_unknown():
    assert m._check_category("check_01_https") == "Transporte & TLS"
    assert m._check_category("check_29_safe_browsing") == "OSINT & Reputação"
    assert m._check_category("check_999_x") == "Outros"
    assert m._check_category(None) == "Outros"


def test_build_categories_counts_and_ratio():
    checks = [
        {"check_id": "check_01_https", "status": "FAIL", "severity": "CRITICA", "category": "Transporte & TLS"},
        {"check_id": "check_02_hsts", "status": "PASS", "severity": "BAIXA", "category": "Transporte & TLS"},
        {"check_id": "check_09_x", "status": "INCONCLUSO", "severity": "MEDIA", "category": "Conteúdo"},
    ]
    cats = {c["name"]: c for c in m._build_categories(checks)}
    tls = cats["Transporte & TLS"]
    assert tls["pass_count"] == 1 and tls["fail_count"] == 1 and tls["total"] == 2
    assert tls["pass_ratio"] == 0.5 and tls["has_high_fails"] is True
    # INCONCLUSO conta no total mas não no pass_ratio (denominador = pass+fail)
    cont = cats["Conteúdo"]
    assert cont["total"] == 1 and cont["pass_ratio"] == 1.0 and cont["has_high_fails"] is False


def _full_fixture():
    return {
        "url": "https://x.com.br", "domain": "x.com.br",
        "score": 72, "semaphore": "amarelo", "grade_icon": "🟡",
        "scan_date": "2026-07-19T10:00:00Z", "fail_count": 3, "total_checks": 48,
        "checks": [
            {"check_id": "check_01_https", "name": "HTTPS", "status": "FAIL",
             "category": "Transporte & TLS", "evidence": "SEGREDO-EVID", "impact": "grave"},
            {"check_id": "check_02_hsts", "name": "HSTS", "status": "PASS", "category": "Transporte & TLS"},
        ],
        "categories": [{"name": "Transporte & TLS", "pass_count": 1, "fail_count": 1,
                        "total": 2, "pass_ratio": 0.5, "has_high_fails": True}],
        "risk_summary": {"risks": [{"check_id": "a", "message": "r1"}, {"check_id": "b", "message": "r2"},
                                   {"check_id": "c", "message": "r3"}], "remaining_count": 0},
        "benchmark": {"avg_score": 64, "count": 8000},
        "privacy": {"score": 3, "total": 8, "checks": [{"id": "c"}]},
        "profile_domain": "x.com.br", "has_profile": True,
    }


def test_filter_anonymous_sees_risks_and_checks_without_evidence():
    # KL-89 correção — anônimo vê VALOR (benchmark + TODOS os riscos + categorias com números +
    # checks por nome/status), mas NUNCA evidência técnica nem LGPD.
    out = m._filter_scan_result(_full_fixture(), "anonymous")
    assert out["access_level"] == "anonymous"
    assert out["score"] == 72 and out["semaphore"] == "amarelo"
    assert out["benchmark"] == {"avg_score": 64, "count": 8000}
    assert len(out["risk_summary"]["risks"]) == 3 and out["risks_total"] == 3  # TODOS os riscos
    assert out["categories"][0]["pass_count"] == 1  # categorias com números (barras + accordion)
    assert out["checks_names_only"] is True and isinstance(out["checks"], list)
    # NUNCA vaza evidência técnica nem indicadores de privacidade/LGPD
    blob = str(out)
    assert "SEGREDO-EVID" not in blob and "privacy_indicators" not in out
    assert all("evidence" not in c and "impact" not in c for c in out["checks"])


def test_filter_unconfirmed_same_visibility_no_evidence():
    out = m._filter_scan_result(_full_fixture(), "unconfirmed")
    assert out["access_level"] == "unconfirmed"
    assert out["benchmark"] == {"avg_score": 64, "count": 8000}
    assert len(out["risk_summary"]["risks"]) == 3 and out["risks_total"] == 3  # TODOS os riscos
    assert out["categories"][0]["pass_count"] == 1
    assert out["checks_names_only"] is True
    # checks só nome/status — SEM evidência/impacto; LGPD travado
    assert "SEGREDO-EVID" not in str(out)
    assert all("evidence" not in c and "impact" not in c for c in out["checks"])
    assert "privacy_indicators" not in out


def test_filter_confirmed_sees_everything():
    out = m._filter_scan_result(_full_fixture(), "confirmed")
    assert out["access_level"] == "confirmed"
    assert out["pdf_available"] is True
    assert out["privacy_indicators"]["score"] == 3  # LGPD só p/ conta confirmada
    assert out["risk_summary"]["risks"] and out["risks_total"] == 3
    # evidência disponível no nível confirmado
    assert any(c.get("evidence") == "SEGREDO-EVID" for c in out["checks"])


def test_filter_alert_session_full_checks_but_no_lgpd():
    # O visitante do link do alerta vê o resultado COMPLETO (evidência + PDF), mas LGPD é travado
    # (não é conta — KL-89 correção Problema 2).
    out = m._filter_scan_result(_full_fixture(), "alert_session")
    assert out["pdf_available"] is True and "checks" in out
    assert any(c.get("evidence") == "SEGREDO-EVID" for c in out["checks"])
    assert "privacy_indicators" not in out


# --------------------------------------------------------------------------- #
# 2. Endpoint GET /scan/result
# --------------------------------------------------------------------------- #

def test_scan_result_anonymous_no_email_no_leak(client):
    r = client.get("/scan/result?url=https://x.com.br")
    assert r.status_code == 200
    j = r.json()
    assert j["access_level"] == "anonymous"
    assert j["score"] == 72 and j["semaphore"] == "amarelo"
    # KL-89: anônimo vê categorias + checks por nome (sem evidência) + todos os riscos; LGPD travado
    assert j["checks_names_only"] is True and isinstance(j["checks"], list)
    assert j["categories"] and j["risks_total"] >= 1
    # anti-vazamento: nenhuma evidência ("ev-check_...") nem indicadores de LGPD
    assert "ev-check_" not in str(j) and "privacy_indicators" not in j


def test_scan_result_confirmed_gets_full(client):
    u = _signup(client)
    r = client.get("/scan/result?url=https://x.com.br", headers=_bearer(u))
    assert r.status_code == 200
    j = r.json()
    assert j["access_level"] == "confirmed"
    assert j["pdf_available"] is True and j["report_urls"]["executive"].startswith("/report/executive")
    # KL-89 P0: `checks` traz SÓ os que rodaram (o fake tem 8) — sem pad de inconclusivo fantasma.
    assert isinstance(j["checks"], list) and len(j["checks"]) == 8
    assert j["total_checks"] == 8 and j["partial"] is True  # scan free/parcial sinalizado
    assert all(c["status"] in ("PASS", "FAIL", "INCONCLUSO") for c in j["checks"])  # nada "não rodado"
    assert any("ev-check_" in (c.get("evidence") or "") for c in j["checks"])  # evidência presente


def test_scan_result_unconfirmed_user(client, store):
    u = _signup(client, email="novo@x.com.br")
    store.by_id[u["id"]]["email_confirmed"] = False  # conta não confirmada (Bloco 2)
    r = client.get("/scan/result?url=https://x.com.br", headers=_bearer(u))
    j = r.json()
    assert j["access_level"] == "unconfirmed"
    assert j["checks_names_only"] is True
    assert "ev-check_" not in str(j) and "privacy_indicators" not in j


def test_scan_result_serves_recent_from_cache_no_rescan(client, store, monkeypatch):
    # KL-89 P0 — existe scan recente (< 24h) → carrega instantâneo, SEM re-escanear.
    called = {"scan": False}

    async def _no_scan(*a, **k):
        called["scan"] = True
        return _fake_report()
    monkeypatch.setattr(m, "_safe_scan", _no_scan)

    async def _recent(url, full=False, max_age_minutes=60):
        assert max_age_minutes == m._SCAN_RESULT_MAX_AGE_MIN  # janela de 24h
        return _fake_report()
    monkeypatch.setattr(m, "get_recent_only", _recent)

    j = client.get("/scan/result?url=https://x.com.br").json()
    assert j["from_cache"] is True
    assert called["scan"] is False  # NÃO re-escaneou


def test_scan_result_serves_free_tier_worker_scan_no_rescan(client, store, monkeypatch):
    # KL-89 P0 (fix do bug): o scan do worker de discovery é FREE (15 checks) e NÃO passa no
    # _tier_ok(full=True). O /scan/result deve cair no lookup free e servir esse scan mesmo assim
    # (instantâneo), em vez de re-escanear. Regressão do "link do alerta re-escaneava".
    called = {"scan": False}

    async def _no_scan(*a, **k):
        called["scan"] = True
        return _fake_report()
    monkeypatch.setattr(m, "_safe_scan", _no_scan)

    calls = []

    async def _recent(url, full=False, max_age_minutes=60):
        calls.append(full)
        return None if full else _fake_report()  # só existe o scan FREE (full=False)
    monkeypatch.setattr(m, "get_recent_only", _recent)

    j = client.get("/scan/result?url=https://x.com.br").json()
    assert j["from_cache"] is True
    assert called["scan"] is False       # NÃO re-escaneou
    assert calls == [True, False]        # tentou o full, caiu no free (o do worker/alerta)


def test_scan_result_refresh_forces_new_scan(client, store, monkeypatch):
    # KL-89 P0 — refresh=1 (botão "Atualizar análise") pula o cache e força scan novo.
    seen = {"force": None, "recent_called": False}

    async def _scan(url, full=True, ingest_source=None, scanned_by_email=None, force=False):
        seen["force"] = force
        return _fake_report()
    monkeypatch.setattr(m, "_safe_scan", _scan)

    async def _recent(*a, **k):
        seen["recent_called"] = True
        return _fake_report()
    monkeypatch.setattr(m, "get_recent_only", _recent)

    j = client.get("/scan/result?url=https://x.com.br&refresh=1").json()
    assert j["from_cache"] is False
    assert seen["recent_called"] is False  # não consultou o cache (refresh explícito)
    assert seen["force"] is True           # escaneou com force=True


def test_scan_result_cache_hit_skips_anon_rate_limit(client, store, monkeypatch):
    # KL-89 P0 — servir do cache NÃO consome a cota anônima (5/h). 8 hits do cache = 8x 200.
    async def _recent(*a, **k):
        return _fake_report()
    monkeypatch.setattr(m, "get_recent_only", _recent)
    for _ in range(8):
        r = client.get("/scan/result?url=https://x.com.br")
        assert r.status_code == 200 and r.json()["from_cache"] is True


def test_scan_result_anon_rate_limit_5_per_hour(client):
    for _ in range(5):
        assert client.get("/scan/result?url=https://x.com.br").status_code == 200
    r = client.get("/scan/result?url=https://x.com.br")
    assert r.status_code == 429
    assert "conta gratuita" in r.json()["detail"].lower()


def test_scan_result_authenticated_unlimited(client):
    u = _signup(client)
    # muito além do teto anônimo (5) — conta logada não tem rate limit
    for _ in range(8):
        assert client.get("/scan/result?url=https://x.com.br", headers=_bearer(u)).status_code == 200


# --------------------------------------------------------------------------- #
# 3. _client_ip — atrás do Cloudflare, o IP real vem em CF-Connecting-IP
# --------------------------------------------------------------------------- #

def test_client_ip_prefers_cf_connecting_ip():
    req = SimpleNamespace(headers={"cf-connecting-ip": "203.0.113.5", "x-real-ip": "172.16.0.1"},
                          client=SimpleNamespace(host="10.0.0.1"))
    assert m._client_ip(req) == "203.0.113.5"


def test_client_ip_falls_back_to_x_real_ip_then_peer():
    req = SimpleNamespace(headers={"x-real-ip": "172.16.0.1"}, client=SimpleNamespace(host="10.0.0.1"))
    assert m._client_ip(req) == "172.16.0.1"
    req2 = SimpleNamespace(headers={}, client=SimpleNamespace(host="10.0.0.1"))
    assert m._client_ip(req2) == "10.0.0.1"
