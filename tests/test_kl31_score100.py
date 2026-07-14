"""Testes do bônus de score 100 (KL-31): e-mail condicional, token de bônus,
crédito de scan completo e autorização no /scan/summary. Offline."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import api.main as m
from notifier.email_client import KlarimMailer
from discovery.alert_worker import bonus_scan_token, _is_score100


# --- e-mail condicional ---------------------------------------------------- #

def test_alert_score100_template(monkeypatch):
    # Freemium (fix): score 100 → assunto de parabéns (inalterado) + CTA de conta.
    monkeypatch.setenv("JWT_SECRET", "x" * 40)
    mailer = KlarimMailer("re_fake")
    p = mailer._alert_params("d@e.com", "https://empresa.com.br", 100, "verde", 0, {},
                             bonus_token="tok.sig")
    assert "parabéns" in p["subject"] and "nota máxima" in p["subject"]
    assert "Criar conta e monitorar" in p["html"] and "/cadastrar" in p["html"]
    assert "nota máxima" in p["html"].lower()
    assert "R$" not in p["html"]  # nunca menciona preço no fluxo de score 100


def test_alert_normal_template_cta_freemium():
    # Freemium (fix): alerta normal → CTA "Criar conta e monitorar" → /cadastrar.
    mailer = KlarimMailer("re_fake")
    p = mailer._alert_params("d@e.com", "https://x.com.br", 72, "amarelo", 3, {})
    assert "resultado da avaliação" in p["subject"]  # assunto inalterado
    assert "Criar conta e monitorar" in p["html"] and "/cadastrar" in p["html"]
    assert "R$" not in p["html"]


def test_is_score100():
    assert _is_score100(100, "verde") is True
    assert _is_score100(100, "VERDE") is True
    assert _is_score100(100, "amarelo") is False
    assert _is_score100(96, "verde") is False


def test_bonus_token_verifies_in_api(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "x" * 40)
    tok = bonus_scan_token("a@b.com", "https://x.com.br")
    payload = m._verify_scan_token(tok)
    assert payload and payload["email"] == "a@b.com"
    assert payload["bonus"] is True and payload["full"] is False


# --- /scan/summary com bônus ----------------------------------------------- #

class _FakeScore:
    def __init__(self, s, failed=0):
        self.score, self.failed, self.passed, self.inconclusive = s, failed, 29 - failed, 0
        self.semaphore = "verde" if s >= 90 and failed == 0 else "amarelo"
        self.grade_icon = "🟢"
        self.fails_by_severity: dict = {}


class _FakeReport:
    def __init__(self, s, results_n=29, failed=0):
        self.url = "https://x.com.br"
        self.results = []
        self.score = _FakeScore(s, failed)


class _CreditStore:
    def __init__(self, consume_ok):
        self._consume_ok = consume_ok
        self.consumed = []

    async def consume_full_scan_credit(self, email, url):
        self.consumed.append((email, url))
        return self._consume_ok


def _client(monkeypatch):
    return TestClient(m.app, raise_server_exceptions=False)


def test_summary_use_bonus_runs_full(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "x" * 40)
    store = _CreditStore(consume_ok=True)
    monkeypatch.setattr(m, "get_target_store", lambda: store)

    captured = {}

    async def fake_safe_scan(url, full=True, ingest_source=None, scanned_by_email=None):
        captured["full"] = full
        return _FakeReport(100)
    monkeypatch.setattr(m, "_safe_scan", fake_safe_scan)

    async def fake_extras(url, email, charge_id, scan_token):
        return {}
    monkeypatch.setattr(m, "_full_extras", fake_extras)

    tok = m._make_scan_token("a@b.com.br", "https://x.com.br", full=False, bonus=True)
    c = _client(monkeypatch)
    r = c.get("/scan/summary?url=x.com.br&use_bonus=true", headers={"X-Scan-Token": tok})
    assert r.status_code == 200
    body = r.json()
    assert body["is_full"] is True and captured["full"] is True
    assert store.consumed == [("a@b.com.br", "https://x.com.br")]  # crédito consumido


def test_summary_use_bonus_without_credit_falls_back_basic(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "x" * 40)
    monkeypatch.setenv("PAYWALL_ENABLED", "true")  # gate KL-27/31: básico vs completo
    store = _CreditStore(consume_ok=False)  # sem crédito → não autoriza completo
    monkeypatch.setattr(m, "get_target_store", lambda: store)

    async def fake_safe_scan(url, full=True, ingest_source=None, scanned_by_email=None):
        fake_safe_scan.full = full
        return _FakeReport(100 if full else 100, results_n=15 if not full else 29)
    monkeypatch.setattr(m, "_safe_scan", fake_safe_scan)

    tok = m._make_scan_token("a@b.com.br", "https://x.com.br", full=False, bonus=True)
    c = _client(monkeypatch)
    r = c.get("/scan/summary?url=x.com.br&use_bonus=true", headers={"X-Scan-Token": tok})
    assert r.status_code == 200
    assert r.json()["is_full"] is False   # bônus já usado → básico (15)
    assert fake_safe_scan.full is False


def test_summary_bonus_token_without_use_bonus_is_basic(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "x" * 40)
    monkeypatch.setenv("PAYWALL_ENABLED", "true")  # gate KL-27/31: básico vs completo
    store = _CreditStore(consume_ok=True)
    monkeypatch.setattr(m, "get_target_store", lambda: store)

    async def fake_safe_scan(url, full=True, ingest_source=None, scanned_by_email=None):
        fake_safe_scan.full = full
        return _FakeReport(100)
    monkeypatch.setattr(m, "_safe_scan", fake_safe_scan)

    tok = m._make_scan_token("a@b.com.br", "https://x.com.br", full=False, bonus=True)
    c = _client(monkeypatch)
    # sem use_bonus (visualização inicial) → básico, NÃO consome o crédito
    r = c.get("/scan/summary?url=x.com.br", headers={"X-Scan-Token": tok})
    assert r.status_code == 200 and r.json()["is_full"] is False
    assert fake_safe_scan.full is False and store.consumed == []


def test_summary_open_paywall_is_full(monkeypatch):
    # KL-51 f2: paywall aberto (default) → um token BÁSICO já vê o resultado completo.
    monkeypatch.setenv("JWT_SECRET", "x" * 40)
    store = _CreditStore(consume_ok=True)
    monkeypatch.setattr(m, "get_target_store", lambda: store)

    async def fake_safe_scan(url, full=True, ingest_source=None, scanned_by_email=None):
        fake_safe_scan.full = full
        return _FakeReport(80)
    monkeypatch.setattr(m, "_safe_scan", fake_safe_scan)

    tok = m._make_scan_token("a@b.com.br", "https://x.com.br", full=False)
    c = _client(monkeypatch)
    r = c.get("/scan/summary?url=x.com.br", headers={"X-Scan-Token": tok})
    assert r.status_code == 200 and r.json()["is_full"] is True
    assert fake_safe_scan.full is True
