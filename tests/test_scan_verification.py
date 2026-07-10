"""Testes da verificação de e-mail (código 6 dígitos) do scan público (KL-25). Offline."""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

import api.main as m


# --- token de scan (HMAC) -------------------------------------------------- #

def test_scan_token_roundtrip(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "x" * 40)
    tok = m._make_scan_token("a@b.com.br", "https://x.com.br")
    p = m._verify_scan_token(tok)
    assert p and p["email"] == "a@b.com.br" and p["url"] == "https://x.com.br"


def test_scan_token_tamper_and_expiry(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "x" * 40)
    tok = m._make_scan_token("a@b.com.br", "https://x.com.br")
    assert m._verify_scan_token(tok + "z") is None            # assinatura adulterada
    assert m._verify_scan_token("lixo") is None
    # expirado
    raw = tok.rsplit(".", 1)[0]
    import base64, json, hmac, hashlib
    payload = json.loads(base64.urlsafe_b64decode(raw))
    payload["exp"] = int(time.time()) - 10
    raw2 = base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode()).decode()
    sig2 = hmac.new(("x" * 40).encode(), raw2.encode(), hashlib.sha256).hexdigest()[:32]
    assert m._verify_scan_token(f"{raw2}.{sig2}") is None


def test_norm_scan_url():
    assert m._norm_scan_url("www.EXEMPLO.com.br/") == "https://www.exemplo.com.br"
    assert m._norm_scan_url("https://X.com.br") == "https://x.com.br"
    assert m._norm_scan_url("  exemplo.com.br  ") == "https://exemplo.com.br"


# --- fakes ----------------------------------------------------------------- #

class FakeStore:
    def __init__(self, credit=None, verify_ok=True, recent=None):
        self._credit = credit
        self._verify_ok = verify_ok
        self._recent = recent
        self.verifications = []
        self.free_scans = []

    async def get_scan_credit(self, email):
        return self._credit

    async def create_scan_verification(self, email, code, url, ttl_minutes=10, ip_address=None):
        self.verifications.append({"email": email, "code": code, "url": url, "ip": ip_address})

    async def verify_scan_code(self, email, code, url):
        return self._verify_ok

    async def record_free_scan(self, email, url):
        self.free_scans.append((email, url))

    async def get_recent_scan_checks(self, url, mins=60):
        return self._recent


class FakeMailer:
    def __init__(self):
        self.sent = []

    async def send_verification_code(self, to_email, code, domain):
        self.sent.append({"to": to_email, "code": code, "domain": domain})
        return {"email_id": "vc_1"}


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "x" * 40)
    monkeypatch.setenv("RESEND_API_KEY", "re_test")
    mailer = FakeMailer()
    monkeypatch.setattr(m, "_mailer", lambda: mailer)
    # zera os rate limits in-memory entre testes
    m._code_email_hits.clear(); m._code_ip_hits.clear(); m._verify_hits.clear()
    return {"mailer": mailer}


def _client(monkeypatch, store):
    monkeypatch.setattr(m, "get_target_store", lambda: store)
    return TestClient(m.app, raise_server_exceptions=False)


# --- request-code ---------------------------------------------------------- #

def test_request_code_sends(env, monkeypatch):
    store = FakeStore(credit=None)  # e-mail novo
    c = _client(monkeypatch, store)
    r = c.post("/scan/request-code", json={"email": "a@b.com.br", "url": "exemplo.com.br"})
    assert r.status_code == 200 and r.json()["status"] == "code_sent"
    assert len(env["mailer"].sent) == 1 and len(env["mailer"].sent[0]["code"]) == 6
    assert store.verifications[0]["url"] == "https://exemplo.com.br"


def test_request_code_cleans_url_encoded_email(env, monkeypatch):
    store = FakeStore(credit=None)
    c = _client(monkeypatch, store)
    r = c.post("/scan/request-code", json={"email": "%20a@b.com.br", "url": "x.com.br"})
    assert r.status_code == 200
    assert env["mailer"].sent[0]["to"] == "a@b.com.br"  # limpo


def test_request_code_limit_reached(env, monkeypatch):
    store = FakeStore(credit={"free_scans_used": 1, "first_scan_url": "https://outro.com.br"})
    c = _client(monkeypatch, store)
    r = c.post("/scan/request-code", json={"email": "a@b.com.br", "url": "novo.com.br"})
    assert r.json()["status"] == "limit_reached" and not env["mailer"].sent


def test_request_code_already_scanned_same_url(env, monkeypatch):
    store = FakeStore(credit={"free_scans_used": 1, "first_scan_url": "https://x.com.br"})
    c = _client(monkeypatch, store)
    r = c.post("/scan/request-code", json={"email": "a@b.com.br", "url": "x.com.br"})
    assert r.json()["status"] == "already_scanned"


def test_request_code_rate_limit(env, monkeypatch):
    store = FakeStore(credit=None)
    c = _client(monkeypatch, store)
    codes = [c.post("/scan/request-code", json={"email": "a@b.com.br", "url": f"s{i}.com.br"},
                    headers={"X-Real-IP": "1.1.1.1"}).status_code for i in range(4)]
    assert codes[:3] == [200, 200, 200] and codes[3] == 429


