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

    async def get_site_profile(self, tid):
        return {"public_visible": True}

    async def global_avg_score(self):
        return {"avg_score": 64, "count": 8000}

    # users (JWT)
    async def email_has_verified_scan(self, email):
        return True

    async def create_user(self, email, password_hash, name=None, role="owner"):
        email = email.lower().strip()
        if email in self.users:
            return None
        u = {"id": self.next_id, "email": email, "name": name, "plan": "free",
             "max_sites": 1, "is_active": True, "role": role, "password_hash": password_hash}
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

    async def _fake_safe_scan(url, full=True, ingest_source=None, scanned_by_email=None):
        return _fake_report()
    monkeypatch.setattr(m, "_safe_scan", _fake_safe_scan)
    # rate limit anônimo cai no fallback in-memory — zera entre testes
    m._scan_anon_hour.clear()
    m._scan_anon_day.clear()
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


def test_filter_anonymous_is_preview_only():
    out = m._filter_scan_result(_full_fixture(), "anonymous")
    assert out["access_level"] == "anonymous"
    assert out["score"] == 72 and out["semaphore"] == "amarelo"
    assert out["benchmark_locked"] is True and out["checks_locked"] is True
    assert len(out["risks_preview"]) == 1 and out["risks_total"] == 3
    assert [c["name"] for c in out["categories_preview"]] == ["Transporte & TLS"]
    assert "pass_count" not in out["categories_preview"][0]  # barras: só ratio, sem números
    # NUNCA vaza checks/evidência/benchmark/privacidade
    blob = str(out)
    assert "SEGREDO-EVID" not in blob and "checks" not in out
    assert "benchmark" not in out and "privacy_indicators" not in out


def test_filter_unconfirmed_is_partial_no_evidence():
    out = m._filter_scan_result(_full_fixture(), "unconfirmed")
    assert out["access_level"] == "unconfirmed"
    assert out["benchmark"] == {"avg_score": 64, "count": 8000}
    assert len(out["risks_preview"]) == 2 and out["risks_total"] == 3
    assert out["categories"][0]["pass_count"] == 1  # categorias com números
    assert out["checks_names_only"] is True and out["pdf_locked"] is True
    # checks só nome/status — SEM evidência/impacto
    assert "SEGREDO-EVID" not in str(out)
    assert all("evidence" not in c and "impact" not in c for c in out["checks"])


def test_filter_confirmed_sees_everything():
    out = m._filter_scan_result(_full_fixture(), "confirmed")
    assert out["access_level"] == "confirmed"
    assert out["pdf_available"] is True
    assert out["privacy_indicators"]["score"] == 3
    assert out["risk_summary"]["risks"] and out["risks_total"] == 3
    # evidência disponível no nível confirmado
    assert any(c.get("evidence") == "SEGREDO-EVID" for c in out["checks"])


def test_filter_alert_session_same_as_confirmed():
    out = m._filter_scan_result(_full_fixture(), "alert_session")
    assert out["pdf_available"] is True and "checks" in out
    assert any(c.get("evidence") == "SEGREDO-EVID" for c in out["checks"])


# --------------------------------------------------------------------------- #
# 2. Endpoint GET /scan/result
# --------------------------------------------------------------------------- #

def test_scan_result_anonymous_no_email_no_leak(client):
    r = client.get("/scan/result?url=https://x.com.br")
    assert r.status_code == 200
    j = r.json()
    assert j["access_level"] == "anonymous"
    assert j["score"] == 72 and j["semaphore"] == "amarelo"
    assert j["checks_locked"] is True and "categories_preview" in j
    # anti-vazamento: nenhuma evidência ("ev-check_...") nem lista de checks completa
    assert "ev-check_" not in str(j) and "checks" not in j


def test_scan_result_confirmed_gets_full(client):
    u = _signup(client)
    r = client.get("/scan/result?url=https://x.com.br", headers=_bearer(u))
    assert r.status_code == 200
    j = r.json()
    assert j["access_level"] == "confirmed"
    assert j["pdf_available"] is True and j["report_urls"]["executive"].startswith("/report/executive")
    assert isinstance(j["checks"], list) and len(j["checks"]) == 48
    assert any("ev-check_" in (c.get("evidence") or "") for c in j["checks"])  # evidência presente


def test_scan_result_unconfirmed_user(client, store):
    u = _signup(client, email="novo@x.com.br")
    store.by_id[u["id"]]["email_confirmed"] = False  # conta não confirmada (Bloco 2)
    r = client.get("/scan/result?url=https://x.com.br", headers=_bearer(u))
    j = r.json()
    assert j["access_level"] == "unconfirmed"
    assert j["pdf_locked"] is True and j["checks_names_only"] is True
    assert "ev-check_" not in str(j)


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
