"""KL-163 (Prompt 1/2) — PDF de um run do Security Gate.

Cobre: o endpoint `GET /gate/runs/{id}/report` (auth API key OU sessão; 401/403/404; KYC gate) e o
builder/template PURO (conteúdo do PDF via HTML) + `mask_cpf`. A renderização WeasyPrint é
monkeypatchada no endpoint (offline/rápido); um teste dedicado renderiza um PDF real (`%PDF-`).
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

import api.main as m
from api import gate as g
from api import auth_users
from api.validators import mask_cpf
from reporter import gate_run_report as grr

SECRET = "k" * 64
VALID_CPF = "529.982.247-25"


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _now():
    return datetime.now(timezone.utc)


_FREE = {"id": 1, "name": "Free", "slug": "free", "scans_per_day": 5, "max_domains": 1,
         "checks_allowed": ["headers", "ssl", "exposure", "https_redirect"], "scan_third_party": False}
_PLANS = {1: _FREE}

_RESULTS = [
    {"check": "content_security_policy", "category": "headers", "path": "/", "status": "fail",
     "severity": "high", "detail": "Adicione o header Content-Security-Policy com política restritiva."},
    {"check": "x_content_type_options", "category": "headers", "path": "/", "status": "pass",
     "severity": "low", "detail": "nosniff presente"},
    {"check": "env_exposed", "category": "exposure", "path": "/.env", "status": "fail",
     "severity": "critical", "detail": "/.env acessível (HTTP 200)"},
    {"check": "ssl_valid", "category": "ssl", "path": "/", "status": "pass",
     "severity": "medium", "detail": "certificado válido"},
]


class FakeStore:
    def __init__(self):
        # 10 = dono do run, KYC completo; 20 = outra conta (KYC completo); 30 = sem KYC.
        self.users = {
            10: {"id": 10, "email": "dev@acme.com", "is_active": True, "account_type": "developer",
                 "gate_plan_id": 1, "gate_trial_ends_at": None, "email_confirmed": True,
                 "kyc_completed": True, "kyc_completed_at": _now(), "suspended": False,
                 "cpf": VALID_CPF, "address": "Rua Um, 100", "phone": "11999999999",
                 "phone_verified": True},
            20: {"id": 20, "email": "other@acme.com", "is_active": True, "account_type": "developer",
                 "gate_plan_id": 1, "gate_trial_ends_at": None, "email_confirmed": True,
                 "kyc_completed": True, "kyc_completed_at": _now(), "suspended": False,
                 "cpf": "111.444.777-35", "address": "Rua Dois, 200", "phone": "11988888888",
                 "phone_verified": True},
            30: {"id": 30, "email": "nokyc@acme.com", "is_active": True, "account_type": "developer",
                 "gate_plan_id": 1, "gate_trial_ends_at": None, "email_confirmed": True,
                 "kyc_completed": False, "kyc_completed_at": None, "suspended": False,
                 "cpf": None, "address": None, "phone": None, "phone_verified": False},
        }
        self.keys = []
        self.audits = []
        # 1 run pertencente à conta 10.
        self.runs = {500: {"id": 500, "project_id": None, "account_id": 10,
                           "url": "https://example.com.br", "score": 80, "passed": False,
                           "fail_on": "critical", "duration_ms": 1200, "results": _RESULTS,
                           "checks_run": ["headers", "ssl", "exposure"], "checks_blocked": [],
                           "metadata": {}, "created_at": "2026-08-10T17:32:00Z"}}

    async def get_user_by_id(self, uid):
        return self.users.get(int(uid))

    async def get_account_gate_fields(self, account_id):
        u = self.users.get(int(account_id))
        if not u:
            return None
        return {k: u.get(k) for k in ("id", "email", "account_type", "gate_plan_id",
                                      "gate_trial_ends_at", "email_confirmed", "kyc_completed",
                                      "kyc_completed_at", "suspended", "cpf", "address", "phone",
                                      "phone_verified")}

    async def get_gate_plan(self, pid):
        return dict(_PLANS[pid]) if pid in _PLANS else None

    async def get_gate_plan_by_slug(self, slug):
        return next((dict(p) for p in _PLANS.values() if p["slug"] == slug), None)

    async def get_gate_run(self, run_id, account_id=None):
        r = self.runs.get(int(run_id))
        if r is None:
            return None
        if account_id is not None and r["account_id"] != account_id:
            return None
        return dict(r)

    # API key auth
    async def get_gate_api_key_by_hash(self, key_hash):
        return next((dict(k) for k in self.keys if k["key_hash"] == key_hash), None)

    async def touch_gate_api_key(self, key_id):
        pass

    async def insert_gate_audit(self, account_id, action, **kw):
        self.audits.append({"account_id": account_id, "action": action, **kw})


def _register_key(store, full_key, account_id=10):
    store.keys.append({"id": len(store.keys) + 1, "account_id": account_id, "key_prefix": full_key[:8],
                       "key_hash": g._hash_key(full_key), "name": "d", "is_active": True,
                       "created_at": _now(), "last_used_at": None, "grace_expires_at": None})
    return full_key


def _cookie(user):
    return {auth_users.USER_COOKIE: auth_users.create_user_token(user)}


@pytest.fixture
def store(monkeypatch):
    s = FakeStore()
    monkeypatch.setenv("JWT_SECRET", SECRET)
    monkeypatch.setattr(m, "get_target_store", lambda: s)
    monkeypatch.setattr(g, "get_target_store", lambda: s)
    import discovery.store as ds
    monkeypatch.setattr(ds, "get_target_store", lambda: s)
    monkeypatch.setattr(m, "_cache", None)
    monkeypatch.setattr(g, "_scan_redis", lambda: None)
    # Não roda WeasyPrint nos testes de endpoint (offline/rápido).
    async def _fake_pdf(context):
        return b"%PDF-FAKE"
    monkeypatch.setattr(grr, "generate_gate_run_report_pdf", _fake_pdf)
    return s


@pytest.fixture
def client(store):
    return TestClient(m.app, raise_server_exceptions=False)


# --------------------------------------------------------------------------- #
# Endpoint
# --------------------------------------------------------------------------- #

def test_report_with_session_ok(client, store):
    r = client.get("/gate/runs/500/report", cookies=_cookie(store.users[10]))
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/pdf"
    cd = r.headers["content-disposition"]
    assert cd.startswith("attachment;")
    assert "klarim-gate-example.com.br-2026-08-10.pdf" in cd
    assert r.content == b"%PDF-FAKE"
    # audit registrado
    assert any(a["action"] == "run_report" for a in store.audits)


def test_report_with_api_key_ok(client, store):
    key = _register_key(store, "KLM_" + "a" * 32, account_id=10)
    r = client.get("/gate/runs/500/report", headers={"X-API-Key": key})
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/pdf"


def test_report_no_auth_401(client, store):
    r = client.get("/gate/runs/500/report")
    assert r.status_code == 401


def test_report_other_account_403(client, store):
    # Conta 20 tenta baixar o run da conta 10 → 403 (não 404: o run existe).
    r = client.get("/gate/runs/500/report", cookies=_cookie(store.users[20]))
    assert r.status_code == 403


def test_report_no_kyc_403(client, store):
    # Run da conta 30 (sem KYC).
    store.runs[600] = dict(store.runs[500], id=600, account_id=30)
    r = client.get("/gate/runs/600/report", cookies=_cookie(store.users[30]))
    assert r.status_code == 403
    assert "cadastro" in r.json()["detail"].lower()


def test_report_run_not_found_404(client, store):
    r = client.get("/gate/runs/9999/report", cookies=_cookie(store.users[10]))
    assert r.status_code == 404


# --------------------------------------------------------------------------- #
# Builder / template (PURO) — conteúdo do PDF via HTML
# --------------------------------------------------------------------------- #

def _ctx(run=None, **kw):
    run = run or {"url": "https://example.com.br", "score": 80, "passed": False,
                  "fail_on": "critical", "created_at": "2026-08-10T17:32:00Z", "results": _RESULTS}
    return grr.build_gate_run_context(run, cpf_masked=kw.get("cpf", "***.***.247-25"),
                                      plan_name=kw.get("plan", "Free"),
                                      generated_at=kw.get("generated_at", "11/08/2026 às 10:00"))


def test_html_contains_domain():
    html = grr.build_gate_run_report_html(_ctx())
    assert "example.com.br" in html
    assert "10/08/2026 às 14:32" in html   # created_at 17:32Z → 14:32 Brasília
    assert "80/100" in html
    assert "***.***.247-25" in html         # CPF SEMPRE mascarado


def test_html_contains_checks_and_recommendations():
    html = grr.build_gate_run_report_html(_ctx())
    assert "Content Security Policy" in html          # nome humanizado do check
    assert "Adicione o header Content-Security-Policy" in html   # recomendação (detail) do FAIL
    assert "Env Exposed" in html
    assert "/.env acessível" in html
    # categorias
    assert "Headers" in html and "Exposure" in html
    # resumo com contagem de falhas por severidade
    assert "crítica" in html and "alta" in html


def test_html_score_100_success_message():
    run = {"url": "https://ok.com", "score": 100, "passed": True, "fail_on": "critical",
           "created_at": "2026-08-10T12:00:00Z",
           "results": [{"check": "ssl_valid", "category": "ssl", "path": "/", "status": "pass",
                        "severity": "low", "detail": "ok"}]}
    ctx = grr.build_gate_run_context(run, cpf_masked=None, plan_name="Team",
                                     generated_at="x")
    assert ctx["no_findings"] is True
    assert ctx["cpf_masked"] is None
    html = grr.build_gate_run_report_html(ctx)
    assert "Nenhum problema encontrado" in html
    assert "Desenvolvedor" not in html   # sem CPF → sem a linha do desenvolvedor


def test_real_pdf_render():
    """Renderização real (WeasyPrint) — o endpoint monkeypatcha, mas o gerador precisa funcionar."""
    pdf = _run(grr.generate_gate_run_report_pdf(_ctx()))
    assert pdf[:5] == b"%PDF-"
    assert len(pdf) > 1000


# --------------------------------------------------------------------------- #
# mask_cpf
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("raw,expected", [
    ("529.982.247-25", "***.***.247-25"),
    ("52998224725", "***.***.247-25"),
    ("111.444.777-35", "***.***.777-35"),
    ("", "***.***.***-**"),
    ("123", "***.***.***-**"),
    (None, "***.***.***-**"),
])
def test_mask_cpf(raw, expected):
    assert mask_cpf(raw) == expected