def test_request_code_invalid_email(env, monkeypatch):
    c = _client(monkeypatch, FakeStore())
    assert c.post("/scan/request-code", json={"email": "nao-eh", "url": "x.com.br"}).status_code == 422


# --- verify-code ----------------------------------------------------------- #

def test_verify_code_ok_returns_token(env, monkeypatch):
    store = FakeStore(verify_ok=True)
    c = _client(monkeypatch, store)
    r = c.post("/scan/verify-code", json={"email": "a@b.com.br", "code": "123456", "url": "x.com.br"})
    body = r.json()
    assert body["status"] == "verified" and body["scan_token"]
    assert store.free_scans == [("a@b.com.br", "https://x.com.br")]
    p = m._verify_scan_token(body["scan_token"])
    assert p["email"] == "a@b.com.br" and p["url"] == "https://x.com.br"


def test_verify_code_invalid(env, monkeypatch):
    store = FakeStore(verify_ok=False)
    c = _client(monkeypatch, store)
    r = c.post("/scan/verify-code", json={"email": "a@b.com.br", "code": "000000", "url": "x.com.br"})
    assert r.json()["status"] == "invalid" and store.free_scans == []


def test_verify_code_rate_limit(env, monkeypatch):
    store = FakeStore(verify_ok=False)
    c = _client(monkeypatch, store)
    codes = [c.post("/scan/verify-code", json={"email": "a@b.com.br", "code": "111111", "url": "x.com.br"}).status_code
             for _ in range(6)]
    assert codes[5] == 429


# --- modo demo (Fix pós-KL-27) --------------------------------------------- #

def test_demo_request_code_no_email(env, monkeypatch):
    monkeypatch.setenv("DEMO_EMAIL", "demo@klarim.net")
    store = FakeStore(credit=None)
    c = _client(monkeypatch, store)
    r = c.post("/scan/request-code", json={"email": "demo@klarim.net", "url": "x.com.br"})
    b = r.json()
    assert b["status"] == "code_sent" and b.get("demo") is True
    assert env["mailer"].sent == []  # nenhum e-mail enviado no modo demo


def test_demo_verify_code_fixed(env, monkeypatch):
    monkeypatch.setenv("DEMO_EMAIL", "demo@klarim.net")
    store = FakeStore(verify_ok=False)  # o store não valida — o demo aceita 000000
    c = _client(monkeypatch, store)
    r = c.post("/scan/verify-code", json={"email": "demo@klarim.net", "code": "000000", "url": "x.com.br"})
    b = r.json()
    assert b["status"] == "verified" and b["scan_token"] and b.get("demo") is True
    assert store.free_scans == []  # demo NÃO consome crédito (repetível)


def test_demo_verify_code_wrong(env, monkeypatch):
    monkeypatch.setenv("DEMO_EMAIL", "demo@klarim.net")
    c = _client(monkeypatch, FakeStore())
    r = c.post("/scan/verify-code", json={"email": "demo@klarim.net", "code": "111111", "url": "x.com.br"})
    assert r.json()["status"] == "invalid"


# --- check-credit ---------------------------------------------------------- #

def test_check_credit(env, monkeypatch):
    store = FakeStore(credit={"free_scans_used": 1, "first_scan_url": "https://x.com.br"})
    c = _client(monkeypatch, store)
    r = c.post("/scan/check-credit", json={"email": "a@b.com.br", "url": "x.com.br"})
    b = r.json()
    assert b["has_free_scan"] is False and b["same_url_scanned"] is True and b["free_scans_used"] == 1


# --- scan/summary gating --------------------------------------------------- #

class _FakeScore:
    score, semaphore, grade_icon = 100, "verde", "🟢"
    failed, passed, inconclusive = 0, 15, 1
    fails_by_severity: dict = {}


class _FakeReport:
    url = "https://x.com.br"
    results: list = []
    score = _FakeScore()


def test_scan_summary_no_token_auth_required(env, monkeypatch):
    store = FakeStore(recent=None)  # nada em cache/banco
    c = _client(monkeypatch, store)
    r = c.get("/scan/summary?url=x.com.br")
    assert r.status_code == 200 and r.json()["status"] == "auth_required"


def test_scan_summary_with_token_scans(env, monkeypatch):
    store = FakeStore()
    c = _client(monkeypatch, store)

    async def fake_safe_scan(url, full=True, ingest_source=None, scanned_by_email=None):
        fake_safe_scan.email = scanned_by_email
        return _FakeReport()
    monkeypatch.setattr(m, "_safe_scan", fake_safe_scan)

    tok = m._make_scan_token("a@b.com.br", "https://x.com.br")
    r = c.get("/scan/summary?url=x.com.br", headers={"X-Scan-Token": tok})
    assert r.status_code == 200 and r.json()["score"] == 100
    assert fake_safe_scan.email == "a@b.com.br"  # scanned_by_email propagado


def test_scan_summary_no_token_returns_cached(env, monkeypatch):
    # há resultado recente no banco -> retorna sem exigir token
    store = FakeStore(recent={"results": []})
    c = _client(monkeypatch, store)

    async def fake_recent(url, full=False):
        return _FakeReport()
    monkeypatch.setattr(m, "get_recent_only", fake_recent)
    r = c.get("/scan/summary?url=x.com.br")
    assert r.status_code == 200 and r.json()["score"] == 100
